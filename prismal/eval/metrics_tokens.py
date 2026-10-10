"""Token-efficiency report for the eval harness (Phase TKE — SPEC-TKE-ACC-005).

Turns a run's :class:`CaseResult` list into a :class:`TokenReport` (tokens per
successful task, cache-hit ratio, solve rate, trajectory length, input cost per
success), persists/loads a committed baseline, and checks a candidate against
it — the CI token-regression gate (TKE9-03). Pure functions; no runtime imports.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.eval.types import CaseResult

DEFAULT_BASELINE_PATH = "tests/eval/baselines/tokens.json"


@dataclass(frozen=True)
class TokenReport:
    """Suite-level token efficiency metrics."""

    tokens_per_successful_task: float
    cache_hit_ratio: float
    solve_rate: float
    mean_trajectory_length: float  # LLM calls per task
    input_cost_usd_per_success: float


def build_token_report(results: Sequence[CaseResult]) -> TokenReport:
    """Aggregate per-case trajectories into a :class:`TokenReport`.

    Per-success figures divide the *whole* run's spend by the number of solved
    cases, so failed attempts count against efficiency.
    """
    n = len(results)
    if n == 0:
        return TokenReport(0.0, 0.0, 0.0, 0.0, 0.0)
    trajectories = [r.trajectory for r in results]
    successes = sum(1 for r in results if r.passed)
    total_tokens = sum(t.tokens for t in trajectories)
    total_prompt = sum(t.prompt_tokens for t in trajectories)
    total_cached = sum(t.cache_read_tokens for t in trajectories)
    total_input_cost = sum(t.input_cost_usd for t in trajectories)
    return TokenReport(
        tokens_per_successful_task=(total_tokens / successes) if successes else 0.0,
        cache_hit_ratio=(total_cached / total_prompt) if total_prompt else 0.0,
        solve_rate=successes / n,
        mean_trajectory_length=sum(t.llm_calls for t in trajectories) / n,
        input_cost_usd_per_success=(total_input_cost / successes) if successes else 0.0,
    )


def save_baseline(report: TokenReport, path: str = DEFAULT_BASELINE_PATH) -> None:
    """Write *report* as the committed JSON baseline."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dataclasses.asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_baseline(path: str = DEFAULT_BASELINE_PATH) -> TokenReport:
    """Read a JSON baseline written by :func:`save_baseline`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = {f.name for f in dataclasses.fields(TokenReport)}
    return TokenReport(**{k: float(v) for k, v in data.items() if k in fields})


def check_token_regression(
    current: TokenReport,
    baseline: TokenReport,
    *,
    max_token_regression: float = 0.10,
    max_hit_ratio_drop: float = 0.10,
) -> list[str]:
    """Return one message per violated threshold; an empty list means pass."""
    problems: list[str] = []
    base_tokens = baseline.tokens_per_successful_task
    if base_tokens > 0 and current.tokens_per_successful_task > base_tokens * (
        1.0 + max_token_regression
    ):
        problems.append(
            f"tokens_per_successful_task {current.tokens_per_successful_task:.1f} exceeds "
            f"baseline {base_tokens:.1f} by more than {max_token_regression:.0%}"
        )
    if baseline.cache_hit_ratio - current.cache_hit_ratio > max_hit_ratio_drop:
        problems.append(
            f"cache_hit_ratio {current.cache_hit_ratio:.3f} dropped more than "
            f"{max_hit_ratio_drop:.2f} below baseline {baseline.cache_hit_ratio:.3f}"
        )
    return problems


__all__ = [
    "DEFAULT_BASELINE_PATH",
    "TokenReport",
    "build_token_report",
    "check_token_regression",
    "load_baseline",
    "save_baseline",
]
