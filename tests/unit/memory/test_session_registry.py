"""Unit tests for SessionRegistry."""

from __future__ import annotations

from pathlib import Path

from lightagent.memory.session_registry import SessionRegistry


class TestPersistentSession:
    """Tests for persistent CLI/Dashboard session management."""

    def test_creates_file_on_first_call(self, tmp_path: Path) -> None:
        """get_persistent_session creates the session file if absent."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        sid = reg.get_persistent_session()
        assert (tmp_path / "session_id").exists()
        assert sid.startswith("user-")

    def test_returns_same_id_on_second_call(self, tmp_path: Path) -> None:
        """get_persistent_session returns the same ID across instances."""
        reg1 = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        sid1 = reg1.get_persistent_session()
        reg2 = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        sid2 = reg2.get_persistent_session()
        assert sid1 == sid2


class TestChannelLinking:
    """Tests for cross-channel session linking."""

    def test_lookup_returns_none_when_not_linked(self, tmp_path: Path) -> None:
        """lookup returns None for an unknown (channel, user_id) pair."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        assert reg.lookup("telegram", "99999") is None

    def test_link_and_lookup_roundtrip(self, tmp_path: Path) -> None:
        """link stores mapping; lookup retrieves it."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        reg.link("telegram", "12345", "user-abc")
        assert reg.lookup("telegram", "12345") == "user-abc"

    def test_unlink_removes_mapping(self, tmp_path: Path) -> None:
        """unlink removes the stored mapping."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        reg.link("telegram", "12345", "user-abc")
        reg.unlink("telegram", "12345")
        assert reg.lookup("telegram", "12345") is None

    def test_link_overwrites_existing(self, tmp_path: Path) -> None:
        """Calling link twice updates the session_id."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        reg.link("telegram", "12345", "user-abc")
        reg.link("telegram", "12345", "user-xyz")
        assert reg.lookup("telegram", "12345") == "user-xyz"

    def test_unlink_nonexistent_is_noop(self, tmp_path: Path) -> None:
        """unlink on a non-existent mapping does not raise."""
        reg = SessionRegistry(profile_dir=tmp_path, db_path=tmp_path / "s.db")
        reg.unlink("telegram", "nonexistent")  # must not raise
