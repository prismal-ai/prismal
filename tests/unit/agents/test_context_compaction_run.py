"""Unit tests for the context-compaction per-run seeding trio (Phase LH — SPEC-LH-CTX-003)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.context_compaction import (
    ContextCompactor,
    clear_context_compaction_run,
    context_compaction_react_kwargs,
    get_context_compactor,
    maybe_compact_context,
    maybe_seed_context_compaction_run,
)
from prismal.agents.state import create_initial_state
from prismal.core.config import Settings


def _messages(n: int) -> list[HumanMessage]:
    return [HumanMessage(content=f"msg{i}", id=f"id{i}") for i in range(n)]


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    state = create_initial_state(session_id="test-session")
    clear_context_compaction_run(state)
    yield
    clear_context_compaction_run(state)


# ── seeding — disabled path ───────────────────────────────────────────────────


def test_seed_noop_when_disabled() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=False)
    maybe_seed_context_compaction_run(state, settings)
    assert get_context_compactor(state) is None
    assert "loop" not in state["metadata"]


# ── seeding — enabled path ────────────────────────────────────────────────────


def test_seed_creates_compactor_when_enabled() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=True, context_compaction_strategy="truncate")
    maybe_seed_context_compaction_run(state, settings)
    compactor = get_context_compactor(state)
    assert isinstance(compactor, ContextCompactor)
    assert state["metadata"]["loop"]["compaction"] == {"enabled": True, "strategy": "truncate"}


def test_seed_idempotent_within_same_turn() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=True)
    maybe_seed_context_compaction_run(state, settings)
    first = get_context_compactor(state)
    maybe_seed_context_compaction_run(state, settings)
    second = get_context_compactor(state)
    assert first is second


def test_seed_reseeds_on_new_turn() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=True)
    maybe_seed_context_compaction_run(state, settings)
    first = get_context_compactor(state)

    state["messages"].append(HumanMessage(content="new turn", id="human-2"))
    maybe_seed_context_compaction_run(state, settings)
    second = get_context_compactor(state)
    assert first is not second


def test_react_kwargs_empty_when_disabled() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=False)
    maybe_seed_context_compaction_run(state, settings)
    assert context_compaction_react_kwargs(state) == {}


def test_react_kwargs_carries_compactor_when_enabled() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=True)
    maybe_seed_context_compaction_run(state, settings)
    kwargs = context_compaction_react_kwargs(state)
    assert isinstance(kwargs.get("context_compactor"), ContextCompactor)


def test_clear_releases_the_entry() -> None:
    state = create_initial_state(session_id="test-session")
    settings = Settings(context_compaction_enabled=True)
    maybe_seed_context_compaction_run(state, settings)
    assert get_context_compactor(state) is not None
    clear_context_compaction_run(state)
    assert get_context_compactor(state) is None


# ── maybe_compact_context ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_compact_noop_when_disabled() -> None:
    state = create_initial_state(session_id="test-session")
    state["messages"] = _messages(200)
    settings = Settings(context_compaction_enabled=False)
    assert await maybe_compact_context(state, settings) == {}


@pytest.mark.asyncio
async def test_maybe_compact_noop_below_threshold() -> None:
    state = create_initial_state(session_id="test-session")
    state["messages"] = _messages(5)
    settings = Settings(context_compaction_enabled=True, context_compaction_max_messages=60)
    maybe_seed_context_compaction_run(state, settings)
    assert await maybe_compact_context(state, settings) == {}


@pytest.mark.asyncio
async def test_maybe_compact_returns_state_update_when_over_threshold() -> None:
    state = create_initial_state(session_id="test-session")
    state["messages"] = _messages(200)
    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=60,
        context_compaction_keep_recent=10,
    )
    maybe_seed_context_compaction_run(state, settings)
    update = await maybe_compact_context(state, settings)
    assert "messages" in update
    assert len(update["messages"]) == 190


@pytest.mark.asyncio
async def test_maybe_compact_does_not_recompact_same_unchanged_state() -> None:
    state = create_initial_state(session_id="test-session")
    state["messages"] = _messages(200)
    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=60,
        context_compaction_keep_recent=10,
        context_compaction_min_interval_messages=20,
    )
    maybe_seed_context_compaction_run(state, settings)
    first_update = await maybe_compact_context(state, settings)
    assert first_update != {}

    # No new messages added, state unchanged — must not compact the same segment again.
    second_update = await maybe_compact_context(state, settings)
    assert second_update == {}


@pytest.mark.asyncio
async def test_maybe_compact_recompacts_after_reducer_shrinks_then_regrows() -> None:
    """A real reduction (smaller state) then real growth must not stay stuck."""
    from langgraph.graph.message import add_messages

    state = create_initial_state(session_id="test-session")
    state["messages"] = _messages(200)
    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=60,
        context_compaction_keep_recent=10,
        context_compaction_min_interval_messages=20,
    )
    maybe_seed_context_compaction_run(state, settings)
    first_update = await maybe_compact_context(state, settings)
    assert first_update != {}

    # Simulate the graph runtime applying the reducer, then real new traffic
    # arriving until the threshold is legitimately exceeded again.
    state["messages"] = add_messages(state["messages"], first_update["messages"])
    new_tail = [HumanMessage(content=f"new{i}", id=f"newid{i}") for i in range(70)]
    state["messages"].extend(new_tail)  # push well past max_messages=60 again

    second_update = await maybe_compact_context(state, settings)
    assert second_update != {}
