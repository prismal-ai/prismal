"""Unit tests for the blind_review_pipeline builder + topology (Phase BRP4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from prismal.agents.subgraphs.code_review.types import CodeReviewReport
from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.registry import SubgraphDefinition


def _compile(definition: SubgraphDefinition) -> Any:
    """Compile a definition with an in-process MemorySaver (no aiosqlite thread)."""
    return assemble_state_graph(definition).compile(checkpointer=MemorySaver())


async def _fake_spec_fn(goal: str) -> str:
    return "SPEC"


async def _fake_impl_fn(spec: str, issues: Any) -> str:
    return "IMPL"


def _fakes(a_score: float = 0.9, b_score: float = 0.85) -> dict[str, Any]:
    async def rev_a(spec: str, artifact: str) -> CodeReviewReport:
        return CodeReviewReport(summary="a", score=a_score, approved=a_score >= 0.8)

    async def rev_b(spec: str, artifact: str) -> CodeReviewReport:
        return CodeReviewReport(summary="b", score=b_score, approved=b_score >= 0.8)

    return {
        "spec_fn": _fake_spec_fn,
        "implementer_fn": _fake_impl_fn,
        "reviewer_a_fn": rev_a,
        "reviewer_b_fn": rev_b,
    }


def _fake_settings(hitl_enabled: bool) -> Any:
    return type("S", (), {"hitl_enabled": hitl_enabled})()


# ── BRP4-05: topology ─────────────────────────────────────────────────────────


def test_build_subgraph_topology_matches_spec() -> None:
    """The SubgraphDefinition matches the spec topology (SPEC-BRP-SUB-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )

    definition = build_blind_review_pipeline_subgraph(**_fakes())

    assert definition.name == "blind_review_pipeline"
    assert definition.entry_point == "spec_agent"
    for node in (
        "spec_agent",
        "implementer",
        "reviewer_a",
        "reviewer_b",
        "synthesis",
        "approval_seed",
        "human_approval",
    ):
        assert node in definition.nodes

    edges = set(definition.edges)
    assert ("spec_agent", "implementer") in edges
    assert ("implementer", "reviewer_a") in edges
    assert ("reviewer_a", "reviewer_b") in edges
    assert ("reviewer_b", "synthesis") in edges
    assert ("approval_seed", "human_approval") in edges

    assert "synthesis" in definition.conditional_edges
    assert "human_approval" in definition.conditional_edges


# ── BRP4-02: score gate wiring ────────────────────────────────────────────────


def test_score_gate_routes_pass() -> None:
    """The wired synthesis gate routes to approval_seed when score >= threshold (BRP4-02)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )

    definition = build_blind_review_pipeline_subgraph(**_fakes())
    gate = definition.conditional_edges["synthesis"]

    state = {
        "iteration_count": 0,
        "metadata": {"blind_review": {"synthesis": {"report": {"score": 0.95}}}},
    }
    assert gate(state) == "approval_seed"


def test_score_gate_routes_fail() -> None:
    """The wired synthesis gate routes back to implementer when score < threshold (BRP4-02)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )

    definition = build_blind_review_pipeline_subgraph(**_fakes())
    gate = definition.conditional_edges["synthesis"]

    state = {
        "iteration_count": 0,
        "metadata": {"blind_review": {"synthesis": {"report": {"score": 0.4}}}},
    }
    assert gate(state) == "implementer"


# ── BRP4-04: HITL ─────────────────────────────────────────────────────────────


def test_hitl_bypassed_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With hitl disabled, the wired HITL gate routes straight to END (BRP4-04)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )

    monkeypatch.setattr(
        "prismal.agents.subgraphs.gates.get_settings",
        lambda: _fake_settings(hitl_enabled=False),
    )

    definition = build_blind_review_pipeline_subgraph(**_fakes())
    gate = definition.conditional_edges["human_approval"]

    # Even a recorded "reject" is bypassed to on_approve (__end__) when disabled.
    state = {"metadata": {"_hitl_last_action": "reject"}}
    assert gate(state) == "__end__"


@pytest.mark.asyncio
async def test_hitl_interrupt_raised_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With hitl enabled a passing run pauses at the human_approval interrupt (BRP4-04)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )
    from prismal.core.config import Settings

    monkeypatch.setattr(
        "prismal.agents.subgraphs.blind_review_pipeline.builder.get_settings",
        lambda: _fake_settings(hitl_enabled=True),
    )

    definition = build_blind_review_pipeline_subgraph(
        **_fakes(a_score=0.9, b_score=0.9), settings=Settings()
    )
    compiled = _compile(definition)

    result = await compiled.ainvoke(
        {"messages": [], "metadata": {}},
        config={"configurable": {"thread_id": "brp-hitl"}},
    )

    assert "__interrupt__" in result


# ── BRP4-03: both reviewers run before synthesis ──────────────────────────────


