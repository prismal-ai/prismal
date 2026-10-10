"""Unit tests for the compaction hysteresis trigger (Phase TKE — SPEC-TKE-MSK-002)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

from prismal.agents.artifacts import ArtifactRef
from prismal.agents.context_compaction import (
    ContextCompactor,
    clear_context_compaction_run,
    get_context_compactor,
    maybe_compact_context,
    maybe_seed_context_compaction_run,
)
from prismal.agents.state import create_initial_state
from prismal.core.config import Settings
from prismal.utils.token_estimate import estimate_tokens


class FakeStore:
    def __init__(self) -> None:
        self.n = 0

    def put(self, content: str, *, run_id: str, tenant: str, kind: str, **_: Any) -> ArtifactRef:
        self.n += 1
        return ArtifactRef(
            id=f"a{self.n}",
            run_id=run_id,
            tenant=tenant,
            tool=None,
            tokens_estimate=estimate_tokens(content),
            created_at=0.0,
            kind=kind,
        )


def _tokens(messages: list[BaseMessage]) -> int:
    return sum(estimate_tokens(str(m.content)) for m in messages)


def _h(i: int) -> HumanMessage:
    return HumanMessage(content="h" * 40, id=f"h{i}")  # 10 tokens each


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "token_efficiency_enabled": True,
        "context_compaction_high_water": 100,
        "context_compaction_low_water": 50,
        "context_compaction_max_messages": 1000,
        "context_compaction_keep_recent": 2,
    }
    base.update(kw)
    return Settings(**base)


# ── trigger ───────────────────────────────────────────────────────────────────


def test_high_water_trigger() -> None:
    compactor = ContextCompactor(settings=_settings())
    assert compactor.should_compact([_h(i) for i in range(10)]) == (False, "")  # 100 tokens
    assert compactor.should_compact([_h(i) for i in range(11)]) == (True, "high_water")


def test_high_water_replaces_message_count_trigger() -> None:
    compactor = ContextCompactor(settings=_settings(context_compaction_max_messages=3))
    assert compactor.should_compact([_h(i) for i in range(5)]) == (False, "")


def test_master_off_ignores_high_water() -> None:
    settings = _settings(token_efficiency_enabled=False, context_compaction_max_messages=3)
    compactor = ContextCompactor(settings=settings)
    assert compactor.should_compact([_h(i) for i in range(4)]) == (True, "message_count")
    assert compactor.should_compact([_h(i) for i in range(3)]) == (False, "")


def test_zero_high_water_uses_lh_trigger() -> None:
    settings = Settings(token_efficiency_enabled=True, context_compaction_max_messages=3)
    compactor = ContextCompactor(settings=settings)
    assert compactor.should_compact([_h(i) for i in range(4)]) == (True, "message_count")


# ── truncate down to low water ───────────────────────────────────────────────


async def test_truncate_drops_until_low_water() -> None:
    msgs: list[BaseMessage] = [_h(i) for i in range(12)]  # 120 tokens
    compactor = ContextCompactor(settings=_settings())
    result = await compactor.compact(msgs)
    assert result.compacted is True
    assert result.removed_ids == tuple(f"h{i}" for i in range(7))  # 5 remain → 50 tokens
    kept = [m for m in msgs if m.id not in result.removed_ids]
    assert _tokens(kept) <= 50


async def test_truncate_respects_keep_recent() -> None:
    msgs: list[BaseMessage] = [HumanMessage(content="z" * 400, id=f"b{i}") for i in range(4)]
    compactor = ContextCompactor(settings=_settings(context_compaction_keep_recent=3))
    result = await compactor.compact(msgs)
    assert result.removed_ids == ("b0",)


async def test_truncate_compact_list_until_low_water() -> None:
    msgs: list[BaseMessage] = [_h(i) for i in range(12)]
    compactor = ContextCompactor(settings=_settings())
    out = await compactor.compact_list(msgs)
    assert [m.id for m in out] == [f"h{i}" for i in range(7, 12)]


# ── mask down to low water ───────────────────────────────────────────────────


def _mask_history() -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for i in range(4):
        msgs.append(
            AIMessage(
                content="",
                id=f"a{i}",
                tool_calls=[{"name": "t", "args": {}, "id": f"c{i}", "type": "tool_call"}],
            )
        )
        msgs.append(ToolMessage(content="o" * 200, id=f"t{i}", tool_call_id=f"c{i}", name="t"))
    msgs.append(HumanMessage(content="q", id="hq"))
    return msgs  # 4 × 50 tokens + 1 = 201 tokens


async def test_mask_oldest_first_until_low_water() -> None:
    settings = _settings(
        context_compaction_strategy="mask_observations",
        context_compaction_high_water=150,
        context_compaction_low_water=130,
        context_mask_keep_turns=1,
    )
    compactor = ContextCompactor(settings=settings, artifact_store=FakeStore())
    msgs = _mask_history()
    assert compactor.should_compact(msgs) == (True, "high_water")
    result = await compactor.mask_observations(msgs, run_id="r", tenant="t")
    # masking t0 and t1 brings the estimate from 201 to 127 (≤ 130)
    assert [m.id for m in result.replaced] == ["t0", "t1"]
    merged = add_messages(add_messages([], msgs), list(result.replaced))
    assert _tokens(merged) <= 130
    assert compactor.should_compact(merged) == (False, "")


# ── once per band through maybe_compact_context ──────────────────────────────


async def test_truncate_once_per_band_with_growing_history() -> None:
    settings = _settings(context_compaction_enabled=True)
    state = create_initial_state(session_id="sess-hyst")
    clear_context_compaction_run(state)
    try:
        maybe_seed_context_compaction_run(state, settings)
        compactions = 0
        history: list[BaseMessage] = []
        for i in range(30):
            history = add_messages(history, [_h(i)])
            state["messages"] = history
            update = await maybe_compact_context(state, settings)
            if update:
                compactions += 1
                history = add_messages(history, update["messages"])
                assert _tokens(history) <= 50
            # a duplicate hop with no reducer application must not re-compact
            state["messages"] = history
            assert await maybe_compact_context(state, settings) == {}
        # 10 tokens/msg: cross 100 at msg 11 → down to 50, then +6 msgs cross again …
        # starts 0: crossings at i=10, 16, 22, 28 → 4 compactions
        assert compactions == 4
    finally:
        clear_context_compaction_run(state)


async def test_mask_once_per_band_with_growing_history() -> None:
    settings = _settings(
        context_compaction_enabled=True,
        context_compaction_strategy="mask_observations",
        context_compaction_high_water=1000,
        context_compaction_low_water=600,
        context_mask_keep_turns=1,
    )
    state = create_initial_state(session_id="sess-hyst-mask")
    clear_context_compaction_run(state)
    try:
        maybe_seed_context_compaction_run(state, settings)
        compactor = get_context_compactor(state)
        assert compactor is not None
        compactor._artifact_store = FakeStore()
        history: list[BaseMessage] = []
        compactions = 0
        for i in range(12):
            history = add_messages(
                history,
                [
                    AIMessage(
                        content="",
                        id=f"a{i}",
                        tool_calls=[{"name": "t", "args": {}, "id": f"c{i}", "type": "tool_call"}],
                    ),
                    ToolMessage(content="o" * 1600, id=f"t{i}", tool_call_id=f"c{i}", name="t"),
                ],
            )
            state["messages"] = history
            update = await maybe_compact_context(state, settings)
            if update:
                compactions += 1
                before = [m.id for m in history]
                history = add_messages(history, update["messages"])
                assert [m.id for m in history] == before
                assert _tokens(history) <= 600
            state["messages"] = history
            assert await maybe_compact_context(state, settings) == {}
            # every crossing compacted exactly once: never above high water after a hop
            assert _tokens(history) <= 1000
        # +400 tokens/step: crossings at i=2, 4, 6, 8, 10 (each masks back to ≤ 600)
        assert compactions == 5
    finally:
        clear_context_compaction_run(state)
