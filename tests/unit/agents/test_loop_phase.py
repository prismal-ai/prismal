"""Unit tests for prismal.agents.loop_phase.resolve_phase (Phase LH — SPEC-LH-PHS-001)."""

from __future__ import annotations

from prismal.agents.loop_phase import resolve_phase
from prismal.agents.state import create_initial_state


def test_no_plan_and_no_hint_resolves_to_none() -> None:
    state = create_initial_state(session_id="s")
    assert resolve_phase(state) is None


def test_planning_when_plan_exists_and_nothing_started() -> None:
    state = create_initial_state(session_id="s")
    state["task_plan"] = ["a", "b", "c"]
    state["pending_tasks"] = ["a", "b", "c"]
    state["completed_tasks"] = []
    assert resolve_phase(state) == "planning"


def test_executing_once_a_task_has_completed() -> None:
    state = create_initial_state(session_id="s")
    state["task_plan"] = ["a", "b", "c"]
    state["pending_tasks"] = ["b", "c"]
    state["completed_tasks"] = ["a"]
    assert resolve_phase(state) == "executing"


def test_finishing_when_pending_drained() -> None:
    state = create_initial_state(session_id="s")
    state["task_plan"] = ["a", "b", "c"]
    state["pending_tasks"] = []
    state["completed_tasks"] = ["a", "b", "c"]
    assert resolve_phase(state) == "finishing"


def test_explicit_hint_overrides_task_plan_derivation() -> None:
    state = create_initial_state(session_id="s")
    state["task_plan"] = ["a", "b", "c"]
    state["pending_tasks"] = ["a", "b", "c"]
    state["completed_tasks"] = []
    state["metadata"]["loop"] = {"phase": "executing"}
    assert resolve_phase(state) == "executing"


def test_invalid_explicit_hint_falls_through_to_derivation() -> None:
    state = create_initial_state(session_id="s")
    state["task_plan"] = ["a", "b", "c"]
    state["pending_tasks"] = []
    state["completed_tasks"] = ["a", "b", "c"]
    state["metadata"]["loop"] = {"phase": "not_a_real_phase"}
    assert resolve_phase(state) == "finishing"


def test_never_raises_on_malformed_metadata() -> None:
    state = create_initial_state(session_id="s")
    state["metadata"] = {"loop": "not_a_dict"}  # type: ignore[typeddict-item]
    assert resolve_phase(state) is None