@pytest.mark.asyncio
async def test_both_reviewers_run_before_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end (fakes): both verdicts land, synthesis merges them, run completes (BRP4-03)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )
    from prismal.core.config import Settings

    monkeypatch.setattr(
        "prismal.agents.subgraphs.blind_review_pipeline.builder.get_settings",
        lambda: _fake_settings(hitl_enabled=False),
    )

    definition = build_blind_review_pipeline_subgraph(
        **_fakes(a_score=0.9, b_score=0.85), settings=Settings()
    )
    compiled = _compile(definition)

    result = await compiled.ainvoke(
        {"messages": [], "metadata": {}},
        config={"configurable": {"thread_id": "brp-both"}},
    )

    br = result["metadata"]["blind_review"]
    assert br["reviewer_a_verdict"].score == 0.9
    assert br["reviewer_b_verdict"].score == 0.85
    assert br["synthesis"]["report"]["score"] == 0.85  # conservative min
    assert "__interrupt__" not in result


@pytest.mark.asyncio
async def test_failing_synthesis_loops_back_and_force_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistently failing review loops back to implementer, bounded by max_iterations (BRP4-05)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )
    from prismal.core.config import Settings

    monkeypatch.setattr(
        "prismal.agents.subgraphs.blind_review_pipeline.builder.get_settings",
        lambda: _fake_settings(hitl_enabled=False),
    )

    settings = Settings(blind_review_max_iterations=2)
    # Both reviewers always fail (score < threshold) → the gate keeps routing
    # back to implementer until the iteration bound force-passes.
    definition = build_blind_review_pipeline_subgraph(
        **_fakes(a_score=0.1, b_score=0.1), settings=settings
    )
    compiled = _compile(definition)

    result = await compiled.ainvoke(
        {"messages": [], "metadata": {}, "iteration_count": 0},
        config={"configurable": {"thread_id": "brp-loop"}},
    )

    # Terminates (force-pass) rather than looping forever, and the implementer
    # ran at least max_iterations times.
    assert result["iteration_count"] >= 2
    assert "__interrupt__" not in result


@pytest.mark.asyncio
async def test_fail_then_retry_recovers_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reviewers fail on the first pass and approve on the retry; the run completes (BRP6-06)."""
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        build_blind_review_pipeline_subgraph,
    )
    from prismal.core.config import Settings

    monkeypatch.setattr(
        "prismal.agents.subgraphs.blind_review_pipeline.builder.get_settings",
        lambda: _fake_settings(hitl_enabled=False),
    )

    attempts = {"n": 0}

    async def spec_fn(goal: str) -> str:
        return "SPEC"

    async def impl_fn(spec: str, issues: Any) -> str:
        attempts["n"] += 1
        return f"IMPL#{attempts['n']}"

    async def rev(spec: str, artifact: str) -> CodeReviewReport:
        # Fail on the first implementation, approve on the retry.
        score = 0.95 if attempts["n"] >= 2 else 0.1
        return CodeReviewReport(summary="r", score=score, approved=score >= 0.8)

    definition = build_blind_review_pipeline_subgraph(
        spec_fn=spec_fn,
        implementer_fn=impl_fn,
        reviewer_a_fn=rev,
        reviewer_b_fn=rev,
        settings=Settings(blind_review_max_iterations=3),
    )
    compiled = _compile(definition)

    result = await compiled.ainvoke(
        {"messages": [], "metadata": {}, "iteration_count": 0},
        config={"configurable": {"thread_id": "brp-retry"}},
    )

    assert attempts["n"] >= 2  # the implementer ran again after the first failure
    assert result["metadata"]["blind_review"]["synthesis"]["report"]["approved"] is True
    assert "__interrupt__" not in result


# ── BRP4-06: idempotent registration ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_is_idempotent() -> None:
    """register_blind_review_pipeline skips a second build once registered (BRP4-06)."""
    import prismal.agents.subgraphs.blind_review_pipeline.builder as builder_mod
    from prismal.agents.subgraphs.blind_review_pipeline.builder import (
        register_blind_review_pipeline,
    )

    builder_mod._COMPILED_GRAPHS.clear()

    mock_factory = AsyncMock()
    mock_factory.build = AsyncMock(return_value=MagicMock())

    store: dict[str, Any] = {}
    mock_registry = MagicMock()
    mock_registry.get = MagicMock(side_effect=lambda name: store.get(name))

    async def _register(name: str, definition: Any) -> None:
        store[name] = definition

    mock_registry.register = AsyncMock(side_effect=_register)

    with (
        patch(
            "prismal.agents.subgraphs.blind_review_pipeline.builder.SubgraphFactory",
            return_value=mock_factory,
        ),
        patch(
            "prismal.agents.subgraphs.blind_review_pipeline.builder.SubgraphRegistry.get_instance",
            return_value=mock_registry,
        ),
    ):
        await register_blind_review_pipeline(checkpointer_path=":memory:")
        await register_blind_review_pipeline(checkpointer_path=":memory:")

    mock_factory.build.assert_called_once()
    assert store.get("blind_review_pipeline") is not None
