"""Skills manager — discovery, activation, and tool aggregation.

:class:`SkillsManager` orchestrates the three-tier skills system:

* ``available/`` — installed skills that can be activated
* ``active/``    — symlinks to enabled skills (managed by this module)
* ``custom/``    — AI-generated skills that require human review before use

Example::

    from pathlib import Path
    from lightagent.skills.manager import SkillsManager

    manager = SkillsManager()
    print(manager.list_skills())
    await manager.activate("web_search", confirm=True)
    tools = manager.get_active_tools()
    await manager.deactivate("web_search")
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightagent.core.exceptions import SkillLoadError, SkillValidationError
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from lightagent.skills.base import BaseSkill

logger = get_logger("lightagent.skills.manager")

SkillStatus = Literal["active", "available", "custom", "error"]


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


class SkillsManager:
    """Manages skill discovery, activation, deactivation, and tool aggregation.

    Args:
        skills_root: Root of the skills tree.  Defaults to the ``skills/``
            package directory next to this module.
    """

    def __init__(self, skills_root: Path | None = None) -> None:
        """Initialise the manager.

        Args:
            skills_root: Override for the default skills root directory.
        """
        if skills_root is None:
            skills_root = Path(__file__).parent
        self._root = skills_root
        self._available = self._root / "available"
        self._active = self._root / "active"
        self._custom = self._root / "custom"
        self._active_skills: dict[str, BaseSkill] = {}

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
        needs_confirm = (
            bool(meta.requires_permissions) or not meta.safe_to_auto_activate
        )
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
        """
        names = list(self._active_skills.keys())
        for name in names:
            await self.deactivate(name)

        # Re-activate anything linked in active/
        if self._active.exists():
            for link in sorted(self._active.iterdir()):
                if link.name.startswith((".", "_")):
                    continue
                if not link.is_dir():
                    continue
                try:
                    await self.activate(link.name, confirm=True)
                except Exception as exc:
                    logger.error("skill_reload_error", skill=link.name, error=str(exc))

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _find_skill_dir(self, name: str) -> tuple[Path, bool]:
        """Return (skill_dir, is_custom) for the given skill name.

        Args:
            name: Skill directory name.

        Raises:
            SkillLoadError: Skill not found in available/ or custom/.
        """
        available_dir = self._available / name
        if available_dir.is_dir():
            return available_dir, False
        custom_dir = self._custom / name
        if custom_dir.is_dir():
            return custom_dir, True
        raise SkillLoadError(name, "Not found in available/ or custom/")

    def _load_skill_class(self, skill_py: Path) -> type[BaseSkill]:
        """Dynamically load the BaseSkill subclass from a skill.py file.

        Args:
            skill_py: Absolute path to the skill module file.

        Returns:
            The first :class:`~lightagent.skills.base.BaseSkill` subclass found.

        Raises:
            SkillLoadError: No BaseSkill subclass found in the module.
        """
        from lightagent.skills.base import BaseSkill  # local to avoid circular

        module_name = f"lightagent.skills._dynamic.{skill_py.parent.name}"
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
            raise SkillLoadError(
                skill_py.parent.name, f"Error executing module: {exc}"
            ) from exc

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseSkill)
                and attr is not BaseSkill
            ):
                return attr

        raise SkillLoadError(
            skill_py.parent.name, "No BaseSkill subclass found in skill.py"
        )

    def _scan_dir(
        self,
        directory: Path,
        status: Literal["available", "custom"],
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
