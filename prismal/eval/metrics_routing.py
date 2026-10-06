"""Routing cost/quality report (Phase AMR — SPEC-AMR-INT-002, PLAN §4).

Compares a static-baseline scorecard with a routed one: cost, quality
(``pass_rate``) and ``routing_savings_usd``. The gate enforces the PLAN target
(-40 % cost with ≤ 1 pt quality loss); ``check_routing_gate`` returns the
violations (empty = pass).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismal.eval.types import Scorecard

DEFAULT_MIN_SAVINGS_RATIO = 0.40
DEFAULT_MAX_QUALITY_DROP_PTS = 1.0


@dataclass(frozen=True)
class RoutingReport:
    """Static vs routed run, per case averages."""

    static_cost_usd: float
    routed_cost_usd: float
    savings_usd: float
    savings_ratio: float
    static_quality: float
    routed_quality: float
    quality_drop_pts: float  # percentage points (100 x pass-rate delta)


def build_routing_report(static: Scorecard, routed: Scorecard) -> RoutingReport:
    """Report for *routed* against the *static* baseline."""
    savings = static.avg_cost_usd - routed.avg_cost_usd
    ratio = savings / static.avg_cost_usd if static.avg_cost_usd > 0 else 0.0
    return RoutingReport(
        static_cost_usd=static.avg_cost_usd,
        routed_cost_usd=routed.avg_cost_usd,
        savings_usd=savings,
        savings_ratio=ratio,
        static_quality=static.pass_rate,
        routed_quality=routed.pass_rate,
        quality_drop_pts=(static.pass_rate - routed.pass_rate) * 100.0,
    )


def check_routing_gate(
    report: RoutingReport,
    *,
    min_savings_ratio: float = DEFAULT_MIN_SAVINGS_RATIO,
    max_quality_drop_pts: float = DEFAULT_MAX_QUALITY_DROP_PTS,
) -> list[str]:
    """Violations of the routing gate (empty list = pass)."""
    problems: list[str] = []
    if report.savings_ratio < min_savings_ratio:
        problems.append(f"savings {report.savings_ratio:.1%} below target {min_savings_ratio:.0%}")
    if report.quality_drop_pts > max_quality_drop_pts:
        problems.append(
            f"quality dropped {report.quality_drop_pts:.2f} pts (max {max_quality_drop_pts:.2f})"
        )
    return problems
