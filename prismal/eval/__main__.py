"""Command-line entry point for the eval harness (SPEC-EVL-CLI-001).

Subcommands::

    python -m prismal.eval run     --suite SET.yaml [--live-api] [--baseline b.json]
                                   [--json out.json] [--markdown out.md]
    python -m prismal.eval redteam --corpus CORPUS.yaml [--baseline b.json] [--json out.json]
    python -m prismal.eval gate    --current run.json --baseline baseline.json [--tolerance 0.02]

Deterministic with fakes by default; ``--live-api`` opts into real models. Exit
code is ``0`` on success, ``1`` on a failed gate, matching CI conventions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.eval.regression import compare
from prismal.eval.report import to_json, to_markdown
from prismal.eval.runner import EvalRunner
from prismal.eval.types import EvalSet, Scorecard

if TYPE_CHECKING:
    from collections.abc import Callable


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``prismal.eval`` argument parser."""
    parser = argparse.ArgumentParser(prog="prismal.eval", description="Agent eval harness")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run an eval-set against the graph")
    run.add_argument("--suite", required=True, help="Path to the eval-set YAML")
    run.add_argument("--live-api", action="store_true", help="Use real models (opt-in)")
    run.add_argument("--baseline", help="Baseline scorecard JSON to gate against")
    run.add_argument("--json", dest="json_out", help="Write the scorecard JSON here")
    run.add_argument("--markdown", dest="md_out", help="Write the scorecard Markdown here")

    redteam = sub.add_parser("redteam", help="Run the adversarial corpus")
    redteam.add_argument("--corpus", required=True, help="Path to the red-team corpus YAML")
    redteam.add_argument("--live-api", action="store_true", help="Use real models (opt-in)")
    redteam.add_argument("--baseline", help="Baseline scorecard JSON to gate against")
    redteam.add_argument("--json", dest="json_out", help="Write the scorecard JSON here")

    gate = sub.add_parser("gate", help="Compare a scorecard against a baseline")
    gate.add_argument("--current", required=True, help="Current scorecard JSON")
    gate.add_argument("--baseline", required=True, help="Baseline scorecard JSON")
    gate.add_argument("--tolerance", type=float, default=None, help="Per-metric tolerance")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the requested subcommand."""
    args = build_parser().parse_args(argv)
    if args.command == "gate":
        return _cmd_gate(args)
    if args.command == "redteam":
        return _cmd_run(args, suite_path=args.corpus, loader=_load_corpus)
    return _cmd_run(args, suite_path=args.suite, loader=EvalSet.from_yaml)


def _cmd_run(args: argparse.Namespace, *, suite_path: str, loader: Callable[[str], EvalSet]) -> int:
    """Run a suite/corpus, render the scorecard, and optionally gate."""
    settings = get_settings()
    eval_set = loader(suite_path)
    runner = EvalRunner(settings=settings)
    card = asyncio.run(runner.run_set(eval_set))

    if getattr(args, "json_out", None):
        Path(args.json_out).write_text(to_json(card), encoding="utf-8")
    if getattr(args, "md_out", None):
        Path(args.md_out).write_text(to_markdown(card), encoding="utf-8")

    print(
        f"suite={card.suite} pass_rate={card.pass_rate:.1%} "
        f"avg_steps={card.avg_steps:.2f} tool_error_rate={card.tool_error_rate:.1%}"
    )

    baseline_path = getattr(args, "baseline", None)
    if baseline_path:
        tolerance = settings.eval_regression_tolerance
        result = compare(card, _load_scorecard(baseline_path), tolerance=tolerance)
        if not result.passed:
            for line in result.regressions:
                print(f"REGRESSION: {line}")
            return 1
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    """Compare two committed scorecards and exit non-zero on regression."""
    tolerance = (
        args.tolerance if args.tolerance is not None else get_settings().eval_regression_tolerance
    )
    current = _load_scorecard(args.current)
    baseline = _load_scorecard(args.baseline)
    result = compare(current, baseline, tolerance=tolerance)
    if result.passed:
        print(f"GATE PASS: {current.suite} held the line vs baseline")
        return 0
    for line in result.regressions:
        print(f"REGRESSION: {line}")
    return 1


def _load_corpus(path: str) -> EvalSet:
    """Load the red-team corpus.

    A red-team corpus is itself a valid eval-set (with ``security`` assertions),
    so it loads through :meth:`EvalSet.from_yaml`. V5 adds ``load_redteam_corpus``
    with corpus-specific validation; this stays the CLI seam for it.
    """
    return EvalSet.from_yaml(path)


def _load_scorecard(path: str) -> Scorecard:
    """Reconstruct a metrics-only :class:`Scorecard` from a saved JSON report."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Scorecard(
        suite=str(data.get("suite", "")),
        version=str(data.get("version", "")),
        pass_rate=float(data.get("pass_rate", 0.0)),
        avg_steps=float(data.get("avg_steps", 0.0)),
        tool_error_rate=float(data.get("tool_error_rate", 0.0)),
        avg_cost_usd=float(data.get("avg_cost_usd", 0.0)),
        avg_latency_ms=float(data.get("avg_latency_ms", 0.0)),
        cases=[],
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
