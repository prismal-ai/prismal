"""Unit tests for prismal.memory.mongodb_store — MongoDBMemoryStore.

All MongoDB operations are fully mocked via AsyncMock since `motor` is
an optional dependency not required in the standard test environment.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.memory.mongodb_store import MongoDBMemoryStore

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_collection_mock() -> MagicMock:
    """Return a MagicMock that mimics an AsyncIOMotorCollection."""
    col = MagicMock()
    col.create_index = AsyncMock()
    col.insert_one = AsyncMock()
    col.find_one = AsyncMock(return_value=None)
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    col.find.return_value = cursor
    return col


def _make_motor_stubs(collection: MagicMock) -> tuple[MagicMock, ModuleType]:
    """Return (motor_mock, motor_asyncio_stub) backed by ``collection``.

    ``import motor.motor_asyncio as _motor`` is compiled as IMPORT_FROM on the
    parent ``motor`` module, so the parent mock's ``.motor_asyncio`` attribute
    must point at the stub explicitly.
    """
    db = MagicMock()
    db.__getitem__.return_value = collection

    client = MagicMock()
    client.__getitem__.return_value = db
    client.close = MagicMock()

    stub = ModuleType("motor.motor_asyncio")
    stub.AsyncIOMotorClient = MagicMock(return_value=client)

    motor_mock = MagicMock()
    motor_mock.motor_asyncio = stub  # IMPORT_FROM uses this attribute
    return motor_mock, stub


def _make_store(
    mongodb_url: str = "mongodb://localhost:27017",
) -> tuple[MongoDBMemoryStore, MagicMock]:
    """Return (store, collection_mock) with motor fully stubbed."""
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = []
    store = MongoDBMemoryStore(mongodb_url=mongodb_url, vector_store=mock_vs)
    col = _make_collection_mock()
    return store, col


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_init_stores_url() -> None:
    """MongoDBMemoryStore stores the provided mongodb_url."""
    store, _ = _make_store("mongodb://myhost:27017")
    assert store._mongodb_url == "mongodb://myhost:27017"
    assert store._initialized is False


def test_init_uses_custom_retention() -> None:
    """MongoDBMemoryStore respects the retention_days argument."""
    mock_vs = MagicMock()
    store = MongoDBMemoryStore(
        mongodb_url="mongodb://localhost", vector_store=mock_vs, retention_days=90
    )
    assert store._retention == 90


def test_init_creates_vector_store_when_not_provided() -> None:
    """MongoDBMemoryStore builds the store via VectorStoreFactory when vector_store=None."""
    with patch("prismal.rag.vector_store_factory.VectorStoreFactory") as mock_factory:
        mock_factory.create.return_value = MagicMock()
        with patch("prismal.core.config.get_settings") as mock_cfg:
            mock_settings = MagicMock()
            mock_settings.mongodb_url = "mongodb://localhost"
            mock_settings.memory_retention_days = 30
            mock_cfg.return_value = mock_settings
            store = MongoDBMemoryStore()

    mock_factory.create.assert_called_once()
    assert store._vector_store is not None


# ── initialize ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_raises_when_url_empty() -> None:
    """initialize() raises ValueError when mongodb_url is empty."""
    mock_vs = MagicMock()
    store = MongoDBMemoryStore(mongodb_url="", vector_store=mock_vs)
    with pytest.raises(ValueError, match="mongodb_url"):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_raises_when_motor_missing() -> None:
    """initialize() raises ImportError when motor is not installed."""
    store, _ = _make_store()
    with patch.dict(sys.modules, {"motor": None, "motor.motor_asyncio": None}):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            await store.initialize()


@pytest.mark.asyncio
async def test_initialize_creates_indexes() -> None:
    """initialize() calls create_index twice (TTL + session_id)."""
    store, col = _make_store()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()

    assert col.create_index.call_count == 2
    assert store._initialized is True


@pytest.mark.asyncio
async def test_initialize_is_idempotent() -> None:
    """initialize() is a no-op on the second call."""
    store, col = _make_store()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        call_count = col.create_index.call_count
        await store.initialize()

    assert col.create_index.call_count == call_count  # no new calls


# ── close ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_resets_initialized_flag() -> None:
    """close() sets _initialized back to False."""
    store, col = _make_store()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()

    assert store._initialized is True
    await store.close()
    assert store._initialized is False


@pytest.mark.asyncio
async def test_close_without_init_is_safe() -> None:
    """close() on an uninitialized store does not raise."""
    store, _ = _make_store()
    await store.close()


# ── save ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_returns_uuid_entry_id() -> None:
    """save() returns a UUID4 string and calls collection.insert_one."""
    store, col = _make_store()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        entry_id = await store.save("User prefers dark mode", session_id="sess-1")

    assert len(entry_id) == 36
    col.insert_one.assert_called_once()


@pytest.mark.asyncio
async def test_save_adds_to_vector_store() -> None:
    """save() calls vector_store.add_documents after insert_one."""
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = []
    store = MongoDBMemoryStore(mongodb_url="mongodb://localhost", vector_store=mock_vs)
    col = _make_collection_mock()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        await store.save("remember this", session_id="sess-2")

    mock_vs.add_documents.assert_called_once()


# ── recall ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_returns_empty_on_chroma_error() -> None:
    """recall() returns [] when ChromaDB similarity_search raises."""
    mock_vs = MagicMock()
    mock_vs.similarity_search.side_effect = RuntimeError("chroma error")
    store = MongoDBMemoryStore(mongodb_url="mongodb://localhost", vector_store=mock_vs)
    col = _make_collection_mock()
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        results = await store.recall("any query")

    assert results == []


@pytest.mark.asyncio
async def test_recall_filters_by_session_id() -> None:
    """recall() skips entries that belong to a different session."""
    now = datetime.now(UTC).replace(tzinfo=None)
    expires = now + timedelta(days=30)

    mock_doc = MagicMock()
    mock_doc.metadata = {"entry_id": "e1", "session_id": "sess-other"}
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [(mock_doc, 0.9)]

    store = MongoDBMemoryStore(mongodb_url="mongodb://localhost", vector_store=mock_vs)
    col = _make_collection_mock()
    col.find_one = AsyncMock(
        return_value={
            "_id": "e1",
            "session_id": "sess-other",
            "content": "hello",
            "created_at": now,
            "expires_at": expires,
        }
    )
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        results = await store.recall("hello", session_id="sess-mine")

    assert results == []


# ── clear ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_all_when_no_session_id() -> None:
    """clear(session_id=None) calls delete_many with an empty filter."""
    store, col = _make_store()
    col.find.return_value.to_list = AsyncMock(return_value=[{"_id": "e1"}, {"_id": "e2"}])
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        count = await store.clear()

    assert count == 2
    col.delete_many.assert_called_once_with({})


@pytest.mark.asyncio
async def test_clear_by_session_id() -> None:
    """clear(session_id=X) calls delete_many with session filter."""
    store, col = _make_store()
    col.find.return_value.to_list = AsyncMock(return_value=[{"_id": "e1"}])
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=1))
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        count = await store.clear(session_id="sess-x")

    assert count == 1
    col.delete_many.assert_called_once_with({"session_id": "sess-x"})


# ── expire ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expire_returns_deleted_count() -> None:
    """expire() returns the number of expired entries removed."""
    store, col = _make_store()
    col.find.return_value.to_list = AsyncMock(return_value=[{"_id": "old-e1"}])
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=1))
    motor_mock, motor_stub = _make_motor_stubs(col)

    with patch.dict(sys.modules, {"motor": motor_mock, "motor.motor_asyncio": motor_stub}):
        await store.initialize()
        count = await store.expire()

    assert count == 1
