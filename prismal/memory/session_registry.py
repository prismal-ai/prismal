"""Persistent session ID management and cross-channel session linking.

Two responsibilities:

1. **Persistent CLI/Dashboard session** — reads/writes
   ``data/workspace/profile/session_id`` so the same LangGraph thread
   is used across CLI restarts and the Dashboard.

2. **Channel linking** — stores ``(channel, user_id) → session_id``
   mappings in SQLite so Telegram (and other) users can be linked to
   the same session as the CLI user via a ``/link`` command.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog

logger = structlog.get_logger("prismal.memory.session_registry")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROFILE_DIR = _REPO_ROOT / "data" / "workspace" / "profile"
_DEFAULT_DB = _REPO_ROOT / "data" / "db" / "cron_jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_sessions (
    channel    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (channel, user_id)
);
"""


class SessionRegistry:
    """Manages persistent session IDs and cross-channel session links.

    Args:
        profile_dir: Directory containing the ``session_id`` file.
            Defaults to ``data/workspace/profile`` (relative to repo root).
        db_path: Path to the SQLite database for channel_sessions table.
            Defaults to ``data/db/cron_jobs.db`` (relative to repo root).
    """

    def __init__(
        self,
        profile_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        """Initialise registry paths and ensure the DB schema exists."""
        self._profile_dir = profile_dir or _DEFAULT_PROFILE_DIR
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        """Create the channel_sessions table if it does not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        """Return a configured SQLite connection with row_factory set."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Persistent CLI/Dashboard session ──────────────────────────────────

    @property
    def _session_file(self) -> Path:
        """Return the path to the persistent session ID file."""
        return self._profile_dir / "session_id"

    def get_persistent_session(self) -> str:
        """Return the persistent session ID, creating it if absent.

        The ID is stored in ``{profile_dir}/session_id``.  On first call
        a ``user-{uuid8}`` ID is generated and written.

        Returns:
            Session ID string, e.g. ``"user-a1b2c3d4"``.
        """
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        if self._session_file.exists():
            sid = self._session_file.read_text(encoding="utf-8").strip()
            if sid:
                return sid
        sid = f"user-{uuid.uuid4().hex[:8]}"
        self._session_file.write_text(sid, encoding="utf-8")
        logger.info("session_registry.created_persistent_session", session_id=sid)
        return sid

    def set_persistent_session(self, session_id: str) -> None:
        """Overwrite the persistent session file with a new session ID.

        Args:
            session_id: The new session ID to persist.
        """
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        (self._profile_dir / "session_id").write_text(session_id, encoding="utf-8")

    # ── Channel linking ────────────────────────────────────────────────────

    def link(self, channel: str, user_id: str, session_id: str) -> None:
        """Map (channel, user_id) → session_id in SQLite.

        If a mapping already exists it is overwritten.

        Args:
            channel: Channel name, e.g. ``"telegram"``.
            user_id: Platform user identifier.
            session_id: LangGraph session ID to associate.
        """
        now = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO channel_sessions"
                " (channel, user_id, session_id, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(channel, user_id) DO UPDATE SET"
                " session_id=excluded.session_id, created_at=excluded.created_at",
                (channel, user_id, session_id, now),
            )
        logger.info(
            "session_registry.linked",
            channel=channel,
            user_id=user_id,
            session_id=session_id,
        )

    def unlink(self, channel: str, user_id: str) -> None:
        """Remove the session mapping for (channel, user_id).

        A no-op if the mapping does not exist.

        Args:
            channel: Channel name.
            user_id: Platform user identifier.
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM channel_sessions WHERE channel=? AND user_id=?",
                (channel, user_id),
            )
        logger.info("session_registry.unlinked", channel=channel, user_id=user_id)

    def lookup(self, channel: str, user_id: str) -> str | None:
        """Return the mapped session_id for (channel, user_id), or None.

        Args:
            channel: Channel name.
            user_id: Platform user identifier.

        Returns:
            The mapped session ID string, or ``None`` if not found.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM channel_sessions WHERE channel=? AND user_id=?",
                (channel, user_id),
            ).fetchone()
        return row["session_id"] if row else None

    # ── Preference extraction timestamps ──────────────────────────────────

    @property
    def _extract_ts_file(self) -> Path:
        """Return the path to the last-extraction timestamp JSON file."""
        return self._profile_dir / "last_extract.json"

    def get_last_extract_time(self, session_id: str) -> datetime | None:
        """Return the timestamp of the last preference extraction for a session.

        Args:
            session_id: The session identifier to look up.

        Returns:
            :class:`datetime` of the last extraction, or ``None`` if never run.
        """
        if not self._extract_ts_file.exists():
            return None
        try:
            data: dict[str, str] = json.loads(self._extract_ts_file.read_text(encoding="utf-8"))
            ts = data.get(session_id)
            if ts:
                return datetime.fromisoformat(ts)
        except Exception as exc:
            logger.debug(
                "session_registry.get_last_extract_time.parse_error",
                error=str(exc),
            )
        return None

    def set_last_extract_time(self, session_id: str) -> None:
        """Record the current UTC time as the last preference extraction for a session.

        Args:
            session_id: The session identifier to update.
        """
        self._extract_ts_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if self._extract_ts_file.exists():
            try:
                data = json.loads(self._extract_ts_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.debug(
                    "session_registry.set_last_extract_time.parse_error",
                    error=str(exc),
                )
                data = {}
        data[session_id] = datetime.now(UTC).isoformat()
        self._extract_ts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("session_registry.set_last_extract_time", session_id=session_id)


__all__ = ["SessionRegistry"]
