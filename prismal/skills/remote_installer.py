"""Remote skill installer for LightAgent.

Downloads skills from GitHub repositories and installs them into the
three-tier skills directory structure (``skills/available/``).

Supports the same install convention as the Claude Code CLI::

    # Claude Code CLI (reference)
    npx skills add https://github.com/anthropics/skills --skill skill-creator

    # LightAgent equivalent
    lightagent skills install-remote https://github.com/anthropics/skills \\
        --skill skill-creator

Claude Code skills stored as YAML/Markdown are wrapped in a generated
LightAgent-compatible ``BaseSkill`` subclass so they integrate with the
existing skills lifecycle (enable/disable/validate).

Python-based skills (containing ``skill.py``) are installed as-is.
"""

from __future__ import annotations

import textwrap
from pathlib import Path  # noqa: TC003 — used at runtime, not only in annotations

import httpx
import structlog
import yaml

from prismal.skills.manager import SkillsManager

logger = structlog.get_logger("lightagent.skills.remote_installer")

_GITHUB_API_BASE = "https://api.github.com"
_ANTHROPIC_SKILLS_REPO = "anthropics/skills"

# Recommended Claude Code skills from Anthropic to surface in doctor checks
RECOMMENDED_SKILLS: list[dict[str, str]] = [
    {
        "repo": _ANTHROPIC_SKILLS_REPO,
        "skill": "skill-creator",
        "installed_name": "skill_creator",
        "description": "AI-powered skill generator for LightAgent",
    },
]


