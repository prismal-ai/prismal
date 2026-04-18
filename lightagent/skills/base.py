"""Base skill interface for LightAgent's three-tier skills system.

All LightAgent skills must inherit from :class:`BaseSkill` and implement
the :meth:`metadata` property and :meth:`get_tools` method.

Example::

    from lightagent.skills.base import BaseSkill, SkillMetadata
    from langchain_core.tools import BaseTool, tool

    class MySkill(BaseSkill):
        @property
        def metadata(self) -> SkillMetadata:
            return SkillMetadata(
                name="my_skill",
                description="Does something useful",
                version="1.0.0",
                author="me",
            )

        def get_tools(self) -> list[BaseTool]:
            @tool
            def do_thing(query: str) -> str:
                \"\"\"Do the thing.

                Args:
                    query: Input string.

                Returns:
                    Result string.
                \"\"\"
                return f"done: {query}"
            return [do_thing]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

    from langchain_core.tools import BaseTool


class SkillMetadata(BaseModel):
    """Metadata describing a LightAgent skill.

    Used by :class:`~lightagent.skills.manager.SkillsManager` for discovery,
    validation, and display in ``lightagent skills list``.
    """

    name: str = Field(..., description="Unique identifier slug (snake_case)")
    description: str = Field(..., description="One-line description of the skill")
    version: str = Field(..., description="Semantic version string e.g. '1.0.0'")
    author: str = Field(..., description="Author name or handle")
    requires_permissions: list[str] = Field(
        default_factory=list,
        description="PermissionType values required by this skill",
    )
    safe_to_auto_activate: bool = Field(
        default=False,
        description="If False, explicit user confirmation is required to activate",
    )
    tags: list[str] = Field(default_factory=list, description="Categorisation tags")
    min_python: str = Field(default="3.13", description="Minimum Python version")


class BaseSkill(ABC):
    """Abstract base class for all LightAgent skills.

    Subclass this in ``skill.py`` inside each skill directory.  The skill
    is loaded and validated by :class:`~lightagent.skills.manager.SkillsManager`
    before activation.
    """

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return immutable metadata for this skill."""
        ...

    @abstractmethod
    def get_tools(self) -> list[BaseTool]:
        """Return the LangChain-compatible tools provided by this skill.

        Returns:
            A list of :class:`langchain_core.tools.BaseTool` instances.
        """
        ...

    async def initialize(self) -> None:  # noqa: B027
        """Optional async setup called when the skill is activated.

        Override to perform one-time initialisation (open connections,
        warm caches, etc.).
        """

    async def teardown(self) -> None:  # noqa: B027
        """Optional async cleanup called when the skill is deactivated.

        Override to release resources held since :meth:`initialize`.
        """

    def validate(self) -> bool:
        """Self-check before activation.

        Override to verify runtime prerequisites (env vars, deps, …).

        Returns:
            ``True`` if the skill is ready to be activated.
        """
        return True


# ---------------------------------------------------------------------------
# Markdown-skill helpers
# ---------------------------------------------------------------------------


def _find_skill_md(skill_dir: Path) -> Path | None:
    """Return the ``skill.md`` file inside *skill_dir*, case-insensitively.

    Accepts any capitalisation (``skill.md``, ``SKILL.md``, ``Skill.md``, …).

    Args:
        skill_dir: Directory to search in.

    Returns:
        The :class:`~pathlib.Path` to the file, or ``None`` if not found.
    """
    if not skill_dir.is_dir():
        return None
    for entry in skill_dir.iterdir():
        if entry.is_file() and entry.name.lower() == "skill.md":
            return entry
    return None


def parse_skill_md(skill_dir: Path) -> dict[str, object]:
    """Parse YAML frontmatter from ``skill.md`` inside *skill_dir*.

    The frontmatter must be enclosed in ``---`` fences as the very first block
    of the file.  Accepts any capitalisation of the filename (``SKILL.md``,
    ``skill.md``, …).  Fields that are absent default to sensible fallbacks at
    the call site.

    Args:
        skill_dir: Directory that contains ``skill.md`` (any case).

    Returns:
        Parsed frontmatter as a plain :class:`dict`; empty dict on any error.
    """
    import yaml

    md_path = _find_skill_md(skill_dir)
    if md_path is None:
        return {}
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end].strip()) or {}
    except Exception:
        return {}


