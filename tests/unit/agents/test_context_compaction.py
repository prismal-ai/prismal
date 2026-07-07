"""Unit tests for prismal.agents.context_compaction (Phase LH — SPEC-LH-CTX-001/002)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from prismal.agents.context_compaction import (
    CompactionResult,
    CompactionStrategy,
    ContextCompactor,
)
from prismal.core.config import Settings


def _messages(n: int) -> list[HumanMessage]:
    return [HumanMessage(content=f"msg{i}", id=f"id{i}") for i in range(n)]


# ── CompactionResult no-op invariant ─────────────────────────────────────────


def test_no_op_result_is_unambiguous() -> None:
    result = CompactionResult(
        compacted=False, strategy=CompactionStrategy.TRUNCATE, messages_dropped=0, removed_ids=()
    )
    assert result.messages_dropped == 0
    assert result.removed_ids == ()
    assert result.summary_message is None


# ── should_compact — message-count trigger ───────────────────────────────────


def test_should_compact_triggers_on_message_count() -> None:
    compactor = ContextCompactor(settings=Settings(context_compaction_max_messages=10))
    should, reason = compactor.should_compact(_messages(11))
    assert should is True
    assert reason == "message_count"


def test_should_not_compact_below_message_count_threshold() -> None:
    compactor = ContextCompactor(settings=Settings(context_compaction_max_messages=10))
    should, reason = compactor.should_compact(_messages(10))
    assert should is False
    assert reason == ""


# ── should_compact — token-threshold trigger ─────────────────────────────────


def test_should_compact_triggers_on_token_threshold() -> None:
    settings = Settings(
        context_compaction_max_messages=1000, context_compaction_token_threshold=100
    )
    budget_guard = MagicMock()
    budget_guard.meter.usage.total_tokens = 150
    compactor = ContextCompactor(settings=settings, budget_guard=budget_guard)
    should, reason = compactor.should_compact(_messages(5))
    assert should is True
    assert reason == "token_threshold"


def test_should_not_compact_below_token_threshold() -> None:
    settings = Settings(
        context_compaction_max_messages=1000, context_compaction_token_threshold=100
    )
    budget_guard = MagicMock()
    budget_guard.meter.usage.total_tokens = 50
    compactor = ContextCompactor(settings=settings, budget_guard=budget_guard)
    should, reason = compactor.should_compact(_messages(5))
    assert should is False
    assert reason == ""


def test_token_threshold_ignored_without_budget_guard() -> None:
    settings = Settings(context_compaction_max_messages=1000, context_compaction_token_threshold=1)
    compactor = ContextCompactor(settings=settings)  # no budget_guard
    should, reason = compactor.should_compact(_messages(5))
    assert should is False
    assert reason == ""


def test_should_compact_never_raises_on_budget_guard_error() -> None:
    settings = Settings(context_compaction_max_messages=1000, context_compaction_token_threshold=1)
    budget_guard = MagicMock()
    type(budget_guard.meter).usage = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    compactor = ContextCompactor(settings=settings, budget_guard=budget_guard)
    should, reason = compactor.should_compact(_messages(5))
    assert should is False
    assert reason == ""


# ── compact() — TRUNCATE strategy ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact_truncate_drops_older_segment_keeps_recent_verbatim() -> None:
    settings = Settings(context_compaction_keep_recent=10)
    compactor = ContextCompactor(settings=settings)
    messages = _messages(200)

    result = await compactor.compact(messages)

    assert result.compacted is True
    assert result.strategy == CompactionStrategy.TRUNCATE
    assert result.summary_message is None
    assert result.messages_dropped == 190
    assert result.removed_ids == tuple(f"id{i}" for i in range(190))


@pytest.mark.asyncio
async def test_compact_no_op_when_fewer_messages_than_keep_recent() -> None:
    settings = Settings(context_compaction_keep_recent=10)
    compactor = ContextCompactor(settings=settings)
    result = await compactor.compact(_messages(5))
    assert result.compacted is False
    assert result.messages_dropped == 0
    assert result.removed_ids == ()


@pytest.mark.asyncio
async def test_compact_no_op_when_exactly_keep_recent() -> None:
    settings = Settings(context_compaction_keep_recent=10)
    compactor = ContextCompactor(settings=settings)
    result = await compactor.compact(_messages(10))
    assert result.compacted is False


@pytest.mark.asyncio
async def test_compact_skips_messages_without_a_stable_id() -> None:
    settings = Settings(context_compaction_keep_recent=2)
    compactor = ContextCompactor(settings=settings)
    messages = [HumanMessage(content="no id here")] + _messages(2)
    result = await compactor.compact(messages)
    # the id-less message can't be RemoveMessage'd; it's simply not in removed_ids
    assert "id0" not in result.removed_ids
    assert "id1" not in result.removed_ids  # kept as part of the tail


# ── compact() — SUMMARIZE strategy ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact_summarize_produces_one_summary_message() -> None:
    settings = Settings(context_compaction_keep_recent=10, context_compaction_strategy="summarize")
    summarizer_fn = AsyncMock(return_value="the gist of it")
    compactor = ContextCompactor(settings=settings, summarizer_fn=summarizer_fn)

    result = await compactor.compact(_messages(200))

    assert result.compacted is True
    assert result.strategy == CompactionStrategy.SUMMARIZE
    assert result.summary_message is not None
    assert isinstance(result.summary_message, AIMessage)
    assert "the gist of it" in result.summary_message.content
    assert result.messages_dropped == 190
    summarizer_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_compact_summarize_falls_back_to_truncate_on_summarizer_error() -> None:
    settings = Settings(context_compaction_keep_recent=10, context_compaction_strategy="summarize")
    summarizer_fn = AsyncMock(side_effect=RuntimeError("llm exploded"))
    compactor = ContextCompactor(settings=settings, summarizer_fn=summarizer_fn)

    result = await compactor.compact(_messages(200))

    assert result.compacted is True
    assert result.strategy == CompactionStrategy.TRUNCATE
    assert result.summary_message is None
    assert result.messages_dropped == 190


# ── to_state_update() ─────────────────────────────────────────────────────────


def test_to_state_update_empty_for_no_op_result() -> None:
    compactor = ContextCompactor(settings=Settings())
    result = CompactionResult(
        compacted=False, strategy=CompactionStrategy.TRUNCATE, messages_dropped=0, removed_ids=()
    )
    assert compactor.to_state_update(result) == {}


def test_to_state_update_truncate_yields_remove_message_entries_only() -> None:
    compactor = ContextCompactor(settings=Settings())
    result = CompactionResult(
        compacted=True,
        strategy=CompactionStrategy.TRUNCATE,
        messages_dropped=2,
        removed_ids=("id0", "id1"),
    )
    update = compactor.to_state_update(result)
    assert list(update.keys()) == ["messages"]
    assert all(isinstance(m, RemoveMessage) for m in update["messages"])
    assert [m.id for m in update["messages"]] == ["id0", "id1"]


def test_to_state_update_summarize_appends_summary_after_removals() -> None:
    compactor = ContextCompactor(settings=Settings())
    summary = AIMessage(content="summary", id="sumid")
    result = CompactionResult(
        compacted=True,
        strategy=CompactionStrategy.SUMMARIZE,
        messages_dropped=2,
        removed_ids=("id0", "id1"),
        summary_message=summary,
    )
    update = compactor.to_state_update(result)
    messages = update["messages"]
    assert isinstance(messages[0], RemoveMessage)
    assert isinstance(messages[1], RemoveMessage)
    assert messages[2] is summary


# ── Reducer round-trip (RF-LH-001, RF-LH-002) ────────────────────────────────


@pytest.mark.asyncio
async def test_state_update_round_trips_through_add_messages_reducer() -> None:
    from langgraph.graph.message import add_messages

    settings = Settings(context_compaction_keep_recent=5)
    compactor = ContextCompactor(settings=settings)
    original = _messages(20)

    result = await compactor.compact(original)
    update = compactor.to_state_update(result)
    merged = add_messages(original, update["messages"])

    assert [m.id for m in merged] == [f"id{i}" for i in range(15, 20)]
