"""Sub-agent lifecycle payloads for the AG-UI translator (Phase PRT follow-up).

Skynet workers and MoA proposers announce their lifecycle with
:func:`emit_subagent`, which writes the ``{"type": "subagent", ...}`` custom
payload that :func:`prismal.events.agui.to_agui_events` turns into
``SUBAGENT_STARTED`` / ``SUBAGENT_FINISHED`` / ``SUBAGENT_ERROR``.

Emission goes through LangGraph's ``get_stream_writer()``: it is a no-op when the
caller does not stream ``custom`` and when called outside a graph run, and it
never raises. Callers pass ids, names and an error *type* only — never worker or
model output, which could carry tenant data or secrets.
"""

from __future__ import annotations

from typing import Literal

from prismal.core.logging import get_logger

logger = get_logger(__name__)

SubagentPhase = Literal["start", "end", "error"]


def emit_subagent(
    phase: SubagentPhase,
    subagent_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    parent_id: str | None = None,
    code: str | None = None,
) -> None:
    """Write one sub-agent lifecycle payload to the run's ``custom`` stream (never raises)."""
    payload: dict[str, str] = {"type": "subagent", "phase": phase, "id": subagent_id}
    for key, value in (
        ("name", name),
        ("description", description),
        ("parent_id", parent_id),
        ("code", code),
    ):
        if value is not None:
            payload[key] = value
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # outside a runnable context: nothing to stream to
        return
    try:
        writer(payload)
    except Exception as exc:
        logger.debug("subagent_emit_failed", error=type(exc).__name__)


__all__ = ["SubagentPhase", "emit_subagent"]
