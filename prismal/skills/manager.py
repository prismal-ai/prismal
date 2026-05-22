"""Skills manager — discovery, activation, and tool aggregation.

:class:`SkillsManager` orchestrates the three-tier skills system:

* ``available/`` — installed skills that can be activated
* ``active/``    — symlinks to enabled skills (managed by this module)
* ``custom/``    — AI-generated skills that require human review before use

Example::

    from pathlib import Path
    from prismal.skills.manager import SkillsManager

    manager = SkillsManager()
    print(manager.list_skills())
    await manager.activate("web_search", confirm=True)
    tools = manager.get_active_tools()
    await manager.deactivate("web_search")
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from prismal.core.exceptions import SkillLoadError, SkillValidationError
from prismal.core.logging import get_logger
from prismal.skills.base import _find_skill_md

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from prismal.skills.base import BaseSkill

logger = get_logger("prismal.skills.manager")

SkillStatus = Literal["active", "available", "custom", "external", "error"]


@dataclass
class SkillInfo:
    """Summary information about a discovered skill."""

    name: str
    status: SkillStatus
    version: str
    description: str
    author: str
    safe_to_auto_activate: bool
    requires_permissions: list[str] = field(default_factory=list)
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Zip-archive helpers (module-level, used by SkillsManager.install_from_zip)
# ---------------------------------------------------------------------------


def _zip_detect_prefix(names: list[str]) -> str | None:
    """Return the top-level prefix where ``skill.md`` lives inside a zip.

    Comparison is case-insensitive so both ``skill.md`` and ``SKILL.md`` are
    recognised.

    Args:
        names: List of member paths returned by :meth:`zipfile.ZipFile.namelist`.

    Returns:
        ``""`` if ``skill.md`` is at the archive root, ``"dirname/"`` if it is
        inside a single top-level directory, or ``None`` if not found.
    """
    names_lower = {n.lower(): n for n in names}
    if "skill.md" in names_lower:
        return ""
    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    for top in sorted(top_dirs):
        if f"{top}/skill.md" in names_lower:
            return f"{top}/"
    return None


def _zip_skill_name(prefix: str, zip_path: Path) -> str:
    """Derive a snake_case skill name from the zip prefix or filename.

    Args:
        prefix: Top-level prefix from :func:`_zip_detect_prefix`
            (``""`` when ``skill.md`` is at the archive root).
        zip_path: Path to the zip file, used as fallback when prefix is empty.

    Returns:
        Snake-case skill name string.
    """
    if prefix:
        return prefix.rstrip("/").replace("-", "_")
    return zip_path.stem.replace("-", "_")


class SkillsManager:
    """Manages skill discovery, activation, deactivation, and tool aggregation.

    Args:
        skills_root: Root of the skills tree.  Defaults to the ``skills/``
            package directory next to this module.
    """

    def __init__(
        self,
        skills_root: Path | None = None,
        external_dirs: list[Path] | None = None,
    ) -> None:
        """Initialise the manager.

        Args:
            skills_root: Override for the default skills root directory.
                When ``None`` (the default), the package ``skills/`` directory
                is used and ``external_skills_dirs`` from Settings is loaded
                automatically.  When an explicit path is supplied (e.g. in
                tests) external dirs default to empty unless also provided.
            external_dirs: Additional directories to scan for skills.  When
                ``None`` and *skills_root* was not overridden, directories are
                loaded from ``settings.external_skills_dirs`` automatically.
                Pass an empty list to suppress external-dir loading entirely.
        """
        use_default_root = skills_root is None
        if skills_root is None:
            skills_root = Path(__file__).parent
        self._root = skills_root
        self._available = self._root / "available"
        self._active = self._root / "active"
        self._custom = self._root / "custom"
        self._active_skills: dict[str, BaseSkill] = {}

        if external_dirs is not None:
            self._external_dirs: list[Path] = [
                Path(d).expanduser().resolve() for d in external_dirs
            ]
        elif use_default_root:
            from prismal.core.config import get_settings

            self._external_dirs = [
                Path(d).expanduser().resolve() for d in get_settings().external_skills_dirs if d
            ]
        else:
            # Explicit skills_root provided (e.g. tests) — no external dirs
            self._external_dirs = []

        self._restore_active_skills()

    # ── Discovery ────────────────────────────────────────────────────────────

    def list_skills(self, status: SkillStatus | None = None) -> list[SkillInfo]:
        """Return all discovered skills, optionally filtered by status.

        Args:
            status: If provided, only skills with this status are returned.

        Returns:
            List of :class:`SkillInfo` sorted by name.
        """
        infos: list[SkillInfo] = []
        active_names: set[str] = set()

        # Active skills (already loaded in memory)
        for name, skill in sorted(self._active_skills.items()):
            meta = skill.metadata
            infos.append(
                SkillInfo(
                    name=meta.name,
                    status="active",
                    version=meta.version,
                    description=meta.description,
                    author=meta.author,
                    safe_to_auto_activate=meta.safe_to_auto_activate,
                    requires_permissions=list(meta.requires_permissions),
                )
            )
            active_names.add(name)

        # Available (not yet active)
        infos.extend(self._scan_dir(self._available, "available", active_names))
        # Custom (not yet active)
        infos.extend(self._scan_dir(self._custom, "custom", active_names))
        # External directories (not yet active)
        for ext_dir in self._external_dirs:
            infos.extend(self._scan_dir(ext_dir, "external", active_names))

        if status is not None:
            infos = [i for i in infos if i.status == status]
        return sorted(infos, key=lambda i: i.name)

    # ── Activation ───────────────────────────────────────────────────────────

    async def activate(self, name: str, *, confirm: bool = False) -> None:
        """Activate a skill by name.

        Loads the module, calls ``validate()``, then ``initialize()``, and
        creates a relative symlink in ``active/``.

        Args:
            name: Skill directory name (in ``available/`` or ``custom/``).
            confirm: Required when ``safe_to_auto_activate=False`` or
                ``requires_permissions`` is non-empty.

        Raises:
            SkillLoadError: Skill directory or ``skill.py`` not found.
            SkillValidationError: Confirm missing, custom gate failed,
                or ``validate()`` returned False.
        """
        if name in self._active_skills:
            logger.info("skill_already_active", skill=name)
            return

        skill_dir, is_custom = self._find_skill_dir(name)
        skill_py = skill_dir / "skill.py"
        if not skill_py.exists():
            if _find_skill_md(skill_dir) is not None:
                from prismal.skills.base import generate_skill_py

                generate_skill_py(skill_dir)
            else:
                raise SkillLoadError(name, f"skill.py not found in {skill_dir}")

        # Custom skill human-review gate (AC-006-8)
        if is_custom and not (skill_dir / "validated_by_human.txt").exists():
            raise SkillValidationError(
                name,
                [
                    "Custom skills require 'validated_by_human.txt' before "
                    "activation. Review the generated code and rename "
                    "'human_review_required.txt' to 'validated_by_human.txt'."
                ],
            )

        try:
            skill_cls = self._load_skill_class(skill_py)
            skill = skill_cls()
        except SkillLoadError:
            raise
        except Exception as exc:
            raise SkillLoadError(name, str(exc)) from exc

        meta = skill.metadata

        # Confirmation gate (AC-006-4, AC-006-5)
        needs_confirm = bool(meta.requires_permissions) or not meta.safe_to_auto_activate
        if needs_confirm and not confirm:
            raise SkillValidationError(
                name,
                [
                    f"Skill '{name}' requires explicit confirmation "
                    "(safe_to_auto_activate=False or requires_permissions set). "
                    "Pass confirm=True to activate."
                ],
            )

        # Self-validation (AC-006-9 guard)
        if not skill.validate():
            raise SkillValidationError(
                name,
                [f"Skill '{name}' failed its own validate() check."],
            )

        await skill.initialize()

        # Create relative symlink in active/
        link = self._active / name
        if not link.exists():
            relative_target = Path(os.path.relpath(str(skill_dir), str(self._active)))
            link.symlink_to(relative_target)

        self._active_skills[name] = skill
        logger.info("skill_activated", skill=name, version=meta.version)

    async def deactivate(self, name: str) -> None:
        """Deactivate a skill by name.

        Calls ``teardown()``, removes the symlink, and unregisters the skill.

        Args:
            name: Skill name to deactivate.
        """
        skill = self._active_skills.pop(name, None)
        if skill is not None:
            try:
                await skill.teardown()
            except Exception as exc:
                logger.warning("skill_teardown_error", skill=name, error=str(exc))

        link = self._active / name
        if link.is_symlink():
            link.unlink()

        logger.info("skill_deactivated", skill=name)

    # ── Tool Aggregation ─────────────────────────────────────────────────────

    def get_active_tools(self) -> list[BaseTool]:
        """Return all tools from active skills, deduplicated by tool name.

        Returns:
            Flat list of :class:`langchain_core.tools.BaseTool` instances.
        """
        seen: set[str] = set()
        tools: list[BaseTool] = []
        for skill in self._active_skills.values():
            try:
                for t in skill.get_tools():
                    if t.name not in seen:
                        seen.add(t.name)
                        tools.append(t)
            except Exception as exc:
                logger.error(
                    "skill_get_tools_error",
                    skill=skill.metadata.name,
                    error=str(exc),
                )
        return tools

    # ── Reload ───────────────────────────────────────────────────────────────

    async def reload_all(self) -> None:
        """Teardown all active skills and re-discover from the filesystem.

        Called by the file watcher (Phase 8) when skills are added/modified.
        The ``active/`` symlinks are the source of truth: names are collected
        from that directory *before* any deactivation (since :meth:`deactivate`
        removes symlinks), then all skills are torn down and the collected
        names are re-activated.
        """
        # Snapshot symlinks BEFORE deactivation removes them
        names_to_reload: list[str] = []
        if self._active.exists():
            for link in sorted(self._active.iterdir()):
                if link.name.startswith((".", "_")) or not link.is_dir():
                    continue
                names_to_reload.append(link.name)

        # Teardown everything currently in memory
        for name in list(self._active_skills.keys()):
            await self.deactivate(name)

        # Re-activate from available/ / custom/
        for name in names_to_reload:
            try:
                await self.activate(name, confirm=True)
            except Exception as exc:
                logger.error("skill_reload_error", skill=name, error=str(exc))

    def install_from_zip(self, zip_path: str | Path) -> tuple[str, str | None]:
        """Install a skill from a zip archive containing a ``skill.md`` package.

        The zip must contain ``skill.md`` either at the archive root or inside a
        single top-level directory.  ``scripts/`` and ``references/`` are
        optional.  A ``skill.py`` wrapper is generated automatically so the
        skill can be loaded by the standard :class:`SkillsManager` machinery.

        Expected archive layouts (both accepted)::

            # Root-level layout
            skill.md
            scripts / my_tool.py
            references / doc.md

            # Single top-level directory layout
            my_skill / skill.md
            my_skill / scripts / my_tool.py
            my_skill / references / doc.md

        Args:
            zip_path: Filesystem path to the ``.zip`` archive.

        Returns:
            ``(skill_name, None)`` on success, or ``("", error_message)`` on
            failure.
        """
        from prismal.skills.base import generate_skill_py

        src = Path(zip_path).expanduser().resolve()
        if not src.exists():
            return "", f"Archivo no encontrado: {zip_path}"
        if src.suffix.lower() != ".zip":
            return "", f"Se esperaba un archivo .zip: {zip_path}"

        try:
            with zipfile.ZipFile(src, "r") as zf:
                names = zf.namelist()
                prefix = _zip_detect_prefix(names)
                if prefix is None:
                    return (
                        "",
                        "El zip no contiene skill.md "
                        "(ni en la raíz ni en un directorio raíz único).",
                    )

                skill_name = _zip_skill_name(prefix, src)
                dest = self._available / skill_name
                if dest.exists():
                    return (
                        "",
                        f"Ya existe un skill llamado '{skill_name}' en available/. "
                        "Elige un nombre distinto o elimínalo primero.",
                    )

                dest.mkdir(parents=True)
                for member in names:
                    rel = member[len(prefix) :]
                    if not rel:
                        continue
                    target = dest / rel
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as zf_src, target.open("wb") as out_f:
                            out_f.write(zf_src.read())

        except zipfile.BadZipFile:
            return "", f"Archivo zip inválido: {zip_path}"
        except Exception as exc:
            return "", f"Error al extraer el zip: {exc}"

        # Auto-generate skill.py from the extracted skill.md
        try:
            generate_skill_py(dest)
        except Exception as exc:
            shutil.rmtree(str(dest), ignore_errors=True)
            return "", f"Error generando skill.py desde skill.md: {exc}"

        logger.info("skill_installed_from_zip", skill=skill_name, source=str(src))
        return skill_name, None

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _restore_active_skills(self) -> None:
        """Restore active skills from symlinks in ``active/`` on startup.

        Scans the ``active/`` directory for symlinks left by previous
        :meth:`activate` calls and loads each skill into ``_active_skills``.
        ``initialize()`` is **not** called here (it is async and most skills
        leave it as a no-op); if a skill requires async setup it will be
        called the next time :meth:`activate` is invoked explicitly.

        Errors (broken symlinks, missing ``skill.py``, bad code) are logged
        as warnings so a single broken skill never prevents the others from
        loading.
        """
        if not self._active.exists():
            return
        for link in sorted(self._active.iterdir()):
            if link.name.startswith((".", "_")):
                continue
            if not link.is_dir():
                continue
            skill_py = link / "skill.py"
            if not skill_py.exists():
                if _find_skill_md(link) is not None:
                    try:
                        from prismal.skills.base import (
                            generate_skill_py,
                        )

                        generate_skill_py(link)
                    except Exception as exc:
                        logger.warning("skill_restore_md_error", skill=link.name, error=str(exc))
                        continue
                else:
                    logger.warning(
                        "skill_restore_broken_symlink",
                        skill=link.name,
                        path=str(link),
                    )
                    continue
            try:
                skill_cls = self._load_skill_class(skill_py)
                self._active_skills[link.name] = skill_cls()
                logger.debug("skill_restored", skill=link.name)
            except Exception as exc:
                logger.warning("skill_restore_error", skill=link.name, error=str(exc))

    def _find_skill_dir(self, name: str) -> tuple[Path, bool]:
        """Return (skill_dir, is_custom) for the given skill name.

        Searches ``available/``, ``custom/``, and any configured external
        directories in that order.  External-directory skills are treated the
        same as ``available/`` skills (``is_custom=False``).

        Args:
            name: Skill directory name.

        Raises:
            SkillLoadError: Skill not found in available/, custom/, or any
                external directory.
        """
        available_dir = self._available / name
        if available_dir.is_dir():
            return available_dir, False
        custom_dir = self._custom / name
        if custom_dir.is_dir():
            return custom_dir, True
        for ext_dir in self._external_dirs:
            ext_skill_dir = ext_dir / name
            if ext_skill_dir.is_dir():
                return ext_skill_dir, False
        raise SkillLoadError(
            name,
            f"Not found in available/, custom/, or external dirs: "
            f"{[str(d) for d in self._external_dirs]}",
        )

    def _load_skill_class(self, skill_py: Path) -> type[BaseSkill]:
        """Dynamically load the BaseSkill subclass from a skill.py file.

        Args:
            skill_py: Absolute path to the skill module file.

        Returns:
            The first :class:`~prismal.skills.base.BaseSkill` subclass found.

        Raises:
            SkillLoadError: No BaseSkill subclass found in the module.
        """
        from prismal.skills.base import BaseSkill  # local to avoid circular

        module_name = f"prismal.skills._dynamic.{skill_py.parent.name}"
        # Evict cached module so changes are picked up on reload
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, skill_py)
        if spec is None or spec.loader is None:
            raise SkillLoadError(skill_py.parent.name, "Cannot create module spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise SkillLoadError(skill_py.parent.name, f"Error executing module: {exc}") from exc

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseSkill)
                and attr is not BaseSkill
                # Only pick classes actually defined in this module, not imported ones
                and getattr(attr, "__module__", None) == module.__name__
            ):
                return attr

        raise SkillLoadError(skill_py.parent.name, "No BaseSkill subclass found in skill.py")

    def _scan_dir(
        self,
        directory: Path,
        status: Literal["available", "custom", "external"],
        skip_names: set[str],
    ) -> list[SkillInfo]:
        """Scan a directory and build SkillInfo for each subdirectory.

        Args:
            directory: The directory to scan (available/ or custom/).
            status: Status label to assign to discovered skills.
            skip_names: Names to skip (already active).

        Returns:
            List of :class:`SkillInfo` objects.
        """
        infos: list[SkillInfo] = []
        if not directory.exists():
            return infos
        for d in sorted(directory.iterdir()):
            if d.name.startswith((".", "_")) or not d.is_dir():
                continue
            if d.name in skip_names:
                continue
            skill_py = d / "skill.py"
            if not skill_py.exists():
                if _find_skill_md(d) is not None:
                    try:
                        from prismal.skills.base import (
                            generate_skill_py,
                        )

                        generate_skill_py(d)
                    except Exception as exc:
                        logger.warning("skill_md_generate_error", skill=d.name, error=str(exc))
                        infos.append(
                            SkillInfo(
                                name=d.name,
                                status="error",
                                version="?",
                                description="",
                                author="?",
                                safe_to_auto_activate=False,
                                error_message=str(exc),
                            )
                        )
                        continue
                else:
                    continue
            try:
                skill_cls = self._load_skill_class(skill_py)
                meta = skill_cls().metadata
                infos.append(
                    SkillInfo(
                        name=meta.name,
                        status=status,
                        version=meta.version,
                        description=meta.description,
                        author=meta.author,
                        safe_to_auto_activate=meta.safe_to_auto_activate,
                        requires_permissions=list(meta.requires_permissions),
                    )
                )
            except Exception as exc:
                logger.warning("skill_scan_error", skill=d.name, error=str(exc))
                infos.append(
                    SkillInfo(
                        name=d.name,
                        status="error",
                        version="?",
                        description="",
                        author="?",
                        safe_to_auto_activate=False,
                        error_message=str(exc),
                    )
                )
        return infos


__all__ = ["SkillInfo", "SkillStatus", "SkillsManager"]
