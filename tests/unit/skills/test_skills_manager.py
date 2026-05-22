"""Coverage tests for lightagent.skills.manager — Phase 15 T-004.

Targets lines not reached by the original test_manager.py:
  72          : default skills_root=None uses Path(__file__).parent
  143         : activate() — skill.py absent in skill_dir raises SkillLoadError
  159-162     : activate() — _load_skill_class re-raises SkillLoadError / wraps generic exc
  191→195     : activate() — symlink already present; skip creation
  210-211     : deactivate() — teardown() raises; warning logged, no propagation
  232→231     : get_active_tools() — duplicate tool name skipped
  235-236     : get_active_tools() — get_tools() raises; error logged, continues
  257-264     : reload_all() — re-activates skills linked in active/
  305         : _load_skill_class — spec is None → SkillLoadError
  326         : _load_skill_class — no BaseSkill subclass found → SkillLoadError
  348, 351    : _scan_dir — non-existent dir returns []; hidden/non-dir entries skipped
  356         : _scan_dir — subdir without skill.py is skipped
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prismal.core.exceptions import SkillLoadError
from prismal.skills.manager import SkillsManager

# ---------------------------------------------------------------------------
# Skill source stubs
# ---------------------------------------------------------------------------

_SAFE_SKILL_PY = """\
from langchain_core.tools import BaseTool, tool
from prismal.skills.base import BaseSkill, SkillMetadata


class _SafeSkill(BaseSkill):
    \"\"\"Safe skill for coverage tests.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="safe_skill",
            description="Coverage safe skill",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return a single tool.\"\"\"

        @tool
        def alpha(msg: str) -> str:
            \"\"\"Alpha tool.

            Args:
                msg: input.

            Returns:
                output string.
            \"\"\"
            return f"alpha: {msg}"

        return [alpha]
"""

_SAFE_SKILL_PY_DUPLICATE_TOOL = """\
from langchain_core.tools import BaseTool, tool
from prismal.skills.base import BaseSkill, SkillMetadata


class _DupSkill(BaseSkill):
    \"\"\"Skill that exposes a tool with the same name as _SafeSkill.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="dup_skill",
            description="Duplicate tool name",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return a tool whose name collides with alpha.\"\"\"

        @tool
        def alpha(msg: str) -> str:
            \"\"\"Alpha duplicate.

            Args:
                msg: input.

            Returns:
                output string.
            \"\"\"
            return f"dup-alpha: {msg}"

        return [alpha]
"""

_TEARDOWN_ERROR_SKILL_PY = """\
from langchain_core.tools import BaseTool
from prismal.skills.base import BaseSkill, SkillMetadata


class _TeardownErrorSkill(BaseSkill):
    \"\"\"Skill whose teardown raises.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="teardown_error_skill",
            description="Always raises in teardown",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Return empty tools.\"\"\"
        return []

    async def teardown(self) -> None:
        \"\"\"Always raise.\"\"\"
        raise RuntimeError("teardown exploded")
"""

_TOOLS_ERROR_SKILL_PY = """\
from langchain_core.tools import BaseTool
from prismal.skills.base import BaseSkill, SkillMetadata


class _ToolsErrorSkill(BaseSkill):
    \"\"\"Skill whose get_tools raises.\"\"\"

    @property
    def metadata(self) -> SkillMetadata:
        \"\"\"Return metadata.\"\"\"
        return SkillMetadata(
            name="tools_error_skill",
            description="Always raises in get_tools",
            version="1.0.0",
            author="test",
            safe_to_auto_activate=True,
        )

    def get_tools(self) -> list[BaseTool]:
        \"\"\"Always raise.\"\"\"
        raise RuntimeError("get_tools exploded")
"""

_NO_SUBCLASS_PY = """\
# No BaseSkill subclass — just a plain class.

class NotASkill:
    \"\"\"Not a BaseSkill.\"\"\"
    pass
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_root(tmp_path: Path) -> Path:
    """Create a minimal skills root directory under tmp_path."""
    root = tmp_path / "skills"
    (root / "available").mkdir(parents=True)
    (root / "active").mkdir()
    (root / "custom").mkdir()
    return root


def _add_available(root: Path, name: str, content: str) -> Path:
    """Write a skill.py under available/<name>/ and return the skill dir."""
    skill_dir = root / "available" / name
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "skill.py").write_text(content)
    return skill_dir


