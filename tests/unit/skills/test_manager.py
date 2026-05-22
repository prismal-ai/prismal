"""Unit tests for lightagent.skills.manager (T-071)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prismal.core.exceptions import SkillLoadError, SkillValidationError
from prismal.skills.manager import SkillsManager

# ── Skill stubs ───────────────────────────────────────────────────────────────

MINIMAL_SKILL_PY = """\
from langchain_core.tools import BaseTool, tool
from prismal.skills.base import BaseSkill, SkillMetadata


class _TestSkill(BaseSkill):
    \"\"\"Minimal test skill.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="test_skill",
            description="A test skill",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return tools.\"\"\"

        @tool
        def ping(msg: str) -> str:
            \"\"\"Ping.

            Args:
                msg: message.

            Returns:
                pong response.
            \"\"\"
            return f"pong: {msg}"

        return [ping]
"""

UNSAFE_SKILL_PY = """\
from langchain_core.tools import BaseTool
from prismal.skills.base import BaseSkill, SkillMetadata


class _UnsafeSkill(BaseSkill):
    \"\"\"Skill requiring confirmation.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="unsafe_skill",
            description="Needs confirmation",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=False,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return empty tools.\"\"\"
        return []
"""

INVALID_VALIDATE_SKILL_PY = """\
from langchain_core.tools import BaseTool
from prismal.skills.base import BaseSkill, SkillMetadata


class _BadSkill(BaseSkill):
    \"\"\"Skill that always fails validate.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="bad_skill",
            description="Always fails validate",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return empty tools.\"\"\"
        return []

    def validate(self) -> bool:
        \"\"\"Always return False.\"\"\"
        return False
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_skill_tree(
    available_skills: dict[str, str] | None = None,
    custom_skills: dict[str, str] | None = None,
) -> Path:
    """Build a temporary skills directory tree and return its root path."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "available").mkdir()
    (tmp / "active").mkdir()
    (tmp / "custom").mkdir()

    for name, content in (available_skills or {}).items():
        skill_dir = tmp / "available" / name
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text(content)

    for name, content in (custom_skills or {}).items():
        skill_dir = tmp / "custom" / name
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text(content)

    return tmp


# ── list_skills ───────────────────────────────────────────────────────────────


def test_list_skills_empty() -> None:
    """list_skills returns empty list when no skills exist."""
    root = _make_skill_tree()
    assert SkillsManager(skills_root=root).list_skills() == []


def test_list_skills_discovers_available() -> None:
    """list_skills discovers skills in available/."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    infos = SkillsManager(skills_root=root).list_skills()
    assert len(infos) == 1
    assert infos[0].name == "test_skill"
    assert infos[0].status == "available"


def test_list_skills_discovers_custom() -> None:
    """list_skills discovers skills in custom/ (SkillInfo.name = metadata name)."""
    root = _make_skill_tree(custom_skills={"custom_skill": MINIMAL_SKILL_PY})
    infos = SkillsManager(skills_root=root).list_skills()
    # MINIMAL_SKILL_PY metadata name is "test_skill"
    assert any(i.name == "test_skill" and i.status == "custom" for i in infos)


def test_list_skills_filter_by_status() -> None:
    """list_skills(status='custom') returns only custom skills."""
    root = _make_skill_tree(
        available_skills={"av_skill": MINIMAL_SKILL_PY},
        custom_skills={"cu_skill": MINIMAL_SKILL_PY},
    )
    manager = SkillsManager(skills_root=root)
    custom_infos = manager.list_skills(status="custom")
    assert len(custom_infos) >= 1
    assert all(i.status == "custom" for i in custom_infos)


# ── activate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_creates_symlink() -> None:
    """activate() creates a symlink in active/."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    assert (root / "active" / "test_skill").is_symlink()


@pytest.mark.asyncio
async def test_activate_marks_skill_active() -> None:
    """After activation skill appears in list_skills with status='active'."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    active = manager.list_skills(status="active")
    assert any(i.name == "test_skill" for i in active)


