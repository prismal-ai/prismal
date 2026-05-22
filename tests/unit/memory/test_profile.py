"""Unit tests for prismal.memory.profile.ProfileManager."""

from __future__ import annotations

from pathlib import Path

from prismal.memory.profile import ProfileManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager(tmp_path: Path) -> ProfileManager:
    """Return a ProfileManager pointing to a temp directory."""
    return ProfileManager(profile_dir=tmp_path / "profile")


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_is_configured_false_when_no_files(tmp_path: Path) -> None:
    """is_configured() returns False when neither SOUL.md nor USER.md exists."""
    pm = _manager(tmp_path)
    assert pm.is_configured() is False


def test_is_configured_false_when_only_soul(tmp_path: Path) -> None:
    """is_configured() returns False when only SOUL.md exists."""
    pm = _manager(tmp_path)
    pm._dir.mkdir(parents=True, exist_ok=True)
    pm._soul.write_text("# Agent\n", encoding="utf-8")
    assert pm.is_configured() is False


def test_is_configured_false_when_only_user(tmp_path: Path) -> None:
    """is_configured() returns False when only USER.md exists."""
    pm = _manager(tmp_path)
    pm._dir.mkdir(parents=True, exist_ok=True)
    pm._user.write_text("# User\n", encoding="utf-8")
    assert pm.is_configured() is False


def test_is_configured_true_when_both_files_exist(tmp_path: Path) -> None:
    """is_configured() returns True when both SOUL.md and USER.md exist."""
    pm = _manager(tmp_path)
    pm.save_soul("Lumi", "Helpful assistant.")
    pm.save_user("Ernesto")
    assert pm.is_configured() is True


# ---------------------------------------------------------------------------
# save_soul / load_agent_name / load_soul
# ---------------------------------------------------------------------------


def test_save_soul_creates_soul_md(tmp_path: Path) -> None:
    """save_soul() creates SOUL.md in the configured directory."""
    pm = _manager(tmp_path)
    pm.save_soul("Lumi", "Friendly assistant.")
    assert pm._soul.exists()


def test_load_agent_name_returns_name_from_soul_md(tmp_path: Path) -> None:
    """load_agent_name() extracts the H1 name from SOUL.md."""
    pm = _manager(tmp_path)
    pm.save_soul("Lumi", "Friendly assistant.")
    assert pm.load_agent_name() == "Lumi"


def test_load_agent_name_default_when_no_soul_md(tmp_path: Path) -> None:
    """load_agent_name() returns 'Prismal' when SOUL.md does not exist."""
    pm = _manager(tmp_path)
    assert pm.load_agent_name() == "Prismal"


def test_load_agent_name_default_when_no_h1(tmp_path: Path) -> None:
    """load_agent_name() returns 'Prismal' when SOUL.md has no H1 heading."""
    pm = _manager(tmp_path)
    pm._dir.mkdir(parents=True, exist_ok=True)
    pm._soul.write_text("No heading here.\n", encoding="utf-8")
    assert pm.load_agent_name() == "Prismal"


def test_load_soul_returns_full_content(tmp_path: Path) -> None:
    """load_soul() returns the full text of SOUL.md."""
    pm = _manager(tmp_path)
    pm.save_soul("Lumi", "Friendly assistant.")
    content = pm.load_soul()
    assert "Lumi" in content
    assert "Friendly assistant." in content


def test_load_soul_returns_empty_string_when_missing(tmp_path: Path) -> None:
    """load_soul() returns '' when SOUL.md does not exist."""
    pm = _manager(tmp_path)
    assert pm.load_soul() == ""


def test_save_soul_creates_parent_dirs(tmp_path: Path) -> None:
    """save_soul() creates the profile directory hierarchy if it does not exist."""
    pm = ProfileManager(profile_dir=tmp_path / "deep" / "nested" / "profile")
    pm.save_soul("Bot", "A bot.")
    assert pm._soul.exists()


# ---------------------------------------------------------------------------
# save_user / load_user_name / load_user_context
# ---------------------------------------------------------------------------


def test_save_user_creates_user_md(tmp_path: Path) -> None:
    """save_user() creates USER.md in the configured directory."""
    pm = _manager(tmp_path)
    pm.save_user("Ernesto")
    assert pm._user.exists()


def test_load_user_name_returns_name_from_user_md(tmp_path: Path) -> None:
    """load_user_name() extracts the H1 name from USER.md."""
    pm = _manager(tmp_path)
    pm.save_user("Ernesto")
    assert pm.load_user_name() == "Ernesto"


def test_load_user_name_default_when_no_user_md(tmp_path: Path) -> None:
    """load_user_name() returns 'You' when USER.md does not exist."""
    pm = _manager(tmp_path)
    assert pm.load_user_name() == "You"


def test_load_user_name_default_when_no_h1(tmp_path: Path) -> None:
    """load_user_name() returns 'You' when USER.md has no H1 heading."""
    pm = _manager(tmp_path)
    pm._dir.mkdir(parents=True, exist_ok=True)
    pm._user.write_text("No heading here.\n", encoding="utf-8")
    assert pm.load_user_name() == "You"


def test_load_user_context_returns_full_content(tmp_path: Path) -> None:
    """load_user_context() returns the full text of USER.md."""
    pm = _manager(tmp_path)
    pm.save_user("Ernesto")
    content = pm.load_user_context()
    assert "Ernesto" in content


def test_load_user_context_returns_empty_string_when_missing(tmp_path: Path) -> None:
    """load_user_context() returns '' when USER.md does not exist."""
    pm = _manager(tmp_path)
    assert pm.load_user_context() == ""


def test_save_user_creates_parent_dirs(tmp_path: Path) -> None:
    """save_user() creates the profile directory hierarchy if it does not exist."""
    pm = ProfileManager(profile_dir=tmp_path / "deep" / "nested" / "profile")
    pm.save_user("Alice")
    assert pm._user.exists()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_full_roundtrip(tmp_path: Path) -> None:
    """save_soul + save_user followed by all loaders returns correct values."""
    pm = _manager(tmp_path)
    pm.save_soul("Atlas", "Analytical and precise assistant focused on data.")
    pm.save_user("Carlos")

    assert pm.is_configured() is True
    assert pm.load_agent_name() == "Atlas"
    assert pm.load_user_name() == "Carlos"
    assert "Atlas" in pm.load_soul()
    assert "Carlos" in pm.load_user_context()
    assert "Analytical" in pm.load_soul()


# ---------------------------------------------------------------------------
# Default profile dir
# ---------------------------------------------------------------------------


def test_default_profile_dir_is_data_workspace_profile() -> None:
    """ProfileManager() uses data/workspace/profile as the default directory."""
    pm = ProfileManager()
    assert str(pm._dir) == "data/workspace/profile"


# ---------------------------------------------------------------------------
# Memory module export
# ---------------------------------------------------------------------------


def test_profile_manager_exported_from_memory_package() -> None:
    """ProfileManager is importable from prismal.memory."""
    from prismal.memory import ProfileManager as PM

    assert PM is ProfileManager
