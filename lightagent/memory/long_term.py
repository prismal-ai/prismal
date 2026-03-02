"""Long-term (cross-session) memory backed by SQLite and ChromaDB.

Saves facts from agent conversations to a dual store:

* **SQLite** — structured metadata (id, session_id, content, expiry).
* **ChromaDB** (via :class:`~lightagent.rag.vector_store.ChromaVectorStore`) —
  semantic similarity search for recall.

Sensitive data (API keys, secrets) is automatically redacted before any
content is written to either store (AC-011-5).  Entries expire after
``retention_days`` days and can be pruned by calling :meth:`LongTermMemory.expire`.

Example::

    from lightagent.memory.long_term import LongTermMemory

    mem = LongTermMemory()
    entry_id = await mem.save("User prefers dark mode", session_id="sess-abc")
    results = await mem.recall("UI preferences", session_id="sess-abc")
    await mem.clear(session_id="sess-abc")

AC-011-2: Across sessions, the agent can recall facts from long-term memory.
AC-011-4: ``lightagent memory clear`` delegates to :meth:`clear`.
AC-011-5: Sensitive information is auto-redacted before persistence.
AC-011-6: Entries expire after ``memory_retention_days`` days.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.documents import Document
from pydantic import BaseModel

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from lightagent.rag.vector_store import ChromaVectorStore

logger = get_logger("lightagent.memory.long_term")

# ── Sensitive-data redaction ──────────────────────────────────────────────────

_REDACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[a-zA-Z0-9\-_]{95}"),  # Anthropic
    re.compile(r"sk-[a-zA-Z0-9]{48}"),  # OpenAI
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),  # Google
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36}"),  # GitHub
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"[0-9a-zA-Z/+]{40}"),  # AWS secret (broad, last)
    re.compile(  # generic api_key/secret
        r'(?:api[_\-]?key|secret|token)["\'\s:=]+[a-zA-Z0-9\-_]{20,}',
        re.IGNORECASE,
    ),
]

_REDACTED = "[REDACTED]"


def _redact_sensitive(text: str) -> str:
    """Replace known secret patterns in ``text`` with ``[REDACTED]``.

    Args:
        text: The original text that may contain sensitive data.

    Returns:
        Text with all matched secrets replaced by ``'[REDACTED]'``.
    """
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


# ── SQLite schema ─────────────────────────────────────────────────────────────

_DB_DEFAULT = Path(__file__).parent.parent.parent / "data" / "db" / "memory.db"
_COLLECTION = "lightagent_memory"
_DT_FMT = "%Y-%m-%dT%H:%M:%S"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session ON memory_entries (session_id);
CREATE INDEX IF NOT EXISTS idx_expires ON memory_entries (expires_at);
"""


# ── Model ─────────────────────────────────────────────────────────────────────


class MemoryEntry(BaseModel):
    """A single persisted long-term memory entry.

    Attributes:
        id: Unique identifier (UUID4 string).
        session_id: The session that produced this entry.
        content: The (already-redacted) text content.
        created_at: UTC timestamp of creation.
        expires_at: UTC timestamp after which the entry is stale.
    """

    id: str
    session_id: str
    content: str
    created_at: datetime
    expires_at: datetime

    def is_expired(self) -> bool:
        """Return True if this entry's expiry is in the past.

        Returns:
            Boolean indicating whether the entry has expired.
        """
        return self.expires_at < datetime.now(UTC).replace(tzinfo=None)


# ── Manager ───────────────────────────────────────────────────────────────────


