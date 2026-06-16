"""Regression gate: scorecard vs committed baseline (SPEC-EVL-REG-001).

``compare`` is a pure diff with a per-metric tolerance. ``pass_rate`` is
"higher is better" (a drop beyond tolerance regresses); ``avg_steps``,
``tool_error_rate`` and ``avg_cost_usd`` are "lower is better" (a rise beyond a
*relative* tolerance regresses, with an absolute floor when the baseline is 0).
Baselines are committed under ``tests/eval/baselines/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismal.eval.types import Scorecard


@dataclass(frozen=True)
class RegressionResult:
    """Whether the current scorecard held the line against its baseline."""

    passed: bool
    regressions: list[str]


def compare(
    current: Scorecard, baseline: Scorecard, *, tolerance: float = 0.02
) -> RegressionResult:
    """Compare *current* against *baseline*; fail on any regression beyond tolerance.

    Args:
        current: The scorecard from this run.
        baseline: The committed reference scorecard.
        tolerance: Per-metric slack (absolute for ``pass_rate``; relative for the
            "lower is better" metrics, with an absolute floor when baseline is 0).

    Returns:
        A :class:`RegressionResult`; ``passed`` is ``False`` if any metric
        regressed.
    """
    regressions: list[str] = []

    if current.pass_rate + tolerance < baseline.pass_rate:
        regressions.append(f"pass_rate dropped {baseline.pass_rate:.3f} → {current.pass_rate:.3f}")

    for name, cur, base in (
        ("avg_steps", current.avg_steps, baseline.avg_steps),
        ("tool_error_rate", current.tool_error_rate, baseline.tool_error_rate),
        ("avg_cost_usd", current.avg_cost_usd, baseline.avg_cost_usd),
    ):
        if _rose(cur, base, tolerance):
            regressions.append(f"{name} rose {base:.4f} → {cur:.4f}")

    return RegressionResult(passed=not regressions, regressions=regressions)


def _rose(current: float, baseline: float, tolerance: float) -> bool:
    """True if a 'lower is better' metric rose beyond tolerance."""
    if baseline > 0:
        return current > baseline * (1 + tolerance)
    # Baseline is zero: any rise above the absolute tolerance floor regresses.
    return current > tolerance


__all__ = ["RegressionResult", "compare"]
