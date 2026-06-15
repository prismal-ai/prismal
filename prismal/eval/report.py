"""Scorecard rendering: JSON, Markdown, optional Langfuse (SPEC-EVL-RPT-001).

Pure renderers (``to_json``/``to_markdown``) plus an opt-in ``to_langfuse``
export gated by ``settings.eval_langfuse_export``. None of these mutate the
scorecard or touch the agent runtime.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.eval.types import Scorecard

logger = get_logger(__name__)


def to_json(card: Scorecard) -> str:
    """Serialise the scorecard to indented JSON (enums render as strings)."""
    return json.dumps(dataclasses.asdict(card), indent=2, sort_keys=True, default=str)


def to_markdown(card: Scorecard) -> str:
    """Render a human-readable per-release scorecard in Markdown."""
    lines = [
        f"# Eval scorecard — `{card.suite}`",
        "",
        f"- **Version:** {card.version}",
        f"- **Pass rate:** {card.pass_rate:.1%}",
        f"- **Avg steps:** {card.avg_steps:.2f}",
        f"- **Tool-error rate:** {card.tool_error_rate:.1%}",
        f"- **Avg cost (USD):** {card.avg_cost_usd:.6f}",
        f"- **Avg latency (ms):** {card.avg_latency_ms:.1f}",
        "",
        "| Case | Passed | Steps | Tool calls | Tool errors | Assertions |",
        "|---|---|---|---|---|---|",
    ]
    for case in card.cases:
        traj = case.trajectory
        passed_assertions = sum(1 for ar in case.assertion_results if ar.passed)
        total_assertions = len(case.assertion_results)
        lines.append(
            f"| `{case.case_id}` | {'✅' if case.passed else '❌'} | "
            f"{len(traj.steps)} | {traj.tool_calls} | {traj.tool_errors} | "
            f"{passed_assertions}/{total_assertions} |"
        )
    return "\n".join(lines) + "\n"


def to_langfuse(card: Scorecard, *, settings: Settings | None = None) -> None:
    """Export the scorecard to Langfuse evals when enabled (opt-in, best-effort).

    A no-op unless ``settings.eval_langfuse_export`` is ``True``. Export failures
    are logged, never raised — reporting must not break a run.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    if not settings.eval_langfuse_export:
        return
    try:  # pragma: no cover - exercised only with Langfuse configured
        from prismal.monitoring.langfuse_client import LangfuseManager

        manager = LangfuseManager()
        if not manager.enabled:
            logger.warning("eval.langfuse_unavailable", suite=card.suite)
            return
        manager.score_trace(
            trace_id=f"eval:{card.suite}:{card.version}",
            name=f"eval:{card.suite}",
            value=card.pass_rate,
            comment=f"pass_rate over {len(card.cases)} cases",
        )
    except Exception as exc:  # pragma: no cover - best-effort export
        logger.warning("eval.langfuse_export_failed", suite=card.suite, error=str(exc))


__all__ = ["to_json", "to_langfuse", "to_markdown"]
