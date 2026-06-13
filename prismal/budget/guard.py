"""The BudgetGuard — compare usage to a budget and cut off (Phase C — SPEC-CST-GRD-001).

``check()`` is a pure, O(1) verdict (within / soft / hard) over the meter's
current usage. ``enforce()`` adds the side effects: a hash-first audit record and,
on a hard cap when ``hard_cap`` is set, raising :class:`BudgetExceeded`.
``make_budget_guard_fn`` adapts a guard to the ``budget_guard_fn`` callable the
expensive patterns consume.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

from prismal.budget.types import Budget, BudgetStatus, Degradation, Usage
from prismal.core.exceptions import BudgetExceeded
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from prismal.budget.meter import CostMeter
    from prismal.security.audit import AuditLogger

logger = get_logger(__name__)

# (dimension name, used-from-usage, limit-from-budget)
_Dimension = tuple[str, Callable[[Usage], float], Callable[[Budget], float]]
_DIMENSIONS: tuple[_Dimension, ...] = (
    ("tokens", lambda u: float(u.total_tokens), lambda b: float(b.max_tokens)),
    ("cost", lambda u: float(u.cost_usd), lambda b: float(b.max_cost_usd)),
    ("calls", lambda u: float(u.calls), lambda b: float(b.max_calls)),
    ("wall_clock", lambda u: float(u.wall_clock_s), lambda b: float(b.max_wall_clock_s)),
)


class BudgetGuard:
    """Enforces a :class:`Budget` over a :class:`CostMeter`."""

    def __init__(
        self,
        budget: Budget,
        meter: CostMeter,
        *,
        soft_ratio: float = 0.8,
        hard_cap: bool = True,
        audit: AuditLogger | None = None,
    ) -> None:
        """Initialize BudgetGuard."""
        self.budget = budget
        self.meter = meter
        self.soft_ratio = soft_ratio
        self.hard_cap = hard_cap
        self._audit = audit

    def check(self) -> BudgetStatus:
        """Return the current verdict without side effects."""
        usage = self.meter.usage
        hard_dim: tuple[str, float] | None = None  # (dimension, ratio)
        soft_dim: tuple[str, float] | None = None

        for name, used_of, limit_of in _DIMENSIONS:
            limit = limit_of(self.budget)
            if limit <= 0:  # unlimited dimension
                continue
            used = used_of(usage)
            ratio = used / limit
            if used >= limit:
                if hard_dim is None or ratio > hard_dim[1]:
                    hard_dim = (name, ratio)
            elif ratio >= self.soft_ratio and (soft_dim is None or ratio > soft_dim[1]):
                soft_dim = (name, ratio)

        if hard_dim is not None:
            return BudgetStatus(
                within=False,
                soft_exceeded=False,
                hard_exceeded=True,
                breached_dimension=hard_dim[0],
                usage=usage,
                budget=self.budget,
            )
        if soft_dim is not None:
            return BudgetStatus(
                within=False,
                soft_exceeded=True,
                hard_exceeded=False,
                breached_dimension=soft_dim[0],
                usage=usage,
                budget=self.budget,
            )
        return BudgetStatus(
            within=True,
            soft_exceeded=False,
            hard_exceeded=False,
            breached_dimension=None,
            usage=usage,
            budget=self.budget,
        )

    def enforce(self) -> BudgetStatus:
        """Check, audit any cutoff, and raise on a hard cap when ``hard_cap``."""
        status = self.check()
        if status.hard_exceeded:
            dim = status.breached_dimension or "tokens"
            used, limit = self._dimension_values(dim)
            self._record_cutoff(dim, used, limit, action="abort")
            logger.warning(
                "budget_hard_cutoff",
                dimension=dim,
                used=used,
                limit=limit,
                scope=self.budget.scope.value,
                aborting=self.hard_cap,
            )
            if self.hard_cap:
                raise BudgetExceeded(dim, used, limit, scope=self.budget.scope.value)
        elif status.soft_exceeded:
            dim = status.breached_dimension or "tokens"
            used, limit = self._dimension_values(dim)
            self._record_cutoff(dim, used, limit, action="degrade")
            logger.warning(
                "budget_soft_cutoff",
                dimension=dim,
                used=used,
                limit=limit,
                scope=self.budget.scope.value,
            )
        return status

    def degradation(self) -> Degradation:
        """Translate the current status into pattern degradation advice."""
        status = self.check()
        if status.hard_exceeded:
            return Degradation(terminate=True, reason=f"hard cap on {status.breached_dimension}")
        if status.soft_exceeded:
            return Degradation(reduce=True, reason=f"soft cap on {status.breached_dimension}")
        return Degradation()

    # ── internals ─────────────────────────────────────────────────────────────

    def _dimension_values(self, dimension: str) -> tuple[float, float]:
        usage = self.meter.usage
        for name, used_of, limit_of in _DIMENSIONS:
            if name == dimension:
                return used_of(usage), limit_of(self.budget)
        return 0.0, 0.0

    def _record_cutoff(self, dimension: str, used: float, limit: float, *, action: str) -> None:
        from prismal.monitoring.otel import OTelManager

        with suppress(Exception):
            OTelManager().increment_counter(
                "budget_cutoffs", attributes={"dimension": dimension, "action": action}
            )
        if self._audit is None:
            return
        with suppress(Exception):
            self._audit.log_event(
                "budget_cutoff",
                {
                    "dimension": dimension,
                    "used": used,
                    "limit": limit,
                    "scope": self.budget.scope.value,
                    "action": action,
                },
            )


def make_budget_guard_fn(
    guard: BudgetGuard | None,
) -> Callable[[dict[str, object]], Awaitable[bool]]:
    """Adapt a guard to the ``budget_guard_fn`` patterns accept.

    The returned async ``fn(ctx) -> bool`` returns ``True`` to proceed and
    ``False`` to degrade/stop; it raises :class:`BudgetExceeded` on a hard cap
    when ``guard.hard_cap``. A ``None`` guard yields a zero-overhead always-True
    function (the disabled path).
    """

    if guard is None:

        async def _always(_ctx: dict[str, object]) -> bool:
            return True

        return _always

    async def _checked(_ctx: dict[str, object]) -> bool:
        status = guard.enforce()  # audits; raises on hard + hard_cap
        return status.within

    return _checked
