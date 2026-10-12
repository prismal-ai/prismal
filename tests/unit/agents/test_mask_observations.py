"""Unit tests for observation masking (Phase TKE — SPEC-TKE-MSK-001)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

import prismal.agents.context_compaction as cc
from prismal.agents.artifacts import ArtifactRef, InMemoryArtifactStore
from prismal.agents.context_compaction import (
    OBSERVATION_STUB,
    CompactionResult,
    CompactionStrategy,
    ContextCompactor,
    clear_context_compaction_run,
    maybe_compact_context,
    maybe_seed_context_compaction_run,
)
from prismal.agents.state import create_initial_state
from prismal.core.config import Settings
from prismal.utils.token_estimate import estimate_tokens

# ── helpers ───────────────────────────────────────────────────────────────────


class FakeStore:
    """Records every ``put`` call; optionally fails."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def put(
        self,
        content: str,
        *,
        run_id: str,
        tenant: str,
        kind: str,
        tool: str | None = None,
        ttl_s: int | None = None,
    ) -> ArtifactRef:
        if self.fail:
            raise RuntimeError("store down")
        self.calls.append(
            {"content": content, "run_id": run_id, "tenant": tenant, "kind": kind, "tool": tool}
        )
        return ArtifactRef(
            id=f"art{len(self.calls)}",
            run_id=run_id,
            tenant=tenant,
            tool=tool,
            tokens_estimate=estimate_tokens(content),
            created_at=0.0,
            kind=kind,
        )


class FakeOTel:
    counts: dict[str, int] = {}

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        FakeOTel.counts[metric] = FakeOTel.counts.get(metric, 0) + value


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> type[FakeOTel]:
    FakeOTel.counts = {}
    monkeypatch.setattr(cc, "OTelManager", FakeOTel)
    return FakeOTel


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "token_efficiency_enabled": True,
        "context_compaction_strategy": "mask_observations",
        "context_mask_keep_turns": 2,
        "context_compaction_max_messages": 3,
    }
    base.update(kw)
    return Settings(**base)


def _history() -> list[BaseMessage]:
    """human, ai(call t1), tool t1, ai(call t2), tool t2, human, ai."""
    return [
        HumanMessage(content="q1", id="h1"),
        AIMessage(
            content="",
            id="a1",
            tool_calls=[{"name": "search", "args": {}, "id": "c1", "type": "tool_call"}],
        ),
        ToolMessage(content="X" * 400, id="t1", tool_call_id="c1", name="search"),
        AIMessage(
            content="",
            id="a2",
            tool_calls=[{"name": "fetch", "args": {}, "id": "c2", "type": "tool_call"}],
        ),
        ToolMessage(content="Y" * 200, id="t2", tool_call_id="c2", name="fetch", status="error"),
        HumanMessage(content="q2", id="h2"),
        AIMessage(content="answer", id="a3"),
    ]


# ── enum / constants ──────────────────────────────────────────────────────────


def test_strategy_enum_has_mask_observations() -> None:
    assert CompactionStrategy("mask_observations") is CompactionStrategy.MASK_OBSERVATIONS


def test_stub_format() -> None:
    assert (
        OBSERVATION_STUB.format(tool="search", artifact_id="abc", tokens=100)
        == "[observation elided: tool=search artifact=abc tokens≈100]"
    )


def test_result_replaced_defaults_empty() -> None:
    result = CompactionResult(
        compacted=False, strategy=CompactionStrategy.TRUNCATE, messages_dropped=0, removed_ids=()
    )
    assert result.replaced == ()


# ── mask_observations core ────────────────────────────────────────────────────


async def test_masks_old_observations_and_stores_first(otel: type[FakeOTel]) -> None:
    store = FakeStore()
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    result = await compactor.mask_observations(_history(), run_id="r1", tenant="acme")

    assert result.compacted is True
    assert result.strategy is CompactionStrategy.MASK_OBSERVATIONS
    assert result.messages_dropped == 0
    assert result.removed_ids == ()
    assert [m.id for m in result.replaced] == ["t1", "t2"]

    # Full payload stored first, with scope + kind + tool.
    assert store.calls[0] == {
        "content": "X" * 400,
        "run_id": "r1",
        "tenant": "acme",
        "kind": "tool_output",
        "tool": "search",
    }
    first = result.replaced[0]
    assert isinstance(first, ToolMessage)
    assert first.content == OBSERVATION_STUB.format(
        tool="search", artifact_id="art1", tokens=estimate_tokens("X" * 400)
    )
    assert first.tool_call_id == "c1"
    assert first.name == "search"
    second = result.replaced[1]
    assert isinstance(second, ToolMessage)
    assert second.tool_call_id == "c2"
    assert second.status == "error"
    assert otel.counts.get("observations_masked") == 2


