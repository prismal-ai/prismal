"""Unit tests for the markdown-skill helpers in ``prismal.skills.base``.

Covers the package-parsing, code-generation and tool-construction helpers
(``parse_skill_md``, ``_skill_md_body``, ``_py_class_name``,
``generate_skill_py``, ``_make_subprocess_tool``, ``_make_reference_tools``)
and the concrete :class:`~prismal.skills.base.MarkdownSkill`, none of which were
exercised by ``test_base.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from prismal.skills.base import (
    MarkdownSkill,
    SkillMetadata,
    _as_str_list,
    _find_skill_md,
    _make_reference_tools,
    _make_subprocess_tool,
    _py_class_name,
    _skill_md_body,
    generate_skill_py,
    parse_skill_md,
)

if TYPE_CHECKING:
    from pathlib import Path

# A complete SKILL.md with YAML frontmatter + body.
_FRONTMATTER = """---
name: my-demo-skill
description: A demo skill
version: 2.1.0
author: tester
tags:
  - utility
  - demo
safe_to_auto_activate: true
requires_permissions:
  - filesystem.read
---

# Demo skill

This is the body. Use it wisely.
"""


def _write_skill_md(skill_dir: Path, content: str = _FRONTMATTER, name: str = "SKILL.md") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / name
    md.write_text(content, encoding="utf-8")
    return md


# ── _as_str_list ─────────────────────────────────────────────────────────────


def test_as_str_list_coerces_items_to_str() -> None:
    """A list is coerced element-by-element to strings."""
    assert _as_str_list([1, "a", 2.5]) == ["1", "a", "2.5"]


@pytest.mark.parametrize("value", [None, "not-a-list", 42, {"k": "v"}])
def test_as_str_list_non_list_returns_empty(value: object) -> None:
    """Anything that is not a list yields an empty list."""
    assert _as_str_list(value) == []


# ── _find_skill_md ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["SKILL.md", "skill.md", "Skill.md"])
def test_find_skill_md_is_case_insensitive(tmp_path: Path, name: str) -> None:
    """The file is found regardless of capitalisation."""
    md = _write_skill_md(tmp_path, name=name)
    assert _find_skill_md(tmp_path) == md


def test_find_skill_md_returns_none_when_absent(tmp_path: Path) -> None:
    """An existing directory without a skill.md returns None."""
    assert _find_skill_md(tmp_path) is None


def test_find_skill_md_returns_none_when_not_a_directory(tmp_path: Path) -> None:
    """A path that is not a directory returns None."""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    assert _find_skill_md(not_a_dir) is None


# ── parse_skill_md ───────────────────────────────────────────────────────────


def test_parse_skill_md_parses_frontmatter(tmp_path: Path) -> None:
    """Valid frontmatter parses into a dict with the expected keys."""
    _write_skill_md(tmp_path)
    meta = parse_skill_md(tmp_path)
    assert meta["name"] == "my-demo-skill"
    assert meta["version"] == "2.1.0"
    assert meta["tags"] == ["utility", "demo"]
    assert meta["safe_to_auto_activate"] is True


def test_parse_skill_md_missing_file_returns_empty(tmp_path: Path) -> None:
    """No skill.md yields an empty dict."""
    assert parse_skill_md(tmp_path) == {}


def test_parse_skill_md_no_frontmatter_fence_returns_empty(tmp_path: Path) -> None:
    """A file that does not start with '---' yields an empty dict."""
    _write_skill_md(tmp_path, content="# Just a heading\nNo frontmatter here.")
    assert parse_skill_md(tmp_path) == {}


def test_parse_skill_md_unterminated_frontmatter_returns_empty(tmp_path: Path) -> None:
    """A frontmatter block without a closing fence yields an empty dict."""
    _write_skill_md(tmp_path, content="---\nname: x\n(no closing fence)\n")
    assert parse_skill_md(tmp_path) == {}


def test_parse_skill_md_invalid_yaml_returns_empty(tmp_path: Path) -> None:
    """Malformed YAML is swallowed and yields an empty dict."""
    _write_skill_md(tmp_path, content="---\n: : : not valid : :\n---\nbody\n")
    assert parse_skill_md(tmp_path) == {}


# ── _skill_md_body ───────────────────────────────────────────────────────────


def test_skill_md_body_returns_text_after_frontmatter(tmp_path: Path) -> None:
    """The body is everything after the closing frontmatter fence."""
    _write_skill_md(tmp_path)
    body = _skill_md_body(tmp_path)
    assert body.startswith("# Demo skill")
    assert "Use it wisely." in body
    assert "name: my-demo-skill" not in body


def test_skill_md_body_missing_file_returns_empty(tmp_path: Path) -> None:
    """No skill.md yields an empty body."""
    assert _skill_md_body(tmp_path) == ""


def test_skill_md_body_without_frontmatter_returns_full_text(tmp_path: Path) -> None:
    """When there is no frontmatter, the entire file is the body."""
    _write_skill_md(tmp_path, content="plain body only")
    assert _skill_md_body(tmp_path) == "plain body only"


def test_skill_md_body_unterminated_frontmatter_returns_full_text(tmp_path: Path) -> None:
    """An unterminated frontmatter fence falls back to the full text."""
    content = "---\nname: x\nstill open"
    _write_skill_md(tmp_path, content=content)
    assert _skill_md_body(tmp_path) == content


# ── _py_class_name ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("my-skill", "MySkill"),
        ("my_skill", "MySkill"),
        ("weather-forecast_v2", "WeatherForecastV2"),
        ("single", "Single"),
    ],
)
def test_py_class_name(raw: str, expected: str) -> None:
    """Snake/kebab case names convert to PascalCase."""
    assert _py_class_name(raw) == expected


# ── generate_skill_py ────────────────────────────────────────────────────────


def test_generate_skill_py_raises_without_skill_md(tmp_path: Path) -> None:
    """Generation without a skill.md raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        generate_skill_py(tmp_path)


