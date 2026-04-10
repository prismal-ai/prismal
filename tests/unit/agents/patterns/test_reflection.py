"""Tests for the reflection loop framework (SPEC-035 / Phase 33)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from lightagent.agents.patterns.reflection import reflection_loop, with_reflection
from lightagent.agents.state import create_initial_state

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState


def _state() -> AgentState:
    return create_initial_state(session_id="sess-reflection-test")


# ---------------------------------------------------------------------------
# reflection_loop core behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_iteration_pass() -> None:
    """Score >= threshold on first attempt → returns after 1 iteration."""
    calls: list[str] = []

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        calls.append("gen")
        return "perfect-draft"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        calls.append("crit")
        return ("ok", 0.95)

    draft, score = await reflection_loop(
        gen, crit, _state(), threshold=0.85, max_iterations=3
    )

    assert draft == "perfect-draft"
    assert score == 0.95
    # Exactly 1 generate + 1 critique call.
    assert calls == ["gen", "crit"]


@pytest.mark.asyncio
async def test_multi_iteration_convergence() -> None:
    """Score improves over iterations and passes threshold on iteration 2."""
    scores = iter([0.4, 0.9])
    drafts: list[str] = []

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        if previous_draft is None:
            text = "draft-1"
        else:
            assert critique == "needs more rigor"
            text = "draft-2-refined"
        drafts.append(text)
        return text

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("needs more rigor", next(scores))

    draft, score = await reflection_loop(
        gen, crit, _state(), threshold=0.85, max_iterations=3
    )

    assert draft == "draft-2-refined"
    assert score == 0.9
    assert drafts == ["draft-1", "draft-2-refined"]


@pytest.mark.asyncio
async def test_max_iterations_forced_exit() -> None:
    """After max_iterations, returns best draft even if below threshold."""
    iteration = {"n": 0}

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        iteration["n"] += 1
        return f"draft-{iteration['n']}"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        # Always below threshold.
        return ("never enough", 0.2)

    draft, score = await reflection_loop(
        gen, crit, _state(), threshold=0.95, max_iterations=2
    )

    # All drafts scored 0.2 → first draft remains the "best" (ties don't override).
    assert draft == "draft-1"
    assert score == 0.2
    assert iteration["n"] == 2


@pytest.mark.asyncio
async def test_reflection_disabled_by_env() -> None:
    """LIGHTAGENT_REFLECTION_ENABLED=false → returns first draft, no critique."""
    crit_calls = {"n": 0}

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        return "first-draft"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        crit_calls["n"] += 1
        return ("unused", 0.0)

    fake_settings = type(
        "S", (), {"reflection_enabled": False, "reflection_max_iterations": 3}
    )()

    with patch(
        "lightagent.agents.patterns.reflection.get_settings",
        return_value=fake_settings,
    ):
        draft, score = await reflection_loop(
            gen, crit, _state(), threshold=0.85, max_iterations=3
        )

    assert draft == "first-draft"
    # Sentinel score when bypassed.
    assert score == 1.0
    assert crit_calls["n"] == 0


@pytest.mark.asyncio
async def test_best_draft_returned_on_early_exit() -> None:
    """If max_iterations reached, the highest-scoring draft is returned."""
    drafts = iter([("draft-1", 0.7), ("draft-2", 0.3), ("draft-3", 0.5)])

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        d, _ = next(drafts)
        gen.last = d  # type: ignore[attr-defined]
        return d

    next_score = {"d": {"draft-1": 0.7, "draft-2": 0.3, "draft-3": 0.5}}

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("rework", next_score["d"][draft])

    draft, score = await reflection_loop(
        gen, crit, _state(), threshold=0.99, max_iterations=3
    )

    # Highest score across all 3 iterations is draft-1 @ 0.7.
    assert draft == "draft-1"
    assert score == 0.7


@pytest.mark.asyncio
async def test_global_max_iterations_cap() -> None:
    """settings.reflection_max_iterations caps the caller-supplied value."""
    counter = {"n": 0}

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        counter["n"] += 1
        return f"draft-{counter['n']}"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("low", 0.1)

    fake_settings = type(
        "S", (), {"reflection_enabled": True, "reflection_max_iterations": 2}
    )()

    with patch(
        "lightagent.agents.patterns.reflection.get_settings",
        return_value=fake_settings,
    ):
        await reflection_loop(
            gen, crit, _state(), threshold=0.99, max_iterations=10
        )

    # Caller asked for 10 but global cap is 2.
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_invalid_threshold_raises() -> None:
    """threshold outside [0,1] raises ValueError."""

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        return "x"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("", 1.0)

    with pytest.raises(ValueError, match="threshold"):
        await reflection_loop(gen, crit, _state(), threshold=1.5, max_iterations=2)


@pytest.mark.asyncio
async def test_invalid_max_iterations_raises() -> None:
    """max_iterations < 1 raises ValueError."""

    async def gen(state, previous_draft=None, critique=None):  # type: ignore[no-untyped-def]
        return "x"

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("", 1.0)

    with pytest.raises(ValueError, match="max_iterations"):
        await reflection_loop(gen, crit, _state(), threshold=0.5, max_iterations=0)


# ---------------------------------------------------------------------------
# with_reflection decorator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_reflection_stores_score_in_metadata() -> None:
    """The decorator stores reflection score under state['metadata'][node_name]."""

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("good", 0.92)

    @with_reflection(threshold=0.85, max_iterations=2, critique_fn=crit)
    async def my_node(state):  # type: ignore[no-untyped-def]
        return "node-output"

    state = _state()
    result = await my_node(state)

    assert result == "node-output"
    assert state["metadata"]["my_node"]["reflection_score"] == 0.92


@pytest.mark.asyncio
async def test_with_reflection_handles_node_without_kwargs() -> None:
    """Wrapped node without previous_draft/critique kwargs still works."""

    async def crit(draft, state):  # type: ignore[no-untyped-def]
        return ("ok", 0.99)

    @with_reflection(threshold=0.85, max_iterations=2, critique_fn=crit)
    async def simple_node(state):  # type: ignore[no-untyped-def]
        # Does NOT accept previous_draft / critique kwargs.
        return "simple-output"

    result = await simple_node(_state())
    assert result == "simple-output"


def test_with_reflection_requires_critique_fn() -> None:
    """with_reflection raises ValueError when critique_fn is None."""
    with pytest.raises(ValueError, match="critique_fn"):
        with_reflection(threshold=0.85, max_iterations=2, critique_fn=None)
