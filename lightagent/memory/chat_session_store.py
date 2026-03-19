"""Persistent store for chat session metadata.

Each chat session (identified by its ``session_id``) gets one row with
a LLM-generated title, timestamps, and a message counter.  Uses the
same synchronous sqlite3 pattern as
:class:`~lightagent.scheduler.cron_manager.CronManager`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DB_DEFAULT = Path(__file__).parent.parent.parent / "data" / "db" / "chat_sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    message_count   INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class ChatSession:
    """Metadata snapshot for one chat session."""

    session_id: str
    title: str
    created_at: datetime
    last_active_at: datetime
    message_count: int


def _now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _row_to_session(row: sqlite3.Row) -> ChatSession:
    return ChatSession(
        session_id=row["session_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_active_at=datetime.fromisoformat(row["last_active_at"]),
        message_count=row["message_count"],
    )


class ChatSessionStore:
    """Manages chat session metadata in a local SQLite database.

    Usage::

        store = ChatSessionStore()
        store.create_session("user-abc12345")
        store.update_title("user-abc12345", "Python analysis")
        sessions, total = store.list_sessions(page=1, page_size=10)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise the store, creating the database file if needed.

        Args:
            db_path: Override the default database path. Defaults to
                ``data/db/chat_sessions.db`` relative to the repo root.
        """
        self._db_path = db_path or _DB_DEFAULT
        self._init_db()

    def _init_db(self) -> None:
        """Create the database file and schema if they do not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        """Return a configured sqlite3 connection with Row factory."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_session(self, session_id: str) -> None:
        """Insert a new session row (no-op if it already exists).

        Args:
            session_id: The session identifier.
        """
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO chat_sessions
                    (session_id, title, created_at, last_active_at, message_count)
                VALUES (?, '', ?, ?, 0)
                """,
                (session_id, now, now),
            )

    def update_title(self, session_id: str, title: str) -> None:
        """Set the LLM-generated title for a session.

        Args:
            session_id: Target session.
            title: Short descriptive title (<=80 chars recommended).
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE session_id = ?",
                (title, session_id),
            )

    def increment_message_count(self, session_id: str) -> None:
        """Increment message count and update last_active_at timestamp.

        Args:
            session_id: Target session.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET message_count = message_count + 1,
                    last_active_at = ?
                WHERE session_id = ?
                """,
                (_now_iso(), session_id),
            )

    def get_session(self, session_id: str) -> ChatSession | None:
        """Return metadata for one session, or None if not found.

        Args:
            session_id: The session to look up.

        Returns:
            :class:`ChatSession` or ``None``.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(
        self,
        page: int = 1,
        page_size: int = 10,
        search: str = "",
    ) -> tuple[list[ChatSession], int]:
        """Return a page of sessions ordered by last_active_at descending.

        Args:
            page: 1-based page number.
            page_size: Number of rows per page.
            search: Optional substring to filter on ``title``.

        Returns:
            A ``(sessions, total_count)`` tuple.
        """
        offset = (page - 1) * page_size
        pattern = f"%{search}%" if search else "%"
        with self._conn() as conn:
            total: int = conn.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE title LIKE ?",
                (pattern,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE title LIKE ?
                ORDER BY last_active_at DESC
                LIMIT ? OFFSET ?
                """,
                (pattern, page_size, offset),
            ).fetchall()
        return [_row_to_session(r) for r in rows], total


__all__ = ["ChatSession", "ChatSessionStore"]
