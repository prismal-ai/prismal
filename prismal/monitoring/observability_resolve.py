"""Per-run observability registry (Phase OBS — SPEC-OBS-RES-001, DD-OBS-002).

Mirrors :mod:`prismal.budget.resolve` exactly: :func:`seed_observability_run`
installs the live :class:`~prismal.agents.extension.ports.ObservabilityPort` in an
**in-process registry keyed by session_id** — never in checkpointed state, so a
live provider (which may hold in-memory span buffers) never reaches the
checkpoint serializer. ``state["metadata"]["observability"]`` carries only the
serializable marker ``{"enabled": True, "run_id": ...}``.

Idempotent per user-turn (turn-scope default): a run's telemetry accumulates
within a turn and a fresh run starts on the next one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from prismal.monitoring.observability import run_name_for

if TYPE_CHECKING:
    from prismal.agents.extension.ports import ObservabilityPort

# session_id -> {"provider": ObservabilityPort, "run_id": str, "turn": int}.
# In-process only; never serialized into checkpointed state.
_RUNS: dict[str, dict[str, object]] = {}


def seed_observability_run(
    session_id: str,
    provider: ObservabilityPort,
    *,
    agent_name: str,
    turn: int,
) -> str:
    """Install *provider* in the registry keyed by *session_id*; return the run_id.

    Idempotent per ``(session_id, turn)`` — a repeat call for the same turn keeps
    the already-seeded provider and returns the same ``run_id`` (mirrors
    ``budget/resolve.py::seed_budget_run``). A new ``turn`` replaces the entry so
    a fresh run starts.
    """
    existing = _RUNS.get(session_id)
    if existing is not None and existing.get("turn") == turn:
        return str(existing["run_id"])
    run_id = run_name_for(agent_name=agent_name, session_id=session_id, turn=turn)
    _RUNS[session_id] = {"provider": provider, "run_id": run_id, "turn": turn}
    return run_id


def get_observability_provider(session_id: str) -> ObservabilityPort | None:
    """Return the per-run provider for *session_id*, or ``None`` when unseeded."""
    entry = _RUNS.get(session_id)
    if entry is None:
        return None
    # The registry only ever stores ObservabilityPort instances under "provider".
    return cast("ObservabilityPort", entry["provider"])


def clear_observability_run(session_id: str) -> None:
    """Release the per-run provider for *session_id* (idempotent)."""
    _RUNS.pop(session_id, None)


__all__ = [
    "clear_observability_run",
    "get_observability_provider",
    "seed_observability_run",
]