def test_generate_skill_py_writes_wrapper(tmp_path: Path) -> None:
    """A skill.py wrapper is written with the parsed metadata baked in."""
    _write_skill_md(tmp_path)
    generate_skill_py(tmp_path)

    skill_py = tmp_path / "skill.py"
    assert skill_py.is_file()
    code = skill_py.read_text(encoding="utf-8")
    assert "class MyDemoSkill(MarkdownSkill):" in code
    assert "name='my-demo-skill'" in code
    assert "version='2.1.0'" in code
    assert "safe_to_auto_activate=True" in code
    # The generated module must be valid Python.
    compile(code, str(skill_py), "exec")


def test_generate_skill_py_uses_defaults_when_frontmatter_minimal(tmp_path: Path) -> None:
    """Absent fields fall back to defaults derived from the directory name."""
    skill_dir = tmp_path / "fallback_skill"
    _write_skill_md(skill_dir, content="---\n{}\n---\nbody\n")
    generate_skill_py(skill_dir)
    code = (skill_dir / "skill.py").read_text(encoding="utf-8")
    assert "class FallbackSkill(MarkdownSkill):" in code
    assert "name='fallback_skill'" in code
    assert "author='unknown'" in code


# ── _make_subprocess_tool ────────────────────────────────────────────────────


def test_make_subprocess_tool_metadata(tmp_path: Path) -> None:
    """The wrapped tool exposes the given name and description."""
    script = tmp_path / "cli.py"
    script.write_text("print('hi')", encoding="utf-8")
    tool = _make_subprocess_tool("demo__cli", str(script), "Run the CLI")
    assert tool.name == "demo__cli"
    assert tool.description == "Run the CLI"


def test_make_subprocess_tool_blocked_when_shell_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With shell execution disabled the tool refuses to run."""
    monkeypatch.setattr(
        "prismal.core.config.get_settings",
        lambda: SimpleNamespace(shell_enabled=False),
    )
    script = tmp_path / "cli.py"
    script.write_text("print('should-not-run')", encoding="utf-8")
    tool = _make_subprocess_tool("demo__cli", str(script), "Run the CLI")
    out = tool.invoke({"args": ""})
    assert "disabled" in out.lower()


def test_make_subprocess_tool_runs_when_shell_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With shell execution enabled the script runs and stdout is returned."""
    monkeypatch.setattr(
        "prismal.core.config.get_settings",
        lambda: SimpleNamespace(shell_enabled=True),
    )
    script = tmp_path / "cli.py"
    script.write_text("print('hello-from-script')", encoding="utf-8")
    tool = _make_subprocess_tool("demo__cli", str(script), "Run the CLI")
    out = tool.invoke({"args": ""})
    assert "hello-from-script" in out