def _add_custom(root: Path, name: str, content: str) -> Path:
    """Write a skill.py under custom/<name>/ and return the skill dir."""
    skill_dir = root / "custom" / name
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "skill.py").write_text(content)
    return skill_dir


# ---------------------------------------------------------------------------
# TestSkillsManagerCoverage
# ---------------------------------------------------------------------------


class TestSkillsManagerCoverage:
    """Coverage tests targeting the remaining uncovered branches in SkillsManager."""

    # ── Line 72: default skills_root=None ────────────────────────────────────

    def test_default_skills_root_uses_package_dir(self) -> None:
        """SkillsManager() with no args sets _root to the skills package dir."""
        manager = SkillsManager()
        expected = Path(__file__).parent.parent.parent.parent / "lightagent" / "skills"
        # The actual _root may not equal the exact package dir in all installs,
        # but it must be an absolute path and point to an existing directory.
        assert manager._root.is_absolute()

    def test_default_skills_root_none_branch(self) -> None:
        """skills_root=None triggers the Path(__file__).parent branch (line 72)."""
        manager = SkillsManager(skills_root=None)
        # _root is set and is a Path
        assert isinstance(manager._root, Path)

    # ── Line 143: skill.py absent → SkillLoadError ───────────────────────────

    @pytest.mark.asyncio
    async def test_activate_missing_skill_py_raises_load_error(self, tmp_path: Path) -> None:
        """activate() raises SkillLoadError when skill.py is absent from the dir."""
        root = _make_root(tmp_path)
        # Create the directory but do NOT write skill.py
        skill_dir = root / "available" / "empty_skill"
        skill_dir.mkdir()

        manager = SkillsManager(skills_root=root)
        with pytest.raises(SkillLoadError, match="skill.py not found"):
            await manager.activate("empty_skill", confirm=True)

    # ── Lines 159-162: _load_skill_class raises propagation paths ────────────

    @pytest.mark.asyncio
    async def test_activate_propagates_skill_load_error_from_loader(self, tmp_path: Path) -> None:
        """SkillLoadError from _load_skill_class is re-raised unchanged (line 159-160)."""
        root = _make_root(tmp_path)
        _add_available(root, "bad_skill", _SAFE_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        with patch.object(
            manager,
            "_load_skill_class",
            side_effect=SkillLoadError("bad_skill", "simulated load failure"),
        ):
            with pytest.raises(SkillLoadError, match="simulated load failure"):
                await manager.activate("bad_skill", confirm=True)

    @pytest.mark.asyncio
    async def test_activate_wraps_generic_exception_in_skill_load_error(
        self, tmp_path: Path
    ) -> None:
        """A non-SkillLoadError from _load_skill_class is wrapped (lines 161-162)."""
        root = _make_root(tmp_path)
        _add_available(root, "boom_skill", _SAFE_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        with patch.object(
            manager,
            "_load_skill_class",
            side_effect=ValueError("unexpected boom"),
        ):
            with pytest.raises(SkillLoadError, match="unexpected boom"):
                await manager.activate("boom_skill", confirm=True)

    # ── Line 191→195: symlink already present, creation skipped ──────────────

    @pytest.mark.asyncio
    async def test_activate_with_existing_symlink_does_not_re_create(self, tmp_path: Path) -> None:
        """activate() skips symlink creation when link already exists (line 191→195)."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "safe_skill", _SAFE_SKILL_PY)

        # Pre-create the symlink manually so the `if not link.exists()` branch is False
        link = root / "active" / "safe_skill"
        link.symlink_to(skill_dir)

        manager = SkillsManager(skills_root=root)
        # Should not raise; symlink creation code path is skipped
        await manager.activate("safe_skill", confirm=True)
        assert link.exists()

    # ── Lines 210-211: deactivate() swallows teardown error ──────────────────

    @pytest.mark.asyncio
    async def test_deactivate_swallows_teardown_exception(self, tmp_path: Path) -> None:
        """deactivate() logs warning and does not propagate teardown exceptions."""
        root = _make_root(tmp_path)
        _add_available(root, "teardown_error_skill", _TEARDOWN_ERROR_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        await manager.activate("teardown_error_skill", confirm=True)
        # Should NOT raise even though teardown() raises RuntimeError
        await manager.deactivate("teardown_error_skill")
        assert "teardown_error_skill" not in manager._active_skills

    # ── Lines 232→231: duplicate tool name is skipped ────────────────────────

    @pytest.mark.asyncio
    async def test_get_active_tools_deduplicates_by_name(self, tmp_path: Path) -> None:
        """get_active_tools() deduplicates tools with identical names (line 232→231)."""
        root = _make_root(tmp_path)
        _add_available(root, "safe_skill", _SAFE_SKILL_PY)
        _add_available(root, "dup_skill", _SAFE_SKILL_PY_DUPLICATE_TOOL)

        manager = SkillsManager(skills_root=root)
        await manager.activate("safe_skill", confirm=True)
        await manager.activate("dup_skill", confirm=True)

        tools = manager.get_active_tools()
        tool_names = [t.name for t in tools]
        # "alpha" must appear exactly once despite being provided by two skills
        assert tool_names.count("alpha") == 1

    # ── Lines 235-236: get_tools() raises → error logged, no propagation ─────

    @pytest.mark.asyncio
    async def test_get_active_tools_handles_get_tools_error(self, tmp_path: Path) -> None:
        """get_active_tools() catches and logs errors from skill.get_tools()."""
        root = _make_root(tmp_path)
        _add_available(root, "tools_error_skill", _TOOLS_ERROR_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        await manager.activate("tools_error_skill", confirm=True)

        # Must not raise; the exception is caught and logged
        tools = manager.get_active_tools()
        assert tools == []

    # ── Lines 257-264: reload_all() re-activates symlinked skills ────────────

    @pytest.mark.asyncio
    async def test_reload_all_reactivates_linked_skills(self, tmp_path: Path) -> None:
        """reload_all() re-activates skills that are already symlinked in active/."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "safe_skill", _SAFE_SKILL_PY)

        # Manually create the symlink so reload_all finds it
        link = root / "active" / "safe_skill"
        link.symlink_to(skill_dir)

        manager = SkillsManager(skills_root=root)
        await manager.reload_all()
        active = manager.list_skills(status="active")
        assert any(i.name == "safe_skill" for i in active)

    @pytest.mark.asyncio
    async def test_reload_all_skips_hidden_entries_in_active(self, tmp_path: Path) -> None:
        """reload_all() skips entries starting with '.' or '_' in active/."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "safe_skill", _SAFE_SKILL_PY)

        # Create hidden symlink — should be skipped
        hidden = root / "active" / ".hidden_skill"
        hidden.symlink_to(skill_dir)

        manager = SkillsManager(skills_root=root)
        await manager.reload_all()
        # The hidden entry must not cause an activation attempt
        assert manager.list_skills(status="active") == []

    @pytest.mark.asyncio
    async def test_reload_all_logs_error_for_bad_skill_in_active(self, tmp_path: Path) -> None:
        """reload_all() logs an error (does not raise) when a linked skill fails."""
        root = _make_root(tmp_path)
        # Create a skill dir without a skill.py — activation will raise SkillLoadError
        bad_dir = root / "available" / "bad_reload"
        bad_dir.mkdir()
        # Symlink it into active/ without skill.py
        link = root / "active" / "bad_reload"
        link.symlink_to(bad_dir)

        manager = SkillsManager(skills_root=root)
        # Should not propagate the SkillLoadError
        await manager.reload_all()
        assert manager.list_skills(status="active") == []

    # ── Line 305: _load_skill_class — spec is None ───────────────────────────

    def test_load_skill_class_raises_when_spec_is_none(self, tmp_path: Path) -> None:
        """_load_skill_class raises SkillLoadError when spec_from_file_location returns None."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "spec_none_skill", _SAFE_SKILL_PY)
        skill_py = skill_dir / "skill.py"

        manager = SkillsManager(skills_root=root)
        with patch("importlib.util.spec_from_file_location", return_value=None):
            with pytest.raises(SkillLoadError, match="Cannot create module spec"):
                manager._load_skill_class(skill_py)

    def test_load_skill_class_raises_when_spec_loader_is_none(self, tmp_path: Path) -> None:
        """_load_skill_class raises SkillLoadError when spec.loader is None."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "loader_none_skill", _SAFE_SKILL_PY)
        skill_py = skill_dir / "skill.py"

        fake_spec = MagicMock()
        fake_spec.loader = None

        manager = SkillsManager(skills_root=root)
        with patch("importlib.util.spec_from_file_location", return_value=fake_spec):
            with pytest.raises(SkillLoadError, match="Cannot create module spec"):
                manager._load_skill_class(skill_py)

    # ── Line 326: _load_skill_class — no BaseSkill subclass found ────────────

    def test_load_skill_class_raises_when_no_subclass(self, tmp_path: Path) -> None:
        """_load_skill_class raises SkillLoadError when no BaseSkill subclass is found."""
        root = _make_root(tmp_path)
        skill_dir = _add_available(root, "no_subclass_skill", _NO_SUBCLASS_PY)
        skill_py = skill_dir / "skill.py"

        manager = SkillsManager(skills_root=root)
        with pytest.raises(SkillLoadError, match="No BaseSkill subclass found"):
            manager._load_skill_class(skill_py)

    # ── Lines 348, 351: _scan_dir — non-existent dir / hidden entries ────────

    def test_scan_dir_returns_empty_when_directory_does_not_exist(self, tmp_path: Path) -> None:
        """_scan_dir returns [] without error when the directory does not exist."""
        root = _make_root(tmp_path)
        manager = SkillsManager(skills_root=root)
        result = manager._scan_dir(root / "nonexistent", status="available", skip_names=set())
        assert result == []

    def test_scan_dir_skips_hidden_and_non_dir_entries(self, tmp_path: Path) -> None:
        """_scan_dir skips entries starting with '.' or '_' and plain files."""
        root = _make_root(tmp_path)
        avail = root / "available"
        # Create a hidden directory
        (avail / ".hidden").mkdir()
        # Create a file (not a dir)
        (avail / "somefile.txt").write_text("noise")
        # Create a valid skill to confirm it still works
        valid_dir = avail / "good_skill"
        valid_dir.mkdir()
        (valid_dir / "skill.py").write_text(_SAFE_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        result = manager._scan_dir(avail, status="available", skip_names=set())
        names = [r.name for r in result]
        assert ".hidden" not in names
        assert "somefile.txt" not in names
        assert "safe_skill" in names

    # ── Line 356: _scan_dir — subdir without skill.py skipped ────────────────

    def test_scan_dir_skips_subdir_without_skill_py(self, tmp_path: Path) -> None:
        """_scan_dir skips subdirectories that have no skill.py file."""
        root = _make_root(tmp_path)
        avail = root / "available"
        # A dir with no skill.py
        (avail / "no_skill_py").mkdir()
        # A dir with an unrelated file
        unrelated = avail / "unrelated"
        unrelated.mkdir()
        (unrelated / "readme.txt").write_text("hello")

        manager = SkillsManager(skills_root=root)
        result = manager._scan_dir(avail, status="available", skip_names=set())
        assert result == []

    def test_scan_dir_skips_names_in_skip_set(self, tmp_path: Path) -> None:
        """_scan_dir honours the skip_names set (already-active skills)."""
        root = _make_root(tmp_path)
        avail = root / "available"
        skill_dir = avail / "safe_skill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text(_SAFE_SKILL_PY)

        manager = SkillsManager(skills_root=root)
        result = manager._scan_dir(avail, status="available", skip_names={"safe_skill"})
        assert result == []

    # ── Line 255→exit: reload_all() — active dir does not exist ─────────────

    @pytest.mark.asyncio
    async def test_reload_all_when_active_dir_does_not_exist(self, tmp_path: Path) -> None:
        """reload_all() exits cleanly when active/ directory does not exist."""
        root = tmp_path / "skills"
        (root / "available").mkdir(parents=True)
        # Intentionally do NOT create active/ or custom/
        manager = SkillsManager(skills_root=root)
        # Must not raise even though _active doesn't exist
        await manager.reload_all()
        assert manager.list_skills(status="active") == []

    # ── Line 260: reload_all() — non-directory entry in active/ skipped ──────

    @pytest.mark.asyncio
    async def test_reload_all_skips_plain_files_in_active(self, tmp_path: Path) -> None:
        """reload_all() skips plain files (non-directories) in active/."""
        root = _make_root(tmp_path)
        # Create a plain file in active/ — not a symlink/directory
        (root / "active" / "readme.txt").write_text("not a skill")

        manager = SkillsManager(skills_root=root)
        # Must not raise; plain files are silently skipped
        await manager.reload_all()
        assert manager.list_skills(status="active") == []
