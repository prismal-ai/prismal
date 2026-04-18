"""Append-only markdown conversation history writer.

Writes each agent exchange to a dated Markdown file at
``data/workspace/conversations/{session_id}.md``.

The file format is::

    ---
    session_id: cli-a1b2c3
    created_at: 2026-03-09T20:46:06
    channels: [cli, telegram]
    ---

    ## 2026-03-09T20:46:10 · User · cli

    Hello, world.

    ---

    ## 2026-03-09T20:46:12 · Agent · cli

    Hi there!

    ---
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger("lightagent.memory.conversation_history")

_DEFAULT_BASE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "workspace" / "conversations"
)


class ConversationHistory:
    """Append-only markdown log for a single LangGraph session.

    Each call to :meth:`append` adds one turn to the file.  If the file
    does not yet exist it is created with a YAML front-matter header.
    Write errors are caught and logged — they never raise to the caller.

    Args:
        session_id: The LangGraph ``thread_id`` / session identifier.
        base_dir: Directory where history files are stored.  Defaults to
            ``data/workspace/conversations``.
    """

    def __init__(
        self,
        session_id: str,
        base_dir: Path | None = None,
    ) -> None:
        """Initialise with session ID and optional base directory."""
        self._session_id = session_id
        self._base_dir = base_dir or _DEFAULT_BASE

    def path(self) -> Path:
        """Return the absolute path to this session's markdown file.

        Returns:
            A :class:`~pathlib.Path` of the form
            ``{base_dir}/{session_id}.md``.
        """
        return self._base_dir / f"{self._session_id}.md"

    def read(self) -> str:
        """Return the full markdown content of the history file.

        Returns:
            File contents as a string, or ``""`` if the file does not exist.
        """
        p = self.path()
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")

    def append(
        self,
        role: Literal["User", "Agent"],
        text: str,
        channel: str = "cli",
    ) -> None:
        """Append one conversation turn to the markdown file.

        Creates the file with YAML front-matter on first call.  Updates
        the ``channels`` list in front-matter when a new channel is seen.
        Write errors are logged as warnings and silently swallowed.

        Args:
            role: ``"User"`` or ``"Agent"``.
            text: Message text to record.
            channel: Source channel name (``"cli"``, ``"telegram"``, etc.).
        """
        try:
            p = self.path()
            p.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

            if not p.exists():
                header = (
                    "---\n"
                    f"session_id: {self._session_id}\n"
                    f"created_at: {now}\n"
                    f"channels: [{channel}]\n"
                    "---\n\n"
                )
                p.write_text(header, encoding="utf-8")
            else:
                self._ensure_channel_in_header(p, channel)

            entry = f"## {now} · {role} · {channel}\n\n{text.strip()}\n\n---\n\n"
            with p.open("a", encoding="utf-8") as f:
                f.write(entry)

            logger.debug(
                "conversation_history.appended",
                session_id=self._session_id,
                role=role,
                channel=channel,
            )
        except Exception as exc:  # intentional: spec says "do not crash"
            logger.warning(
                "conversation_history.write_error",
                session_id=self._session_id,
                error=str(exc),
            )

    def _ensure_channel_in_header(self, p: Path, channel: str) -> None:
        """Add *channel* to the ``channels:`` list in the front-matter if absent.

        Args:
            p: Path to the history file (must exist).
            channel: Channel name to add.
        """
        content = p.read_text(encoding="utf-8")
        if "channels:" not in content:
            return
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("channels:") and channel not in line:
                if line.endswith("]"):
                    lines[i] = line[:-1] + f", {channel}]"
                else:
                    lines[i] = line + f", {channel}]"
                p.write_text("\n".join(lines), encoding="utf-8")
                break


__all__ = ["ConversationHistory"]