def _skill_md_body(skill_dir: Path) -> str:
    """Return the body of ``skill.md`` (everything after the YAML frontmatter).

    Strips the ``--- ... ---`` block so only the instructional Markdown content
    is returned.  This is what the agent reads to understand how to apply the
    skill.

    Args:
        skill_dir: Directory that contains ``skill.md`` (any case).

    Returns:
        Body text as a string; empty string if not found or no body.
    """
    md_path = _find_skill_md(skill_dir)
    if md_path is None:
        return ""
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3 :].lstrip("\n")


def _py_class_name(skill_name: str) -> str:
    """Convert a snake_case or kebab-case skill name to PascalCase.

    Args:
        skill_name: Skill identifier string (e.g. ``"my-skill"``).

    Returns:
        PascalCase class name suitable for use in generated Python source.
    """
    return "".join(part.capitalize() for part in skill_name.replace("-", "_").split("_"))


def generate_skill_py(skill_dir: Path) -> None:
    """Generate a ``skill.py`` wrapper from a ``skill.md`` package.

    Creates a ``skill.py`` inside *skill_dir* that subclasses
    :class:`MarkdownSkill` and hard-codes the metadata parsed from the YAML
    frontmatter of ``skill.md``.  The file is idempotent — call again to
    refresh after editing ``skill.md``.

    Accepts any capitalisation of the skill definition file (``skill.md``,
    ``SKILL.md``, …).  The generated class inherits :meth:`MarkdownSkill.get_tools`
    which automatically exposes scripts, a guide tool, and reference-reading
    tools — no ``get_tools`` override is needed in the generated file.

    Expected package layout::

        my_skill/
        ├── SKILL.md          ← YAML frontmatter + methodology description
        ├── scripts/          ← .py files (with @tool decorators or plain CLIs)
        │   └── my_tool.py
        └── references/       ← optional reference documents (Markdown, txt, …)
            └── guide.md

    Args:
        skill_dir: Directory containing at least ``skill.md`` (any case).

    Raises:
        FileNotFoundError: If no ``skill.md`` file is found in *skill_dir*.
    """
    if _find_skill_md(skill_dir) is None:
        raise FileNotFoundError(f"skill.md not found in {skill_dir}")

    meta = parse_skill_md(skill_dir)
    name: str = str(meta.get("name", skill_dir.name))
    description: str = str(meta.get("description", "Markdown-based skill"))
    version: str = str(meta.get("version", "1.0.0"))
    author: str = str(meta.get("author", "unknown"))
    tags: list[str] = list(meta.get("tags", []))  # type: ignore[arg-type]
    safe: bool = bool(meta.get("safe_to_auto_activate", False))
    perms: list[str] = list(meta.get("requires_permissions", []))  # type: ignore[arg-type]

    class_name = _py_class_name(name)
    # No get_tools() override — MarkdownSkill.get_tools() auto-discovers scripts,
    # generates a guide tool from the SKILL.md body, and exposes reference files.
    code = (
        '"""Auto-generated skill wrapper — edit skill.md, not this file."""\n'
        "from __future__ import annotations\n\n"
        "from pathlib import Path\n\n"
        "from lightagent.skills.base import MarkdownSkill, SkillMetadata\n\n"
        "_HERE = Path(__file__).parent\n\n\n"
        f"class {class_name}(MarkdownSkill):\n"
        '    """Skill loaded from the skill.md package in this directory."""\n\n'
        "    def __init__(self) -> None:\n"
        '        """Initialise the markdown-based skill."""\n'
        "        super().__init__(_HERE)\n\n"
        "    @property\n"
        "    def metadata(self) -> SkillMetadata:\n"
        '        """Return skill metadata parsed from skill.md frontmatter."""\n'
        "        return SkillMetadata(\n"
        f"            name={name!r},\n"
        f"            description={description!r},\n"
        f"            version={version!r},\n"
        f"            author={author!r},\n"
        f"            tags={tags!r},\n"
        f"            safe_to_auto_activate={safe!r},\n"
        f"            requires_permissions={perms!r},\n"
        "        )\n"
    )
    (skill_dir / "skill.py").write_text(code, encoding="utf-8")


