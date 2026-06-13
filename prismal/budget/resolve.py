"""Budget resolution and per-run seeding (Phase C — SPEC-CST-RES-001).

``resolve_budget`` turns ``Settings`` (optionally per-tenant) into a
:class:`Budget`. ``seed_budget_run`` installs a per-run ``{meter, guard}`` under
``state["metadata"]["budget"]`` when ``budget_enabled`` — mirroring the
``state["metadata"]["skynet"]`` / ``["kokoro"]`` convention — and is a no-op
otherwise so the disabled path carries zero state. ``get_budget_guard`` reads it
back at the enforcement sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.budget.guard import BudgetGuard
from prismal.budget.meter import CostMeter
from prismal.budget.types import Budget, BudgetScope

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.monitoring.cost_tracker import CostTracker
    from prismal.security.audit import AuditLogger


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
    state: dict[str, Any],
    settings: Settings,
    *,
    org_id: str | None = None,
    session_id: str | None = None,
    cost_tracker: CostTracker | None = None,
    audit: AuditLogger | None = None,
) -> None:
    """Install the per-run budget engine under ``state['metadata']['budget']``.

    No-op when ``settings.budget_enabled`` is False, so the disabled path is
    byte-for-byte unchanged.
    """
    if not settings.budget_enabled:
        return
    meter = CostMeter(
        settings=settings,
        cost_tracker=cost_tracker,
        session_id=session_id,
        tenant=org_id,
    )
    guard = BudgetGuard(
        resolve_budget(settings, org_id=org_id),
        meter,
        soft_ratio=settings.budget_soft_ratio,
        hard_cap=settings.budget_hard_cap,
        audit=audit,
    )
    metadata = state.setdefault("metadata", {})
    metadata["budget"] = {"meter": meter, "guard": guard}


def get_budget_guard(state: dict[str, Any]) -> BudgetGuard | None:
    """Return the per-run :class:`BudgetGuard`, or None when disabled/unseeded."""
    metadata = state.get("metadata") if isinstance(state, dict) else None
    if not isinstance(metadata, dict):
        return None
    bucket = metadata.get("budget")
    if not isinstance(bucket, dict):
        return None
    guard = bucket.get("guard")
    return guard if isinstance(guard, BudgetGuard) else None
