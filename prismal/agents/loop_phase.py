"""Deterministic task-phase resolution for dynamic tool gating (Phase LH — SPEC-LH-PHS-001).

``resolve_phase`` never calls an LLM and never raises. Precedence:

1. An explicit ``state["metadata"]["loop"]["phase"]`` hint, honored verbatim
   when it is one of the three known :data:`Phase` literals.
2. A deterministic fallback derived from ``task_plan``/``pending_tasks``/
   ``completed_tasks``.
3. ``None`` — no plan and no hint — meaning "no gating" (identical to
   pre-LH2 behavior).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

Phase = Literal["planning", "executing", "finishing"]

_KNOWN_PHASES: frozenset[str] = frozenset({"planning", "executing", "finishing"})


def resolve_phase(state: AgentState) -> Phase | None:
    """Return the current task phase, or ``None`` when no gating applies."""
    metadata = state.get("metadata")
    if isinstance(metadata, dict):
        loop_meta = metadata.get("loop")
        if isinstance(loop_meta, dict):
            hint = loop_meta.get("phase")
            if hint in _KNOWN_PHASES:
                return cast("Phase", hint)

    task_plan = state.get("task_plan")
    if not task_plan:
        return None

    pending_tasks = state.get("pending_tasks")
    if pending_tasks:
        completed_tasks = state.get("completed_tasks")
        return "planning" if not completed_tasks else "executing"
    return "finishing"


__all__ = ["Phase", "resolve_phase"]