async def test_unknown_tool_name() -> None:
    store = FakeStore()
    msgs: list[BaseMessage] = [
        ToolMessage(content="data", id="t0", tool_call_id="c0"),
        HumanMessage(content="q", id="h"),
        AIMessage(content="a", id="a"),
    ]
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    result = await compactor.mask_observations(msgs, run_id="r", tenant="t")
    assert "tool=unknown" in str(result.replaced[0].content)


async def test_keep_turns_boundary() -> None:
    # Turns from the end: a3, h2, a2, a1, h1. keep_turns=3 puts the boundary at
    # a2, so t2 (after a2) stays and only t1 is masked.
    compactor = ContextCompactor(
        settings=_settings(context_mask_keep_turns=3), artifact_store=FakeStore()
    )
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert [m.id for m in result.replaced] == ["t1"]


async def test_keep_turns_boundary_protects_observation_at_boundary() -> None:
    # keep_turns=4 puts the boundary at a1, so both t1 and t2 stay.
    compactor = ContextCompactor(
        settings=_settings(context_mask_keep_turns=4), artifact_store=FakeStore()
    )
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert result.replaced == ()


async def test_fewer_turns_than_keep_masks_nothing() -> None:
    compactor = ContextCompactor(
        settings=_settings(context_mask_keep_turns=50), artifact_store=FakeStore()
    )
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert result.compacted is False
    assert result.replaced == ()


async def test_already_masked_and_idless_skipped() -> None:
    msgs = _history()
    msgs[2] = ToolMessage(
        content="[observation elided: tool=search artifact=z tokens≈1]",
        id="t1",
        tool_call_id="c1",
        name="search",
    )
    msgs[4] = ToolMessage(content="Y" * 200, tool_call_id="c2", name="fetch")  # no id
    store = FakeStore()
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    result = await compactor.mask_observations(msgs, run_id="r", tenant="t")
    assert result.compacted is False
    assert store.calls == []


async def test_store_failure_keeps_payload_and_counts_error(otel: type[FakeOTel]) -> None:
    compactor = ContextCompactor(settings=_settings(), artifact_store=FakeStore(fail=True))
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert result.compacted is False
    assert result.replaced == ()
    assert otel.counts.get("observations_mask_errors") == 2
    assert otel.counts.get("observations_masked", 0) == 0


async def test_relevance_fn_keeps_high_scoring() -> None:
    def relevance(msg: ToolMessage, context: Any) -> float:
        return 0.9 if msg.id == "t1" else 0.1

    compactor = ContextCompactor(
        settings=_settings(), artifact_store=FakeStore(), relevance_fn=relevance
    )
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert [m.id for m in result.replaced] == ["t2"]


async def test_relevance_fn_exception_is_positional() -> None:
    def relevance(msg: ToolMessage, context: Any) -> float:
        raise ValueError("boom")

    compactor = ContextCompactor(
        settings=_settings(), artifact_store=FakeStore(), relevance_fn=relevance
    )
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert [m.id for m in result.replaced] == ["t1", "t2"]


async def test_lazy_store_built_when_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Settings] = []
    store = InMemoryArtifactStore(settings=_settings(), audit=object())

    def fake_build(settings: Settings) -> InMemoryArtifactStore:
        built.append(settings)
        return store

    monkeypatch.setattr(cc, "shared_artifact_store", fake_build)
    settings = _settings()
    compactor = ContextCompactor(settings=settings)
    result = await compactor.mask_observations(_history(), run_id="r", tenant="t")
    assert built == [settings]
    assert result.compacted is True


# ── state update / reducer ────────────────────────────────────────────────────


