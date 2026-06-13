"""Cost & Budget Governance (Phase C).

The enforcement layer atop ``prismal/monitoring/`` (observation): meter real
usage per run, compare it to a :class:`Budget`, and cut off (soft = degrade,
hard = abort) when exceeded. Opt-in via ``settings.budget_enabled``.

See ``docs/budget.md`` and ``specs/cost-budget-governance/``.
"""

from __future__ import annotations

from prismal.budget.guard import BudgetGuard, make_budget_guard_fn
from prismal.budget.meter import CostMeter
from prismal.budget.resolve import get_budget_guard, resolve_budget, seed_budget_run
from prismal.budget.types import (
    Budget,
    BudgetScope,
    BudgetStatus,
    Degradation,
    TokenCounts,
    Usage,
)
from prismal.budget.usage import extract_token_usage

__all__ = [
    "Budget",
    "BudgetGuard",
    "BudgetScope",
    "BudgetStatus",
    "CostMeter",
    "Degradation",
    "TokenCounts",
    "Usage",
    "extract_token_usage",
    "get_budget_guard",
    "make_budget_guard_fn",
    "resolve_budget",
    "seed_budget_run",
]
