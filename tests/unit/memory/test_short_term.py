"""Unit tests for lightagent.memory.short_term (T-090)."""

from __future__ import annotations

import threading

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# ── construction ──────────────────────────────────────────────────────────────


def test_short_term_memory_starts_empty() -> None:
    """A new ShortTermMemory has no messages."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    assert len(mem) == 0
    assert mem.get_all() == []


def test_short_term_memory_custom_max() -> None:
    """max_messages parameter is stored."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory(max_messages=10)
    assert mem.max_messages == 10


# ── add ───────────────────────────────────────────────────────────────────────


def test_add_single_message() -> None:
    """add() stores a single message."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    msg = HumanMessage(content="Hello")
    mem.add(msg)
    assert len(mem) == 1


def test_add_multiple_messages() -> None:
    """add() stores multiple messages in order."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    msgs = [HumanMessage(content="Hi"), AIMessage(content="Hello")]
    for m in msgs:
        mem.add(m)
    all_msgs = mem.get_all()
    assert len(all_msgs) == 2
    assert all_msgs[0].content == "Hi"
    assert all_msgs[1].content == "Hello"


def test_add_evicts_oldest_when_full() -> None:
    """add() evicts the oldest message when max_messages is reached."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory(max_messages=3)
    for i in range(4):
        mem.add(HumanMessage(content=f"msg-{i}"))

    msgs = mem.get_all()
    assert len(msgs) == 3
    contents = [m.content for m in msgs]
    assert "msg-0" not in contents
    assert "msg-3" in contents


# ── get_all ───────────────────────────────────────────────────────────────────


def test_get_all_returns_copy() -> None:
    """get_all() returns a list (modifications don't affect internal state)."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    mem.add(HumanMessage(content="X"))
    result = mem.get_all()
    result.clear()
    assert len(mem) == 1  # internal state unchanged


# ── get_recent ────────────────────────────────────────────────────────────────


def test_get_recent_returns_last_n() -> None:
    """get_recent(n) returns the n most recent messages."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    for i in range(5):
        mem.add(HumanMessage(content=f"msg-{i}"))

    recent = mem.get_recent(3)
    assert len(recent) == 3
    contents = [m.content for m in recent]
    assert contents == ["msg-2", "msg-3", "msg-4"]


def test_get_recent_with_n_larger_than_size() -> None:
    """get_recent(n) returns all messages if n > len(memory)."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    mem.add(HumanMessage(content="only"))
    assert len(mem.get_recent(10)) == 1


def test_get_recent_zero() -> None:
    """get_recent(0) returns empty list."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    mem.add(HumanMessage(content="x"))
    assert mem.get_recent(0) == []


# ── clear ─────────────────────────────────────────────────────────────────────


def test_clear_empties_memory() -> None:
    """clear() removes all messages."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    mem.add(HumanMessage(content="a"))
    mem.add(AIMessage(content="b"))
    mem.clear()
    assert len(mem) == 0


# ── thread safety ─────────────────────────────────────────────────────────────


def test_concurrent_add_is_safe() -> None:
    """Concurrent add() calls do not corrupt the internal state."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory(max_messages=1000)
    errors: list[Exception] = []

    def add_msgs() -> None:
        try:
            for i in range(50):
                mem.add(HumanMessage(content=f"msg-{i}"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=add_msgs) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(mem) == 200


# ── message type preservation ─────────────────────────────────────────────────


def test_preserves_message_types() -> None:
    """get_all() returns messages with their original types."""
    from lightagent.memory.short_term import ShortTermMemory

    mem = ShortTermMemory()
    mem.add(SystemMessage(content="sys"))
    mem.add(HumanMessage(content="human"))
    mem.add(AIMessage(content="ai"))

    all_msgs = mem.get_all()
    assert isinstance(all_msgs[0], SystemMessage)
    assert isinstance(all_msgs[1], HumanMessage)
    assert isinstance(all_msgs[2], AIMessage)
