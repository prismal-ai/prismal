"""Unit tests for ConversationHistory."""
from __future__ import annotations

from pathlib import Path

from lightagent.memory.conversation_history import ConversationHistory


class TestConversationHistory:
    """Tests for ConversationHistory append-only markdown writer."""

    def test_creates_file_on_first_append(self, tmp_path: Path) -> None:
        """File is created when it does not exist."""
        h = ConversationHistory("sess-001", base_dir=tmp_path)
        h.append("User", "Hello", channel="cli")
        assert h.path().exists()

    def test_file_has_yaml_frontmatter(self, tmp_path: Path) -> None:
        """File starts with --- YAML front-matter block."""
        h = ConversationHistory("sess-001", base_dir=tmp_path)
        h.append("User", "Hello", channel="cli")
        content = h.path().read_text()
        assert content.startswith("---")
        assert "session_id: sess-001" in content

    def test_append_adds_role_and_text(self, tmp_path: Path) -> None:
        """Appended entry contains role and text."""
        h = ConversationHistory("sess-002", base_dir=tmp_path)
        h.append("User", "What time is it?", channel="cli")
        h.append("Agent", "It is 9 AM.", channel="cli")
        content = h.path().read_text()
        assert "· User ·" in content
        assert "What time is it?" in content
        assert "· Agent ·" in content
        assert "It is 9 AM." in content

    def test_append_is_cumulative(self, tmp_path: Path) -> None:
        """Multiple appends grow the file; do not overwrite."""
        h = ConversationHistory("sess-003", base_dir=tmp_path)
        h.append("User", "First", channel="cli")
        h.append("User", "Second", channel="cli")
        content = h.path().read_text()
        assert "First" in content
        assert "Second" in content

    def test_read_returns_empty_string_if_absent(self, tmp_path: Path) -> None:
        """read() returns '' when the file does not exist."""
        h = ConversationHistory("never-written", base_dir=tmp_path)
        assert h.read() == ""

    def test_channels_header_updated(self, tmp_path: Path) -> None:
        """channels list in front-matter includes all channels used."""
        h = ConversationHistory("sess-004", base_dir=tmp_path)
        h.append("User", "Via CLI", channel="cli")
        h.append("User", "Via Telegram", channel="telegram")
        content = h.path().read_text()
        channels_line = next(
            (line for line in content.splitlines() if line.startswith("channels:")), ""
        )
        assert "cli" in channels_line
        assert "telegram" in channels_line

    def test_write_failure_does_not_raise(self, tmp_path: Path) -> None:
        """append() swallows IOError so the caller is never crashed."""
        h = ConversationHistory("sess-005", base_dir=Path("/nonexistent/path"))
        # Should not raise
        h.append("User", "Hello")

    def test_channels_no_duplicate(self, tmp_path: Path) -> None:
        """Appending the same channel twice does not create duplicates."""
        h = ConversationHistory("sess-006", base_dir=tmp_path)
        h.append("User", "First", channel="cli")
        h.append("User", "Second", channel="cli")
        content = h.path().read_text()
        channels_line = next(
            (line for line in content.splitlines() if line.startswith("channels:")), ""
        )
        assert channels_line.count("cli") == 1