def test_make_subprocess_tool_appends_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stderr produced by the script is appended to the returned output."""
    monkeypatch.setattr(
        "prismal.core.config.get_settings",
        lambda: SimpleNamespace(shell_enabled=True),
    )
    script = tmp_path / "cli.py"
    script.write_text(
        "import sys\nprint('out-line')\nprint('err-line', file=sys.stderr)\n",
        encoding="utf-8",
    )
    tool = _make_subprocess_tool("demo__cli", str(script), "Run the CLI")
    out = tool.invoke({"args": ""})
    assert "out-line" in out
    assert "STDERR:" in out
    assert "err-line" in out


# ── _make_reference_tools ────────────────────────────────────────────────────


def test_make_reference_tools_empty_references_dir_yields_only_guide(tmp_path: Path) -> None:
    """An existing-but-empty references/ dir adds no reader tool."""
    (tmp_path / "references").mkdir()
    tools = _make_reference_tools("demo", tmp_path, skill_md_body="body")
    assert [t.name for t in tools] == ["demo__guide"]


def test_make_reference_tools_empty_when_no_body_no_refs(tmp_path: Path) -> None:
    """No body and no references produce no tools."""
    assert _make_reference_tools("demo", tmp_path, skill_md_body="   ") == []


def test_make_reference_tools_guide_tool(tmp_path: Path) -> None:
    """A non-empty body yields a guide tool returning that body."""
    tools = _make_reference_tools("my-skill", tmp_path, skill_md_body="the guide body")
    assert len(tools) == 1
    guide = tools[0]
    assert guide.name == "my_skill__guide"
    assert guide.invoke({}) == "the guide body"


def test_make_reference_tools_reference_reader(tmp_path: Path) -> None:
    """A populated references/ dir yields a reader that returns file content."""
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("reference content", encoding="utf-8")

    tools = _make_reference_tools("demo", tmp_path, skill_md_body="body")
    names = {t.name for t in tools}
    assert names == {"demo__guide", "demo__read_reference"}

    reader = next(t for t in tools if t.name == "demo__read_reference")
    assert reader.invoke({"filename": "guide.md"}) == "reference content"


def test_make_reference_tools_reader_handles_missing_file(tmp_path: Path) -> None:
    """Reading a nonexistent reference returns a helpful not-found message."""
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("x", encoding="utf-8")

    tools = _make_reference_tools("demo", tmp_path, skill_md_body="")
    reader = next(t for t in tools if t.name == "demo__read_reference")
    out = reader.invoke({"filename": "missing.md"})
    assert "not found" in out.lower()
    assert "guide.md" in out


# ── MarkdownSkill ────────────────────────────────────────────────────────────


def test_markdown_skill_metadata_from_frontmatter(tmp_path: Path) -> None:
    """MarkdownSkill.metadata reflects the parsed frontmatter."""
    _write_skill_md(tmp_path)
    skill = MarkdownSkill(tmp_path)
    meta = skill.metadata
    assert isinstance(meta, SkillMetadata)
    assert meta.name == "my-demo-skill"
    assert meta.tags == ["utility", "demo"]
    assert meta.requires_permissions == ["filesystem.read"]


def test_markdown_skill_metadata_defaults_when_empty(tmp_path: Path) -> None:
    """Without frontmatter, metadata falls back to directory-name defaults."""
    skill_dir = tmp_path / "no_meta"
    _write_skill_md(skill_dir, content="body only, no frontmatter")
    meta = MarkdownSkill(skill_dir).metadata
    assert meta.name == "no_meta"
    assert meta.author == "unknown"


def test_markdown_skill_get_tools_combines_scripts_and_references(tmp_path: Path) -> None:
    """get_tools merges @tool scripts, CLI scripts, guide and reference tools."""
    # Use a named directory: the auto-wrapped CLI tool prefix derives from the
    # directory name, not the frontmatter name.
    skill_root = tmp_path / "my_demo_skill"
    _write_skill_md(skill_root)

    scripts = skill_root / "scripts"
    scripts.mkdir()
    # A script exposing a @tool-decorated function.
    (scripts / "greeter.py").write_text(
        'from langchain_core.tools import tool\n\n\n'
        '@tool\n'
        'def greet(name: str) -> str:\n'
        '    """Greet someone.\n\n'
        '    Args:\n'
        '        name: Person to greet.\n\n'
        '    Returns:\n'
        '        A greeting.\n'
        '    """\n'
        '    return f"hi {name}"\n',
        encoding="utf-8",
    )
    # A plain CLI script (no @tool) -> auto-wrapped as subprocess tool.
    (scripts / "runme.py").write_text('"""A plain CLI."""\nprint("ran")\n', encoding="utf-8")
    # An underscore-prefixed script -> ignored.
    (scripts / "_private.py").write_text("x = 1\n", encoding="utf-8")

    refs = skill_root / "references"
    refs.mkdir()
    (refs / "doc.txt").write_text("ref", encoding="utf-8")

    tool_names = {t.name for t in MarkdownSkill(skill_root).get_tools()}
    assert "greet" in tool_names  # @tool function
    assert "my_demo_skill__runme" in tool_names  # auto-wrapped CLI (dir-name prefix)
    assert "my_demo_skill__guide" in tool_names  # guide tool
    assert "my_demo_skill__read_reference" in tool_names  # reference reader


def test_markdown_skill_load_script_tools_no_scripts_dir(tmp_path: Path) -> None:
    """Without a scripts/ directory, no script tools are produced."""
    _write_skill_md(tmp_path)
    assert MarkdownSkill(tmp_path)._load_script_tools() == []


def test_markdown_skill_load_script_tools_skips_broken_module(tmp_path: Path) -> None:
    """A script that fails to import is skipped, not raised."""
    _write_skill_md(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "broken.py").write_text("this is not valid python ::::", encoding="utf-8")
    # Should not raise, and produces no tool for the broken script.
    assert MarkdownSkill(tmp_path)._load_script_tools() == []


def test_markdown_skill_get_references(tmp_path: Path) -> None:
    """get_references lists files in references/ and is empty when absent."""
    _write_skill_md(tmp_path)
    skill = MarkdownSkill(tmp_path)
    assert skill.get_references() == []

    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "b.md").write_text("b", encoding="utf-8")
    (refs / "a.md").write_text("a", encoding="utf-8")
    result = [p.name for p in skill.get_references()]
    assert result == ["a.md", "b.md"]  # sorted