@pytest.mark.asyncio
async def test_activate_twice_is_idempotent() -> None:
    """Calling activate() on an already-active skill is a no-op."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    await manager.activate("test_skill", confirm=True)  # should not raise
    assert len(manager.list_skills(status="active")) == 1


@pytest.mark.asyncio
async def test_activate_unsafe_without_confirm_raises() -> None:
    """safe_to_auto_activate=False without confirm raises SkillValidationError."""
    root = _make_skill_tree(available_skills={"unsafe_skill": UNSAFE_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    with pytest.raises(SkillValidationError):
        await manager.activate("unsafe_skill")


@pytest.mark.asyncio
async def test_activate_unsafe_with_confirm_succeeds() -> None:
    """confirm=True bypasses the confirmation gate."""
    root = _make_skill_tree(available_skills={"unsafe_skill": UNSAFE_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("unsafe_skill", confirm=True)
    assert any(i.name == "unsafe_skill" for i in manager.list_skills(status="active"))


@pytest.mark.asyncio
async def test_activate_validate_false_raises() -> None:
    """Skill whose validate() returns False raises SkillValidationError."""
    root = _make_skill_tree(available_skills={"bad_skill": INVALID_VALIDATE_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    with pytest.raises(SkillValidationError):
        await manager.activate("bad_skill", confirm=True)


@pytest.mark.asyncio
async def test_activate_custom_without_validated_txt_raises() -> None:
    """Custom skill without validated_by_human.txt cannot be activated."""
    root = _make_skill_tree(custom_skills={"my_custom": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    with pytest.raises(SkillValidationError, match="validated_by_human"):
        await manager.activate("my_custom", confirm=True)


@pytest.mark.asyncio
async def test_activate_custom_with_validated_txt_succeeds() -> None:
    """Custom skill with validated_by_human.txt can be activated."""
    root = _make_skill_tree(custom_skills={"my_custom": MINIMAL_SKILL_PY})
    (root / "custom" / "my_custom" / "validated_by_human.txt").write_text("ok")
    manager = SkillsManager(skills_root=root)
    await manager.activate("my_custom", confirm=True)
    # SkillInfo.name comes from metadata ("test_skill"), not directory name
    assert len(manager.list_skills(status="active")) == 1


@pytest.mark.asyncio
async def test_activate_nonexistent_raises_skill_load_error() -> None:
    """Activating a non-existent skill raises SkillLoadError."""
    root = _make_skill_tree()
    manager = SkillsManager(skills_root=root)
    with pytest.raises(SkillLoadError):
        await manager.activate("nonexistent", confirm=True)


# ── deactivate ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_removes_symlink() -> None:
    """deactivate() removes the symlink from active/."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    await manager.deactivate("test_skill")
    assert not (root / "active" / "test_skill").exists()


@pytest.mark.asyncio
async def test_deactivate_removes_from_active_list() -> None:
    """After deactivation skill no longer appears as active."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    await manager.deactivate("test_skill")
    assert manager.list_skills(status="active") == []


@pytest.mark.asyncio
async def test_deactivate_nonexistent_does_not_raise() -> None:
    """deactivate() on unknown skill name is a no-op."""
    root = _make_skill_tree()
    await SkillsManager(skills_root=root).deactivate("nonexistent")


# ── get_active_tools ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_tools_returns_skill_tools() -> None:
    """get_active_tools() returns tools from active skills."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    tools = manager.get_active_tools()
    assert any(t.name == "ping" for t in tools)


def test_get_active_tools_empty_when_none_active() -> None:
    """get_active_tools() returns [] when no skills are active."""
    root = _make_skill_tree()
    assert SkillsManager(skills_root=root).get_active_tools() == []


# ── reload_all ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_all_clears_active_skills() -> None:
    """reload_all() deactivates all currently active skills."""
    root = _make_skill_tree(available_skills={"test_skill": MINIMAL_SKILL_PY})
    manager = SkillsManager(skills_root=root)
    await manager.activate("test_skill", confirm=True)
    # Remove symlink so reload doesn't re-activate
    (root / "active" / "test_skill").unlink()
    await manager.reload_all()
    assert manager.get_active_tools() == []


# ── isolation (AC-006-9) ──────────────────────────────────────────────────────


def test_crashing_skill_does_not_crash_list_skills() -> None:
    """A skill.py that raises on import is marked 'error'; others still listed."""
    root = _make_skill_tree(
        available_skills={
            "good_skill": MINIMAL_SKILL_PY,
            "bad_import": "raise RuntimeError('broken on import')\n",
        }
    )
    manager = SkillsManager(skills_root=root)
    infos = manager.list_skills()
    statuses = {i.name: i.status for i in infos}
    # MINIMAL_SKILL_PY metadata name is "test_skill"; bad_import dir has no class
    assert statuses.get("test_skill") == "available"
    assert statuses.get("bad_import") == "error"
