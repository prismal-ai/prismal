"""Unit tests for lightagent.memory.long_term (T-091)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── _redact_sensitive ─────────────────────────────────────────────────────────


def test_redact_openai_key() -> None:
    """_redact_sensitive replaces OpenAI API keys."""
    from lightagent.memory.long_term import _redact_sensitive

    text = "My key is sk-" + "a" * 48
    result = _redact_sensitive(text)
    assert "[REDACTED]" in result
    assert "sk-" + "a" * 48 not in result


def test_redact_generic_api_key() -> None:
    """_redact_sensitive replaces generic api_key patterns."""
    from lightagent.memory.long_term import _redact_sensitive

    text = 'api_key="supersecretvalue12345678"'
    result = _redact_sensitive(text)
    assert "[REDACTED]" in result


def test_redact_safe_text_unchanged() -> None:
    """_redact_sensitive does not modify safe text."""
    from lightagent.memory.long_term import _redact_sensitive

    text = "The weather in Paris is sunny today."
    assert _redact_sensitive(text) == text


def test_redact_anthropic_key() -> None:
    """_redact_sensitive replaces Anthropic API keys."""
    from lightagent.memory.long_term import _redact_sensitive

    text = "sk-ant-" + "a" * 95
    result = _redact_sensitive(text)
    assert "[REDACTED]" in result


# ── MemoryEntry model ─────────────────────────────────────────────────────────


def test_memory_entry_has_required_fields() -> None:
    """MemoryEntry can be constructed with required fields."""
    from lightagent.memory.long_term import MemoryEntry

    entry = MemoryEntry(
        id="test-id",
        session_id="sess-1",
        content="The user prefers dark mode.",
        created_at=datetime(2026, 1, 1),
        expires_at=datetime(2026, 2, 1),
    )
    assert entry.id == "test-id"
    assert entry.content == "The user prefers dark mode."


def test_memory_entry_is_expired() -> None:
    """MemoryEntry.is_expired() returns True for past expiry."""
    from lightagent.memory.long_term import MemoryEntry

    entry = MemoryEntry(
        id="x",
        session_id="s",
        content="old",
        created_at=datetime(2020, 1, 1),
        expires_at=datetime(2020, 2, 1),
    )
    assert entry.is_expired() is True


def test_memory_entry_not_expired() -> None:
    """MemoryEntry.is_expired() returns False for future expiry."""
    from lightagent.memory.long_term import MemoryEntry

    future = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)
    entry = MemoryEntry(
        id="x",
        session_id="s",
        content="fresh",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        expires_at=future,
    )
    assert entry.is_expired() is False


# ── LongTermMemory.save ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_stores_entry(tmp_path: Path) -> None:
    """save() persists a memory entry and returns its id."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["doc-id-1"]

    mem = LongTermMemory(db_path=tmp_path / "mem.db", vector_store=mock_store)
    entry_id = await mem.save("User likes Python", session_id="sess-1")

    assert isinstance(entry_id, str)
    assert len(entry_id) > 0
    mock_store.add_documents.assert_called_once()


@pytest.mark.asyncio
async def test_save_redacts_sensitive_content(tmp_path: Path) -> None:
    """save() redacts API keys before storing."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["doc-id-1"]

    mem = LongTermMemory(db_path=tmp_path / "mem.db", vector_store=mock_store)
    await mem.save("My key is sk-" + "a" * 48, session_id="sess-1")

    # Check the document sent to ChromaDB has redacted content
    call_args = mock_store.add_documents.call_args[0][0]
    assert len(call_args) == 1
    assert "[REDACTED]" in call_args[0].page_content
    assert "sk-" + "a" * 48 not in call_args[0].page_content


@pytest.mark.asyncio
async def test_save_persists_across_instances(tmp_path: Path) -> None:
    """Saved entries survive LongTermMemory reconstruction."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["doc-id-1"]

    db = tmp_path / "mem.db"
    mem1 = LongTermMemory(db_path=db, vector_store=mock_store)
    await mem1.save("Persistent fact", session_id="sess-1")

    mem2 = LongTermMemory(db_path=db, vector_store=mock_store)
    entries = mem2._list_all()
    assert any("Persistent fact" in e.content for e in entries)


# ── LongTermMemory.recall ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_returns_memory_entries(tmp_path: Path) -> None:
    """recall() returns a list of MemoryEntry objects."""
    from langchain_core.documents import Document

    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["doc-abc"]
    doc = Document(
        page_content="User prefers Python",
        metadata={"entry_id": "doc-abc", "session_id": "sess-1"},
    )
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    db = tmp_path / "mem.db"
    mem = LongTermMemory(db_path=db, vector_store=mock_store)
    await mem.save("User prefers Python", session_id="sess-1")

    results = await mem.recall("programming language preference")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_with_session_filter(tmp_path: Path) -> None:
    """recall() with session_id filters to that session."""
    from langchain_core.documents import Document

    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["doc-1"]
    doc = Document(
        page_content="fact",
        metadata={"entry_id": "doc-1", "session_id": "sess-A"},
    )
    mock_store.similarity_search.return_value = [(doc, 0.9)]

    mem = LongTermMemory(db_path=tmp_path / "mem.db", vector_store=mock_store)
    await mem.save("fact", session_id="sess-A")

    results = await mem.recall("fact", session_id="sess-B")
    # Entry belongs to sess-A, filtering for sess-B should exclude it
    assert len(results) == 0


# ── LongTermMemory.clear ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_all_removes_all_entries(tmp_path: Path) -> None:
    """clear() with no session_id removes all entries."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.side_effect = [["id-1"], ["id-2"]]

    mem = LongTermMemory(db_path=tmp_path / "mem.db", vector_store=mock_store)
    await mem.save("fact-A", session_id="sess-1")
    await mem.save("fact-B", session_id="sess-2")

    count = await mem.clear()
    assert count == 2
    assert len(mem._list_all()) == 0


@pytest.mark.asyncio
async def test_clear_session_removes_only_that_session(tmp_path: Path) -> None:
    """clear(session_id=X) removes only entries for session X."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.side_effect = [["id-1"], ["id-2"]]

    mem = LongTermMemory(db_path=tmp_path / "mem.db", vector_store=mock_store)
    await mem.save("fact-A", session_id="sess-1")
    await mem.save("fact-B", session_id="sess-2")

    count = await mem.clear(session_id="sess-1")
    assert count == 1
    remaining = mem._list_all()
    assert len(remaining) == 1
    assert remaining[0].session_id == "sess-2"


# ── LongTermMemory.expire ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expire_removes_old_entries(tmp_path: Path) -> None:
    """expire() removes entries past their expires_at timestamp."""
    from lightagent.memory.long_term import LongTermMemory

    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id-1"]

    mem = LongTermMemory(
        db_path=tmp_path / "mem.db",
        vector_store=mock_store,
        retention_days=0,  # expires immediately
    )
    await mem.save("soon-to-expire", session_id="sess-1")

    count = await mem.expire()
    assert count >= 1
    assert len(mem._list_all()) == 0