async def test_to_state_update_includes_replaced_and_preserves_positions() -> None:
    history = add_messages([], _history())
    compactor = ContextCompactor(settings=_settings(), artifact_store=FakeStore())
    result = await compactor.mask_observations(history, run_id="r", tenant="t")
    update = compactor.to_state_update(result)
    assert update == {"messages": list(result.replaced)}

    merged = add_messages(history, update["messages"])
    assert [m.id for m in merged] == [m.id for m in history]
    assert str(merged[2].content).startswith("[observation elided:")
    assert str(merged[4].content).startswith("[observation elided:")
    # tool_call pairing preserved
    assert isinstance(merged[2], ToolMessage)
    assert merged[2].tool_call_id == merged[1].tool_calls[0]["id"]  # type: ignore[attr-defined]
    assert isinstance(merged[4], ToolMessage)
    assert merged[4].tool_call_id == merged[3].tool_calls[0]["id"]  # type: ignore[attr-defined]


# ── compact() delegation / master flag ───────────────────────────────────────


async def test_compact_delegates_to_mask_with_default_scope() -> None:
    store = FakeStore()
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    result = await compactor.compact(_history())
    assert result.strategy is CompactionStrategy.MASK_OBSERVATIONS
    assert store.calls[0]["run_id"].startswith("anon-")  # never a shared constant
    assert store.calls[0]["tenant"] == "default"


async def test_master_off_mask_behaves_as_truncate() -> None:
    store = FakeStore()
    settings = _settings(token_efficiency_enabled=False, context_compaction_keep_recent=2)
    compactor = ContextCompactor(settings=settings, artifact_store=store)
    result = await compactor.compact(_history())
    assert result.strategy is CompactionStrategy.TRUNCATE
    assert result.removed_ids == ("h1", "a1", "t1", "a2", "t2")
    assert result.replaced == ()
    assert store.calls == []


async def test_default_strategy_unchanged() -> None:
    compactor = ContextCompactor(
        settings=Settings(context_compaction_keep_recent=2), artifact_store=FakeStore()
    )
    result = await compactor.compact(_history())
    assert result.strategy is CompactionStrategy.TRUNCATE
    assert compactor.to_state_update(result)["messages"][0].type == "remove"


# ── compact_list mask path ───────────────────────────────────────────────────


async def test_compact_list_mask_substitutes_in_place() -> None:
    history = _history()
    compactor = ContextCompactor(settings=_settings(), artifact_store=FakeStore())
    out = await compactor.compact_list(history)
    assert len(out) == len(history)
    assert [m.id for m in out] == [m.id for m in history]
    assert str(out[2].content).startswith("[observation elided:")
    assert str(out[4].content).startswith("[observation elided:")
    assert out[0] is history[0]
    assert history[2].content == "X" * 400  # input not mutated


# ── maybe_compact_context scope derivation ───────────────────────────────────


async def test_maybe_compact_context_mask_uses_session_and_org() -> None:
    state = create_initial_state(session_id="sess-mask")
    state["messages"] = add_messages([], _history())
    state["metadata"]["org_id"] = "acme"
    settings = _settings(context_compaction_enabled=True)
    clear_context_compaction_run(state)
    try:
        maybe_seed_context_compaction_run(state, settings)
        store = FakeStore()
        compactor = cc.get_context_compactor(state)
        assert compactor is not None
        compactor._artifact_store = store
        update = await maybe_compact_context(state, settings)
        assert [m.id for m in update["messages"]] == ["t1", "t2"]
        assert store.calls[0]["run_id"] == "sess-mask"
        assert store.calls[0]["tenant"] == "acme"
    finally:
        clear_context_compaction_run(state)


# ── tenant isolation (security review 2026-10-05) ────────────────────────────


async def test_compact_list_mask_uses_given_scope() -> None:
    store = FakeStore()
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    await compactor.compact_list(_history(), run_id="sess-1", tenant="acme")
    assert {(c["run_id"], c["tenant"]) for c in store.calls} == {("sess-1", "acme")}


async def test_compact_list_mask_without_scope_is_never_shared() -> None:
    store = FakeStore()
    compactor = ContextCompactor(settings=_settings(), artifact_store=store)
    await compactor.compact_list(_history())
    await compactor.compact_list(_history())
    run_ids = [c["run_id"] for c in store.calls]
    assert "run" not in run_ids
    assert all(r.startswith("anon-") for r in run_ids)
    assert run_ids[0] != run_ids[-1]  # each anonymous call gets its own scope


def test_run_scope_without_session_is_anonymous_and_unique() -> None:
    state = create_initial_state(session_id="")
    first, tenant = cc._run_scope(state)
    second, _ = cc._run_scope(state)
    assert tenant == "default"
    assert first.startswith("anon-") and first != second
