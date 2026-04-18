"""Unit tests for ChatSessionStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lightagent.memory.chat_session_store import ChatSessionStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def store(tmp_path: Path) -> ChatSessionStore:
    return ChatSessionStore(db_path=tmp_path / "chat_sessions.db")


def test_create_and_get_session(store: ChatSessionStore) -> None:
    store.create_session("user-abc12345")
    s = store.get_session("user-abc12345")
    assert s is not None
    assert s.session_id == "user-abc12345"
    assert s.title == ""
    assert s.message_count == 0


def test_update_title(store: ChatSessionStore) -> None:
    store.create_session("user-abc12345")
    store.update_title("user-abc12345", "Python data analysis")
    s = store.get_session("user-abc12345")
    assert s is not None
    assert s.title == "Python data analysis"


def test_increment_message_count(store: ChatSessionStore) -> None:
    store.create_session("user-abc12345")
    store.increment_message_count("user-abc12345")
    store.increment_message_count("user-abc12345")
    s = store.get_session("user-abc12345")
    assert s is not None
    assert s.message_count == 2


def test_list_sessions_pagination(store: ChatSessionStore) -> None:
    for i in range(15):
        store.create_session(f"user-{i:08x}")
        store.update_title(f"user-{i:08x}", f"Session {i}")
    sessions, total = store.list_sessions(page=1, page_size=10)
    assert len(sessions) == 10
    assert total == 15
    sessions2, _ = store.list_sessions(page=2, page_size=10)
    assert len(sessions2) == 5


def test_list_sessions_search(store: ChatSessionStore) -> None:
    store.create_session("user-00000001")
    store.update_title("user-00000001", "Python data analysis")
    store.create_session("user-00000002")
    store.update_title("user-00000002", "MCP configuration guide")
    sessions, total = store.list_sessions(page=1, search="Python")
    assert total == 1
    assert sessions[0].title == "Python data analysis"


def test_list_sessions_ordered_by_last_active(store: ChatSessionStore) -> None:
    store.create_session("user-00000001")
    store.create_session("user-00000002")
    store.increment_message_count("user-00000002")  # updates last_active_at
    sessions, _ = store.list_sessions(page=1)
    assert sessions[0].session_id == "user-00000002"


def test_create_session_idempotent(store: ChatSessionStore) -> None:
    store.create_session("user-abc12345")
    store.create_session("user-abc12345")  # must not raise
    s = store.get_session("user-abc12345")
    assert s is not None


def test_get_session_missing_returns_none(store: ChatSessionStore) -> None:
    assert store.get_session("user-missing") is None