class LongTermMemory:
    """Dual-store long-term memory: SQLite metadata + ChromaDB semantic search.

    Args:
        db_path: SQLite file path.  Defaults to ``data/db/memory.db``.
        vector_store: Injected :class:`~lightagent.rag.vector_store.ChromaVectorStore`.
            Defaults to a new instance pointing at the configured ChromaDB.
        retention_days: Days until entries expire.  Defaults to
            ``settings.memory_retention_days``.

    Example::

        mem = LongTermMemory()
        await mem.save("User is a Python developer", session_id="sess-1")
        results = await mem.recall("developer background")
    """

    def __init__(
        self,
        db_path: Path | None = None,
        vector_store: ChromaVectorStore | None = None,
        retention_days: int | None = None,
    ) -> None:
        """Initialise LongTermMemory and ensure the SQLite schema exists."""
        self._db = db_path or _DB_DEFAULT
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        if vector_store is not None:
            self._store = vector_store
        else:
            from lightagent.rag.vector_store import ChromaVectorStore

            self._store = ChromaVectorStore(collection_name=_COLLECTION)

        if retention_days is not None:
            self._retention = retention_days
        else:
            from lightagent.core.config import get_settings

            self._retention = get_settings().memory_retention_days

    # ── SQLite helpers ────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection]:
        """Yield a SQLite connection, commit on success, close always."""
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the memory_entries table if it does not exist."""
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        """Convert a SQLite row to a :class:`MemoryEntry`."""
        return MemoryEntry(
            id=row["id"],
            session_id=row["session_id"],
            content=row["content"],
            created_at=datetime.strptime(row["created_at"], _DT_FMT),  # noqa: DTZ007
            expires_at=datetime.strptime(row["expires_at"], _DT_FMT),  # noqa: DTZ007
        )

    def _list_all(self) -> list[MemoryEntry]:
        """Return all entries from SQLite (used in tests and expire())."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_entries ORDER BY created_at"
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ── Public API ────────────────────────────────────────────────────────────

    async def save(self, content: str, session_id: str) -> str:
        """Persist a fact to long-term memory.

        Redacts any sensitive patterns before storing.  The entry is added
        to both SQLite and the ChromaDB vector store.

        Args:
            content: The text fact to remember (e.g. "User prefers dark mode").
            session_id: The session that produced this fact.

        Returns:
            The UUID4 string id assigned to the new entry.

        AC-011-5: Sensitive data is redacted before persistence.
        AC-011-6: Entry expires after ``retention_days`` days.
        """
        safe_content = _redact_sensitive(content)
        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + timedelta(days=self._retention)
        entry_id = str(uuid.uuid4())

        fmt = _DT_FMT
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_entries"
                " (id, session_id, content, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    entry_id,
                    session_id,
                    safe_content,
                    now.strftime(fmt),
                    expires.strftime(fmt),
                ),
            )

        # Store entry_id in both "source" and "entry_id" metadata so that
        # delete_by_source(entry_id) correctly targets this document.
        doc = Document(
            page_content=safe_content,
            metadata={
                "entry_id": entry_id,
                "session_id": session_id,
                "source": entry_id,
            },
        )
        self._store.add_documents([doc])
        logger.info("long_term_memory_saved", entry_id=entry_id, session_id=session_id)
        return entry_id

    async def recall(
        self,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Retrieve semantically similar memories.

        Args:
            query: Free-text query for similarity search.
            session_id: If provided, only return entries from this session.
                Omit to search across all sessions (AC-011-2).
            k: Number of candidates to retrieve from ChromaDB before filtering.

        Returns:
            List of non-expired :class:`MemoryEntry` objects, ordered by
            descending similarity.

        AC-011-2: Cross-session recall when ``session_id`` is None.
        """
        try:
            results = self._store.similarity_search(query, k=k)
        except Exception as exc:
            logger.warning("long_term_memory_recall_error", error=str(exc))
            return []

        entries: list[MemoryEntry] = []
        for doc, _score in results:
            entry_id = doc.metadata.get("entry_id")
            doc_session = doc.metadata.get("session_id", "")

            if session_id is not None and doc_session != session_id:
                continue

            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
                ).fetchone()

            if row is None:
                continue

            entry = self._row_to_entry(row)
            if not entry.is_expired():
                entries.append(entry)

        return entries

    async def clear(self, session_id: str | None = None) -> int:
        """Delete memory entries from both stores.

        Args:
            session_id: If provided, delete only entries for this session.
                If ``None``, delete everything (AC-011-4).

        Returns:
            Number of entries deleted.

        AC-011-4: ``lightagent memory clear`` uses this method.
        """
        with self._conn() as conn:
            if session_id is None:
                rows = conn.execute("SELECT id FROM memory_entries").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM memory_entries WHERE session_id = ?",
                    (session_id,),
                ).fetchall()

            ids = [r["id"] for r in rows]
            count = len(ids)

            if session_id is None:
                conn.execute("DELETE FROM memory_entries")
            else:
                conn.execute(
                    "DELETE FROM memory_entries WHERE session_id = ?", (session_id,)
                )

        for entry_id in ids:
            try:
                self._store.delete_by_source(entry_id)
            except Exception as exc:
                logger.warning(
                    "long_term_memory_clear_chroma_error",
                    entry_id=entry_id,
                    error=str(exc),
                )

        logger.info("long_term_memory_cleared", count=count, session_id=session_id)
        return count

    async def expire(self) -> int:
        """Delete all entries whose ``expires_at`` is in the past.

        Returns:
            Number of entries deleted.

        AC-011-6: Entries expire after ``memory_retention_days`` days.
        """
        now = datetime.now(UTC).replace(tzinfo=None).strftime(_DT_FMT)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM memory_entries WHERE expires_at <= ?", (now,)
            ).fetchall()
            ids = [r["id"] for r in rows]
            conn.execute(
                "DELETE FROM memory_entries WHERE expires_at <= ?", (now,)
            )

        for entry_id in ids:
            try:
                self._store.delete_by_source(entry_id)
            except Exception as exc:
                logger.warning(
                    "long_term_memory_expire_chroma_error",
                    entry_id=entry_id,
                    error=str(exc),
                )

        logger.info("long_term_memory_expired", count=len(ids))
        return len(ids)


__all__ = ["LongTermMemory", "MemoryEntry", "_redact_sensitive"]