def _make_subprocess_tool(
    tool_name: str,
    script_path: str,
    description: str,
) -> BaseTool:
    """Create a LangChain tool that invokes a Python script as a subprocess.

    Used when a ``scripts/`` file contains no ``@tool``-decorated functions —
    the script is treated as a CLI and wrapped automatically.

    Args:
        tool_name: Name for the resulting :class:`~langchain_core.tools.BaseTool`.
        script_path: Absolute filesystem path to the ``.py`` script.
        description: Human-readable description for the tool.

    Returns:
        A :class:`~langchain_core.tools.StructuredTool` that runs the script.
    """
    import subprocess
    import sys as _sys

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    _script_path = script_path  # capture for closure

    class _Args(BaseModel):
        args: str = Field(
            default="",
            description="Space-separated command-line arguments for the script",
        )

    def _run(args: str = "") -> str:
        """Execute the script with optional arguments."""
        from lightagent.core.config import get_settings

        if not get_settings().shell_enabled:
            return "Shell execution is disabled (LIGHTAGENT_SHELL_ENABLED=false)."
        cmd = [_sys.executable, _script_path] + (args.split() if args.strip() else [])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return "Script timed out after 60 s."
        out = result.stdout
        if result.stderr:
            out += f"\nSTDERR:\n{result.stderr}"
        return out.strip() or "(no output)"

    return StructuredTool.from_function(
        func=_run,
        name=tool_name,
        description=description,
        args_schema=_Args,
    )


def _make_reference_tools(
    skill_name: str,
    skill_dir: Path,
    skill_md_body: str,
) -> list[BaseTool]:
    """Create guide and reference-reading tools for a markdown skill.

    Generates two tools:

    * ``{safe_name}__guide()`` — returns the full body of ``SKILL.md`` so
      the agent can read the methodology / instructions without a separate
      file-read call.
    * ``{safe_name}__read_reference(filename)`` — returns the content of any
      file inside the ``references/`` subdirectory by name.

    Args:
        skill_name: Canonical skill name (may contain hyphens).
        skill_dir: Root directory of the skill package.
        skill_md_body: Body text of ``SKILL.md`` (non-frontmatter content).

    Returns:
        List of :class:`~langchain_core.tools.BaseTool` instances (0-2 items).
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    safe_name = skill_name.replace("-", "_")
    tools: list[BaseTool] = []

    # -- Guide tool (always added when body is non-empty) --------------------
    if skill_md_body.strip():
        _body = skill_md_body

        def _read_guide() -> str:
            """Return the full usage guide for the skill."""
            return _body

        tools.append(
            StructuredTool.from_function(
                func=_read_guide,
                name=f"{safe_name}__guide",
                description=(
                    f"Return the full usage guide for the '{skill_name}' skill. "
                    "Read this first to understand how to apply the skill, "
                    "which documents to create, and what templates are available."
                ),
            )
        )

    # -- Reference-reading tool (added when references/ has files) -----------
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        ref_files = sorted(f.name for f in refs_dir.iterdir() if f.is_file())
        if ref_files:
            _refs_dir = refs_dir

            class _RefArgs(BaseModel):
                filename: str = Field(
                    ...,
                    description=(
                        f"Name of the reference file to read. Available: {', '.join(ref_files)}"
                    ),
                )

            def _read_ref(filename: str) -> str:
                """Read a reference document from the skill's references directory."""
                target = _refs_dir / filename
                if not target.is_file():
                    available = ", ".join(f.name for f in _refs_dir.iterdir() if f.is_file())
                    return f"File '{filename}' not found. Available: {available}"
                return target.read_text(encoding="utf-8", errors="replace")

            tools.append(
                StructuredTool.from_function(
                    func=_read_ref,
                    name=f"{safe_name}__read_reference",
                    description=(
                        f"Read a reference document from the '{skill_name}' skill. "
                        f"Available files: {', '.join(ref_files)}"
                    ),
                    args_schema=_RefArgs,
                )
            )

    return tools


