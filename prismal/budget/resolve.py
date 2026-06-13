"""Budget resolution and per-run seeding (Phase C — SPEC-CST-RES-001).

``resolve_budget`` turns ``Settings`` (optionally per-tenant) into a
:class:`Budget`. ``seed_budget_run`` builds the per-run ``{meter, guard}`` when
``budget_enabled`` and stores it in an **in-process registry keyed by
session_id** — never in checkpointed state, so a live ``CostMeter`` (which may
hold a SQLite-backed ``CostTracker``) never reaches the checkpoint serializer
(same principle as the path-based media descriptors). ``get_budget_guard`` reads
it back at the enforcement sites; ``clear_budget_run`` releases it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.budget.guard import BudgetGuard
from prismal.budget.meter import CostMeter
from prismal.budget.types import Budget, BudgetScope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.agents.state import AgentState
    from prismal.core.config import Settings
    from prismal.monitoring.cost_tracker import CostTracker
    from prismal.security.audit import AuditLogger

# session_id -> {"meter": CostMeter, "guard": BudgetGuard}. In-process only.
_RUN_ENGINES: dict[str, dict[str, Any]] = {}

_DEFAULT_KEY = "_default"


def _run_key(state: Mapping[str, Any]) -> str:
    """Derive the registry key for a run from its state (the session id)."""
    if isinstance(state, dict):
        sid = state.get("session_id")
        if sid:
            return str(sid)
    return _DEFAULT_KEY


def _turn_signature(state: Mapping[str, Any]) -> int:
    """Count user turns (``HumanMessage``s) — a cheap per-turn signature.

    Stays constant across the supervisor's many hops within one turn and
    increments when a new user message arrives, so the meter resets per turn.
    """
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if getattr(m, "type", None) == "human")


def resolve_budget(settings: Settings, *, org_id: str | None = None) -> Budget:  # noqa: ARG001 — org_id is the documented per-tenant hook (Phase R)
    """Build a :class:`Budget` from *settings*.

    ``org_id`` is accepted for per-tenant resolution: a host threads tenant
    ceilings via Phase R's ``apply_org_overrides`` so the ``settings`` passed
    here are already tenant-resolved; the budget simply follows.
    """
    return Budget(
        max_tokens=settings.budget_max_tokens,
        max_cost_usd=settings.budget_max_cost_usd,
        max_calls=settings.budget_max_calls,
        max_wall_clock_s=settings.budget_max_wall_clock_s,
        scope=BudgetScope(settings.budget_scope),
    )


def seed_budget_run(
    state: AgentState,
    settings: Settings,
    *,
    org_id: str | None = None,
    session_id: str | None = None,
    cost_tracker: CostTracker | None = None,
    audit: AuditLogger | None = None,
) -> None:
    """Install the per-run budget engine in the registry.

    No-op when ``settings.budget_enabled`` is False, so the disabled path carries
    zero state and is byte-for-byte unchanged. Writes only a serializable marker
    (``{"enabled": True, "scope": ...}``) under ``state['metadata']['budget']``;
    the live engine stays in the in-process registry.
    """
    if not settings.budget_enabled:
        return
    key = session_id or _run_key(state)
    meter = CostMeter(
        settings=settings,
        cost_tracker=cost_tracker,
        session_id=None if key == _DEFAULT_KEY else key,
        tenant=org_id,
    )
    guard = BudgetGuard(
        resolve_budget(settings, org_id=org_id),
        meter,
        soft_ratio=settings.budget_soft_ratio,
        hard_cap=settings.budget_hard_cap,
        audit=audit,
    )
    _RUN_ENGINES[key] = {"meter": meter, "guard": guard, "turn": _turn_signature(state)}
    metadata = state.setdefault("metadata", {})
    metadata["budget"] = {"enabled": True, "scope": settings.budget_scope}


def maybe_seed_budget_run(
    state: AgentState,
    settings: Settings,
    *,
    org_id: str | None = None,
    cost_tracker: CostTracker | None = None,
    audit: AuditLogger | None = None,
) -> None:
    """Seed once per turn — idempotent across the supervisor's hops.

    Re-seeds (fresh meter) only when the user-turn signature changes, so usage
    accumulates within a turn but resets between turns (turn-scope default).
    No-op when disabled.
    """
    if not settings.budget_enabled:
        return
    existing = _RUN_ENGINES.get(_run_key(state))
    if existing is not None and existing.get("turn") == _turn_signature(state):
        return
    seed_budget_run(state, settings, org_id=org_id, cost_tracker=cost_tracker, audit=audit)


def get_budget_guard(state: AgentState) -> BudgetGuard | None:
    """Return the per-run :class:`BudgetGuard`, or None when disabled/unseeded."""
    bucket = _RUN_ENGINES.get(_run_key(state))
    if not bucket:
        return None
    guard = bucket.get("guard")
    return guard if isinstance(guard, BudgetGuard) else None


def clear_budget_run(state: AgentState) -> None:
    """Release the per-run engine for *state* (idempotent)."""
    _RUN_ENGINES.pop(_run_key(state), None)
