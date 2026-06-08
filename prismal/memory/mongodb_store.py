"""MongoDB-backed long-term memory store using Motor and Beanie.

Alternative to :class:`~prismal.memory.long_term.LongTermMemory` for
deployments that use MongoDB as the primary data store.

Architecture
------------
* **Motor** — async MongoDB driver (non-blocking I/O).
* **Collection** — raw Motor collection for full control over TTL index creation
  and document operations without Beanie overhead.
* **TTL index** — ``expires_at`` field has a MongoDB TTL index
  (``expireAfterSeconds=0``) so the database automatically purges expired
  documents without application-level cron jobs.
* **ChromaDB** — semantic recall is provided via
  :class:`~prismal.rag.vector_store.ChromaVectorStore`, identical to the
  SQLite-backed store.

Usage::

    from prismal.memory.mongodb_store import MongoDBMemoryStore

    store = MongoDBMemoryStore()
    await store.initialize()

    entry_id = await store.save("User prefers dark mode", session_id="sess-1")
    results = await store.recall("UI preferences", session_id="sess-1")
    await store.clear(session_id="sess-1")

SPEC-015 acceptance criteria
-----------------------------
* AC-015-1 ``save()`` writes to MongoDB with TTL index.
* AC-015-2 ``recall()`` uses ChromaDB for semantic search then fetches from MongoDB.
* AC-015-3 ``clear()`` removes entries from both stores.
* AC-015-4 Expired entries are pruned automatically by the TTL index.
* AC-015-5 Sensitive data is redacted before persistence (mirrors LongTermMemory).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from prismal.memory.long_term import MemoryEntry, _redact_sensitive

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

    from prismal.agents.extension.ports import VectorStorePort

logger = structlog.get_logger("prismal.memory.mongodb_store")

_COLLECTION_NAME = "prismal_memory"


class MongoDBMemoryStore:
    """Async long-term memory store backed by MongoDB and ChromaDB.

    Uses Motor for non-blocking MongoDB access.  A TTL index on
    ``expires_at`` ensures automatic expiry at the database level.
    Semantic recall is provided by ChromaDB (same strategy as
    :class:`~prismal.memory.long_term.LongTermMemory`).

    Sensitive data (API keys, secrets) is redacted before any content is
    written to either store (AC-015-5).

    Args:
        mongodb_url: MongoDB connection URL.  Defaults to
            ``settings.mongodb_url``.  Must be non-empty to use this store.
        db_name: MongoDB database name.  Defaults to ``"prismal"``.
        vector_store: Injected
            :class:`~prismal.rag.vector_store.ChromaVectorStore`.
            Defaults to a new instance pointing at the configured ChromaDB.
        retention_days: Days until entries expire.  Defaults to
            ``settings.memory_retention_days``.
    """

    def __init__(
        self,
        mongodb_url: str | None = None,
        db_name: str = "prismal",
        vector_store: VectorStorePort | None = None,
        retention_days: int | None = None,
    ) -> None:
        """Initialise store — does not connect until :meth:`initialize` is called."""
        from prismal.core.config import get_settings

        settings = get_settings()
        self._mongodb_url: str = mongodb_url or settings.mongodb_url
        self._db_name = db_name
        self._retention: int = (
            retention_days if retention_days is not None else settings.memory_retention_days
        )
        self._initialized = False
        self._collection: AsyncIOMotorCollection

        if vector_store is not None:
            self._vector_store = vector_store
        else:
            from prismal.rag.vector_store_factory import VectorStoreFactory

            self._vector_store = VectorStoreFactory.create(
                collection_name=f"{_COLLECTION_NAME}_mongo"
            )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Connect to MongoDB and ensure required indexes exist.

        Safe to call multiple times — subsequent calls are no-ops.

        Raises:
            ImportError: If ``motor`` is not installed.
                Install with ``pip install 'prismal[mongodb]'``.
            ValueError: If ``mongodb_url`` is empty.
        """
        if self._initialized:
            return

        if not self._mongodb_url:
            raise ValueError(
                "MongoDBMemoryStore requires a non-empty mongodb_url. "
                "Set PRISMAL_MONGODB_URL or pass mongodb_url= explicitly."
            )

        try:
            import motor.motor_asyncio as _motor
        except ImportError as exc:
            raise ImportError(
                "MongoDB support requires 'motor'. Install with: pip install 'prismal[mongodb]'"
            ) from exc

        client = _motor.AsyncIOMotorClient(self._mongodb_url)
        db = client[self._db_name]
        self._collection = db[_COLLECTION_NAME]
        self._client = client

        # TTL index — MongoDB removes documents automatically when expires_at passes.
        await self._collection.create_index(
            "expires_at",
            expireAfterSeconds=0,
            name="ttl_expires_at",
        )
        # Secondary index for efficient session-scoped queries.
        await self._collection.create_index("session_id", name="idx_session_id")

        self._initialized = True
        logger.info(
            "mongodb_memory_store_initialized",
            db=self._db_name,
            collection=_COLLECTION_NAME,
        )

    async def close(self) -> None:
        """Close the Motor client connection.

        Call during application shutdown to release resources cleanly.
        """
        if self._initialized and hasattr(self, "_client"):
            self._client.close()
            self._initialized = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def save(self, content: str, session_id: str) -> str:
        """Persist a fact to MongoDB long-term memory.

        Redacts sensitive patterns before storing to either backend
        (AC-015-5).  The entry is also added to ChromaDB for later
        semantic recall.

        Args:
            content: The text fact to remember (e.g. ``"User prefers dark mode"``).
            session_id: Identifier for the session that produced this fact.

        Returns:
            The UUID4 string id assigned to the new entry (AC-015-1).
        """
        await self.initialize()

        safe_content = _redact_sensitive(content)
        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + timedelta(days=self._retention)
        entry_id = str(uuid.uuid4())

        doc: dict[str, object] = {
            "_id": entry_id,
            "session_id": session_id,
            "content": safe_content,
            "created_at": now,
            "expires_at": expires,
        }
        await self._collection.insert_one(doc)

        # Mirror to ChromaDB for semantic similarity search.
        from langchain_core.documents import Document as LCDocument

        lc_doc = LCDocument(
            page_content=safe_content,
            metadata={
                "entry_id": entry_id,
                "session_id": session_id,
                "source": entry_id,
            },
        )
        self._vector_store.add_documents([lc_doc])

        logger.info("mongodb_memory_saved", entry_id=entry_id, session_id=session_id)
        return entry_id

    async def recall(
        self,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Retrieve semantically similar memories from MongoDB.

        Performs a ChromaDB similarity search (AC-015-2), then fetches the
        canonical document from MongoDB to apply session filtering and
        expiry validation.

        Args:
            query: Free-text query used for ChromaDB similarity search.
            session_id: If provided, restrict results to this session.
                Omit to search across all sessions.
            k: Number of ChromaDB candidates to retrieve before filtering.

        Returns:
            List of non-expired :class:`~prismal.memory.long_term.MemoryEntry`
            objects ordered by descending similarity score.
        """
        await self.initialize()

        try:
            chroma_results = self._vector_store.similarity_search(query, k=k)
        except Exception as exc:
            logger.warning("mongodb_memory_recall_error", error=str(exc))
            return []

        now = datetime.now(UTC).replace(tzinfo=None)
        entries: list[MemoryEntry] = []

        for doc, _score in chroma_results:
            entry_id = doc.metadata.get("entry_id")
            if not isinstance(entry_id, str):
                continue
            doc_session = doc.metadata.get("session_id", "")

            if session_id is not None and doc_session != session_id:
                continue

            mongo_doc = await self._collection.find_one({"_id": entry_id})
            if mongo_doc is None:
                continue

            expires_at: datetime | None = mongo_doc.get("expires_at")
            if isinstance(expires_at, datetime) and expires_at < now:
                continue

            created_at: datetime = mongo_doc.get("created_at") or now
            entries.append(
                MemoryEntry(
                    id=entry_id,
                    session_id=mongo_doc.get("session_id", ""),
                    content=mongo_doc.get("content", ""),
                    created_at=created_at,
                    expires_at=expires_at or now,
                )
            )

        return entries

    async def clear(self, session_id: str | None = None) -> int:
        """Delete memory entries from MongoDB and ChromaDB.

        Args:
            session_id: If provided, delete only entries for this session.
                If ``None``, delete all entries (AC-015-3).

        Returns:
            Number of entries deleted.
        """
        await self.initialize()

        query: dict[str, str] = {} if session_id is None else {"session_id": session_id}
        cursor = self._collection.find(query, {"_id": 1})
        docs = await cursor.to_list(length=None)
        ids: list[str] = [str(d["_id"]) for d in docs]

        await self._collection.delete_many(query)

        for entry_id in ids:
            try:
                self._vector_store.delete_by_source(entry_id)
            except Exception as exc:
                logger.warning(
                    "mongodb_memory_clear_chroma_error",
                    entry_id=entry_id,
                    error=str(exc),
                )

        logger.info("mongodb_memory_cleared", count=len(ids), session_id=session_id)
        return len(ids)

    async def expire(self) -> int:
        """Manually delete expired entries from both stores.

        MongoDB's TTL index handles automatic expiry (AC-015-4), but this
        method allows on-demand pruning (e.g. in tests or maintenance jobs).

        Returns:
            Number of entries deleted.
        """
        await self.initialize()

        now = datetime.now(UTC).replace(tzinfo=None)
        cursor = self._collection.find({"expires_at": {"$lte": now}}, {"_id": 1})
        docs = await cursor.to_list(length=None)
        ids: list[str] = [str(d["_id"]) for d in docs]

        result = await self._collection.delete_many({"expires_at": {"$lte": now}})

        for entry_id in ids:
            try:
                self._vector_store.delete_by_source(entry_id)
            except Exception as exc:
                logger.warning(
                    "mongodb_memory_expire_chroma_error",
                    entry_id=entry_id,
                    error=str(exc),
                )

        count: int = result.deleted_count
        logger.info("mongodb_memory_expired", count=count)
        return count


__all__ = ["MongoDBMemoryStore"]