class MarkdownSkill(BaseSkill):
    """Concrete :class:`BaseSkill` for skills installed from a ``skill.md`` package.

    A ``skill.md`` package contains:

    * ``skill.md``     — YAML frontmatter (name, description, version, …) plus
      free-form documentation for the agent.
    * ``scripts/``     — ``.py`` files where each function decorated with
      ``@tool`` becomes a LangChain :class:`~langchain_core.tools.BaseTool`.
    * ``references/``  — optional reference documents (txt, md, pdf, …) that
      the agent can load for context.

    Subclass this (via the auto-generated ``skill.py``) — do **not** instantiate
    :class:`MarkdownSkill` directly in production; use
    :func:`generate_skill_py` to produce a proper ``skill.py`` first.
    """

    def __init__(self, skill_dir: Path) -> None:
        """Initialise with the root directory of the skill package.

        Args:
            skill_dir: Directory containing ``skill.md`` and optionally
                ``scripts/`` and ``references/`` sub-directories.
        """
        self._skill_dir = skill_dir

    @property
    def metadata(self) -> SkillMetadata:
        """Return metadata parsed dynamically from ``skill.md`` frontmatter."""
        meta = parse_skill_md(self._skill_dir)
        return SkillMetadata(
            name=str(meta.get("name", self._skill_dir.name)),
            description=str(meta.get("description", "Markdown-based skill")),
            version=str(meta.get("version", "1.0.0")),
            author=str(meta.get("author", "unknown")),
            tags=list(meta.get("tags", [])),  # type: ignore[arg-type]
            safe_to_auto_activate=bool(meta.get("safe_to_auto_activate", False)),
            requires_permissions=list(meta.get("requires_permissions", [])),  # type: ignore[arg-type]
        )

    def get_tools(self) -> list[BaseTool]:
        """Return all tools for this skill.

        Combines three sources:

        1. ``@tool``-decorated functions from ``scripts/*.py``.
        2. Auto-wrapped CLI tools for any script without ``@tool`` decorators.
        3. A guide tool (returns the ``SKILL.md`` body) and a reference-reading
           tool (reads files from ``references/``), when those assets exist.

        Returns:
            Deduplicated list of LangChain-compatible tools.
        """
        tools = self._load_script_tools()
        body = _skill_md_body(self._skill_dir)
        tools.extend(_make_reference_tools(self.metadata.name, self._skill_dir, body))
        return tools

    def _load_script_tools(self) -> list[BaseTool]:
        """Load tools from the ``scripts/`` subdirectory.

        For each ``.py`` file (not starting with ``_``):

        * If the module contains ``@tool``-decorated objects, those are collected.
        * If the module contains **no** ``@tool`` objects, the script is treated
          as a CLI and automatically wrapped in a subprocess-invoking tool whose
          description is taken from the module's docstring.

        Returns:
            List of :class:`~langchain_core.tools.BaseTool` instances.
        """
        import importlib.util
        import sys

        from langchain_core.tools import BaseTool as _BaseTool

        tools: list[_BaseTool] = []
        scripts_dir = self._skill_dir / "scripts"
        if not scripts_dir.exists():
            return tools

        safe_prefix = self._skill_dir.name.replace("-", "_")

        for script in sorted(scripts_dir.glob("*.py")):
            if script.name.startswith("_"):
                continue
            mod_name = f"_md_skill_{self._skill_dir.name}_{script.stem}"
            sys.modules.pop(mod_name, None)
            spec = importlib.util.spec_from_file_location(mod_name, script)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:  # noqa: S112 — skip modules whose import fails; log below
                continue

            # Collect @tool-decorated objects from this module
            script_tools: list[_BaseTool] = [
                getattr(mod, attr) for attr in dir(mod) if isinstance(getattr(mod, attr), _BaseTool)
            ]

            if script_tools:
                tools.extend(script_tools)
            else:
                # No @tool decorators — auto-wrap as a subprocess CLI tool.
                raw_doc: str = getattr(mod, "__doc__", None) or ""
                description = (
                    raw_doc.strip().split("\n")[0].strip()
                    or f"Run {script.name} with optional arguments."
                )
                tool_name = f"{safe_prefix}__{script.stem.replace('-', '_')}"
                tools.append(_make_subprocess_tool(tool_name, str(script.resolve()), description))

        return tools

    def get_references(self) -> list[Path]:
        """Return paths of all files in the ``references/`` directory.

        Returns:
            Sorted list of :class:`~pathlib.Path` objects for each reference
            file; empty list if ``references/`` does not exist.
        """
        refs_dir = self._skill_dir / "references"
        if not refs_dir.exists():
            return []
        return sorted(f for f in refs_dir.iterdir() if f.is_file())


__all__ = [
    "BaseSkill",
    "MarkdownSkill",
    "SkillMetadata",
    "_find_skill_md",
    "_skill_md_body",
    "generate_skill_py",
    "parse_skill_md",
]