class RemoteSkillInstaller:
    """Downloads and installs skills from remote GitHub repositories.

    Handles two skill formats:

    * **Claude Code YAML/MD skills** — wrapped in a generated
      ``BaseSkill`` Python class that exposes the content as an agent tool.
    * **Native Python skills** — copied directly (must contain ``skill.py``).

    Example::

        installer = RemoteSkillInstaller()
        path = asyncio.run(installer.install("anthropics/skills", "skill-creator"))
        print(f"Installed to: {path}")
    """

    def __init__(self, manager: SkillsManager | None = None) -> None:
        """Initialise the installer.

        Args:
            manager: ``SkillsManager`` instance.  A default instance is
                created when *None* is passed.
        """
        self._manager = manager or SkillsManager()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def parse_repo_slug(url_or_slug: str) -> str:
        """Normalise a GitHub URL or ``owner/repo`` slug to ``owner/repo`` form.

        Args:
            url_or_slug: Full GitHub URL (``https://github.com/owner/repo``)
                or short ``owner/repo`` slug.

        Returns:
            Normalised ``owner/repo`` string.
        """
        url = url_or_slug.rstrip("/")
        for prefix in ("https://github.com/", "http://github.com/"):
            if url.startswith(prefix):
                url = url[len(prefix) :]
                break
        # Keep only the first two path components (owner/repo)
        parts = [p for p in url.split("/") if p][:2]
        return "/".join(parts)

    async def list_available_skills(self, repo: str) -> list[str]:
        """List installable skills in a GitHub repository.

        Handles two repo layouts:

        * **Directory-based** — each top-level directory is one skill.
        * **Flat file-based** — YAML/MD files at the root are each one skill
          (the skill name is the file stem, e.g. ``skill-creator.yaml`` →
          ``skill-creator``).

        Args:
            repo: GitHub URL or ``owner/repo`` slug.

        Returns:
            Sorted list of skill names (directories or file stems).

        Raises:
            httpx.HTTPError: On network or HTTP errors.
        """
        slug = self.parse_repo_slug(repo)
        api_url = f"{_GITHUB_API_BASE}/repos/{slug}/contents"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                api_url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            items: list[dict[str, object]] = resp.json()

        names: set[str] = set()
        for item in items:
            raw_name = str(item.get("name", ""))
            if raw_name.startswith("."):
                continue
            if item.get("type") == "dir":
                names.add(raw_name)
            elif item.get("type") == "file":
                # Include YAML/MD files as flat skills (use stem as skill name)
                for ext in (".yaml", ".yml", ".md"):
                    if raw_name.endswith(ext):
                        names.add(raw_name[: -len(ext)])
                        break

        return sorted(names)

    async def install(
        self,
        repo: str,
        skill_name: str,
        dest_name: str | None = None,
        enable: bool = False,
    ) -> Path:
        """Download and install a skill from a GitHub repository.

        Args:
            repo: GitHub URL or ``owner/repo`` slug.
            skill_name: Name of the skill directory inside the repository
                (e.g. ``"skill-creator"``).
            dest_name: Override for the installed skill directory name.
                Defaults to *skill_name* with hyphens replaced by underscores.
            enable: Whether to activate the skill after installing.
                Defaults to ``False`` — Claude Code skills require human
                review before activation.

        Returns:
            Path to the installed skill directory inside ``skills/available/``.

        Raises:
            ValueError: If the skill already exists or cannot be found.
            httpx.HTTPError: On network or HTTP errors.
        """
        slug = self.parse_repo_slug(repo)
        installed_name = dest_name or skill_name.replace("-", "_")

        logger.info(
            "remote_installer.install.start",
            repo=slug,
            skill=skill_name,
            dest=installed_name,
        )

        dest = self._manager._available / installed_name
        if dest.exists():
            raise ValueError(
                f"Skill '{installed_name}' already exists in skills/available/. "
                "Use --dest-name to install under a different name, or remove "
                f"it first with: lightagent skills remove {installed_name}"
            )

        files = await self._fetch_skill_files(slug, skill_name)
        if not files:
            raise ValueError(f"No files found for skill '{skill_name}' in repo '{slug}'.")

        if self._is_claude_code_skill(files):
            self._install_claude_code_skill(
                repo_slug=slug,
                skill_name=skill_name,
                installed_name=installed_name,
                files=files,
                dest=dest,
            )
        else:
            self._install_native_skill(
                skill_name=skill_name,
                files=files,
                dest=dest,
            )

        logger.info(
            "remote_installer.install.done",
            dest=str(dest),
            enable=enable,
        )

        if enable:
            await self._manager.activate(installed_name, confirm=True)

        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_skill_files(self, repo_slug: str, skill_name: str) -> dict[str, str]:
        """Fetch all text files for a skill from a GitHub repository.

        Supports two repository layouts automatically:

        1. **Directory layout** — ``GET /contents/{skill_name}`` returns a
           list of files inside the skill directory.
        2. **Flat file layout** — the skill is a single file at the repo root,
           tried with extensions ``.yaml``, ``.yml``, and ``.md`` when the
           directory path returns HTTP 404.

        Args:
            repo_slug: ``owner/repo`` slug.
            skill_name: Skill name (directory name or file stem).

        Returns:
            Mapping of ``filename → file content`` for every text file found.

        Raises:
            ValueError: When no matching skill is found in the repository.
            httpx.HTTPError: On network errors other than 404.
        """
        # ── Strategy 1: directory at /contents/{skill_name} ──────────────
        dir_url = f"{_GITHUB_API_BASE}/repos/{repo_slug}/contents/{skill_name}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                dir_url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )

        if resp.status_code not in (404, 200):
            resp.raise_for_status()

        if resp.status_code == 200:
            # Could be a list (directory) or a single file object
            data = resp.json()
            if isinstance(data, list):
                # Directory: download every file inside it
                entries: list[dict[str, object]] = data
                files = await self._download_entries(entries)
                logger.debug(
                    "remote_installer.files_fetched",
                    repo=repo_slug,
                    skill=skill_name,
                    layout="directory",
                    count=len(files),
                )
                if files:
                    return files
                # Directory exists but contains no downloadable files (only
                # subdirs). Fall through to the flat-file strategy.

            if isinstance(data, dict) and data.get("type") == "file":
                # Single file returned directly (edge case: exact name match)
                raw_url = str(data.get("download_url") or "")
                if raw_url:
                    async with httpx.AsyncClient(timeout=30) as client:
                        file_resp = await client.get(raw_url)
                        file_resp.raise_for_status()
                    fname = str(data.get("name", skill_name))
                    logger.debug(
                        "remote_installer.files_fetched",
                        repo=repo_slug,
                        skill=skill_name,
                        layout="single_file",
                        count=1,
                    )
                    return {fname: file_resp.text}

        # ── Strategy 2: flat file at repo root with common extensions ─────
        # The repo uses a flat layout: skills are individual YAML/MD files.
        candidate_names = [
            f"{skill_name}.yaml",
            f"{skill_name}.yml",
            f"{skill_name}.md",
        ]
        root_url = f"{_GITHUB_API_BASE}/repos/{repo_slug}/contents"
        async with httpx.AsyncClient(timeout=30) as client:
            root_resp = await client.get(
                root_url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            root_resp.raise_for_status()
            root_items: list[dict[str, object]] = root_resp.json()

        # Build a name→download_url map for fast lookup
        root_file_map: dict[str, str] = {
            str(item["name"]): str(item.get("download_url") or "")
            for item in root_items
            if item.get("type") == "file"
        }

        for candidate in candidate_names:
            raw_url = root_file_map.get(candidate, "")
            if not raw_url:
                continue
            async with httpx.AsyncClient(timeout=30) as client:
                file_resp = await client.get(raw_url)
                file_resp.raise_for_status()
            logger.debug(
                "remote_installer.files_fetched",
                repo=repo_slug,
                skill=skill_name,
                layout="flat_file",
                file=candidate,
            )
            return {candidate: file_resp.text}

        # ── Nothing found: helpful error listing what IS in the repo ─────
        skill_names = sorted(
            str(item["name"])[: -len(ext)]
            for item in root_items
            for ext in (".yaml", ".yml", ".md")
            if str(item.get("name", "")).endswith(ext)
            and not str(item.get("name", "")).startswith(".")
        ) or sorted(
            str(item["name"])
            for item in root_items
            if item.get("type") == "dir" and not str(item.get("name", "")).startswith(".")
        )
        hint = (
            f"Available skills: {', '.join(skill_names[:20])}"
            if skill_names
            else "Run 'lightagent skills list-remote <repo>' to see available skills."
        )
        raise ValueError(f"Skill '{skill_name}' not found in repo '{repo_slug}'. {hint}")

    @staticmethod
    async def _download_entries(
        entries: list[dict[str, object]],
    ) -> dict[str, str]:
        """Download text files from a GitHub directory listing.

        Args:
            entries: List of GitHub API content objects (``type == "file"``).

        Returns:
            Mapping of ``filename → file content`` for every downloadable file.
        """
        files: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=30) as client:
            for entry in entries:
                if entry.get("type") != "file":
                    continue
                raw_url = str(entry.get("download_url") or entry.get("url") or "")
                if not raw_url:
                    continue
                file_resp = await client.get(raw_url)
                file_resp.raise_for_status()
                files[str(entry["name"])] = file_resp.text
        return files

    @staticmethod
    def _is_claude_code_skill(files: dict[str, str]) -> bool:
        """Detect whether the fetched files represent a Claude Code YAML/MD skill.

        A file set is considered a Claude Code skill when it contains at least
        one YAML or Markdown file and does **not** already contain a ``skill.py``
        (which would indicate a native LightAgent skill).

        Args:
            files: Mapping of ``filename → content``.

        Returns:
            ``True`` if the files look like a Claude Code skill.
        """
        if "skill.py" in files:
            return False
        return any(name.endswith((".yaml", ".yml", ".md")) for name in files)

    def _install_claude_code_skill(
        self,
        repo_slug: str,
        skill_name: str,
        installed_name: str,
        files: dict[str, str],
        dest: Path,
    ) -> None:
        """Install a Claude Code YAML/MD skill wrapped as a LightAgent skill.

        Saves all downloaded files to *dest*, determines the primary content
        file (YAML preferred, then MD), extracts metadata, and generates a
        ``skill.py`` wrapper that exposes the content as a LangChain tool.

        Args:
            repo_slug: ``owner/repo`` slug (used in the generated docstring).
            skill_name: Original skill name from the GitHub repository.
            installed_name: Snake-case name used for the installed directory.
            files: Mapping of ``filename → content`` from the repository.
            dest: Destination ``Path`` inside ``skills/available/``.
        """
        dest.mkdir(parents=True, exist_ok=True)

        # Persist all downloaded files
        for fname, content in files.items():
            (dest / fname).write_text(content, encoding="utf-8")

        # Determine the primary content file and extract metadata
        primary_file: str = ""
        description: str = f"Claude Code skill: {skill_name}"
        version: str = "1.0.0"
        tags: list[str] = ["claude-code", "imported"]

        # Prefer YAML, fall back to MD
        yaml_files = sorted(f for f in files if f.endswith((".yaml", ".yml")))
        md_files = sorted(f for f in files if f.endswith(".md"))

        if yaml_files:
            primary_file = yaml_files[0]
            try:
                parsed = yaml.safe_load(files[primary_file])
                if isinstance(parsed, dict):
                    description = str(parsed.get("description", description))
                    version = str(parsed.get("version", version))
                    raw_tags = parsed.get("tags")
                    if isinstance(raw_tags, list):
                        tags = [str(t) for t in raw_tags] + ["claude-code", "imported"]
            except yaml.YAMLError:
                pass
        elif md_files:
            primary_file = md_files[0]
            # Extract a description from the first heading line
            for line in files[primary_file].splitlines():
                stripped = line.lstrip("# ").strip()
                if stripped:
                    description = stripped[:120]
                    break
        else:
            # Fallback: use first available file
            primary_file = next(iter(files))

        # Derive safe Python identifiers
        class_name = "".join(part.capitalize() for part in installed_name.split("_")) + "Skill"
        tool_fn_name = f"get_{installed_name}_guide"

        wrapper_py = self._generate_wrapper_py(
            repo_slug=repo_slug,
            skill_name=skill_name,
            installed_name=installed_name,
            class_name=class_name,
            tool_fn_name=tool_fn_name,
            description=description,
            version=version,
            tags=tags,
            primary_file=primary_file,
        )

        (dest / "skill.py").write_text(wrapper_py, encoding="utf-8")
        (dest / "__init__.py").write_text('"""Skill package."""\n', encoding="utf-8")

        logger.info(
            "remote_installer.claude_skill_wrapped",
            skill=skill_name,
            dest=str(dest),
            primary_file=primary_file,
            class_name=class_name,
        )

    @staticmethod
    def _install_native_skill(
        skill_name: str,
        files: dict[str, str],
        dest: Path,
    ) -> None:
        """Install a native Python-based LightAgent skill.

        Writes all fetched files to *dest* verbatim and adds a default
        ``__init__.py`` when none is present.

        Args:
            skill_name: Original skill name from the repository.
            files: Mapping of ``filename → content``.
            dest: Destination ``Path`` inside ``skills/available/``.
        """
        dest.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (dest / fname).write_text(content, encoding="utf-8")
        if "__init__.py" not in files:
            init_content = '"""Skill package."""\n'
            (dest / "__init__.py").write_text(init_content, encoding="utf-8")
        logger.info(
            "remote_installer.native_skill_installed",
            skill=skill_name,
            dest=str(dest),
        )

    @staticmethod
    def _generate_wrapper_py(
        *,
        repo_slug: str,
        skill_name: str,
        installed_name: str,
        class_name: str,
        tool_fn_name: str,
        description: str,
        version: str,
        tags: list[str],
        primary_file: str,
    ) -> str:
        """Generate the ``skill.py`` source code for a Claude Code skill wrapper.

        The generated module reads the original skill content from *primary_file*
        at import time and exposes it through a single LangChain tool.

        Args:
            repo_slug: GitHub ``owner/repo`` used in the module docstring.
            skill_name: Original skill name (used in docstrings).
            installed_name: Snake-case installed name (used as the skill ``name``
                field and as part of the tool function name).
            class_name: PascalCase class name for the ``BaseSkill`` subclass.
            tool_fn_name: Python identifier for the generated tool function.
            description: One-line description extracted from the skill YAML/MD.
            version: Version string.
            tags: List of tag strings for ``SkillMetadata``.
            primary_file: Filename of the content file to load at runtime.

        Returns:
            Python source code as a string.
        """
        tags_repr = repr(tags)
        description_repr = repr(description)
        return textwrap.dedent(f'''\
            """LightAgent wrapper for the Claude Code skill: {skill_name}.

            Auto-generated by RemoteSkillInstaller
            (lightagent.skills.remote_installer).

            Source repository: https://github.com/{repo_slug}
            Original skill   : {skill_name}
            """

            from __future__ import annotations

            from pathlib import Path

            from langchain_core.tools import BaseTool, tool

            from prismal.skills.base import BaseSkill, SkillMetadata

            # Load the original skill content once at import time.
            _SKILL_CONTENT: str = (
                Path(__file__).parent / {primary_file!r}
            ).read_text(encoding="utf-8")


            class {class_name}(BaseSkill):
                """LightAgent wrapper for the Claude Code skill '{skill_name}'."""

                @property
                def metadata(self) -> SkillMetadata:
                    """Return skill metadata."""
                    return SkillMetadata(
                        name={installed_name!r},
                        description={description_repr},
                        version={version!r},
                        author="anthropics",
                        safe_to_auto_activate=False,
                        tags={tags_repr},
                    )

                def get_tools(self) -> list[BaseTool]:
                    """Return the tools provided by this skill.

                    Returns:
                        A list containing the skill-guide retrieval tool.
                    """

                    @tool
                    def {tool_fn_name}(query: str = "") -> str:
                        """Get instructions and guidance for the {skill_name} skill.

                        Args:
                            query: Optional keyword to filter content lines.
                                When empty, returns the full guide.

                        Returns:
                            Full skill instructions, or lines matching *query*.
                        """
                        if query:
                            matched = [
                                ln
                                for ln in _SKILL_CONTENT.splitlines()
                                if query.lower() in ln.lower()
                            ]
                            return "\\n".join(matched) if matched else _SKILL_CONTENT
                        return _SKILL_CONTENT

                    return [{tool_fn_name}]
            ''')


__all__ = ["RECOMMENDED_SKILLS", "RemoteSkillInstaller"]
