"""``python -m prismal.decisions`` — calibration and benchmark tooling (Phase DEC).

* ``calibrate`` (DEC3-06) turns a labeled JSONL set plus the matching raw
  backend predictions (one JSONL row per example: ``{question_id, kind,
  probabilities, confidence?}``) into ``<out-dir>/<decision_id>@<model>.json``.
  Prediction collection (running Laya / Jev over the labeled states) happens
  out-of-band.
* ``bench`` (DEC6-04) measures latency and metered cost of one configured
  backend on a fixed EN/ES question set. It calls the **live** backend (spends
  real requests) — opt-in, never run by the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from prismal.decisions.calibration import (
    CalibrationStore,
    build_calibration_record,
    load_labeled_set,
)
from prismal.decisions.types import Decision, DecisionKind, OptionSpec, QuestionSpec

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.decisions.ports import DecisionPort


def _load_predictions(path: Path, model: str) -> list[Decision]:
    out: list[Decision] = []
    for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            kind = DecisionKind(row.get("kind", "choice"))
            probs = {str(k): float(v) for k, v in dict(row["probabilities"]).items()}
            labels = tuple(probs)
            if kind is DecisionKind.NOUL:
                q = QuestionSpec(id=row["question_id"], kind=kind, instructions="")
            elif kind is DecisionKind.SCORE:
                q = QuestionSpec(id=row["question_id"], kind=kind, instructions="", levels=labels)
            else:
                q = QuestionSpec(
                    id=row["question_id"],
                    kind=kind,
                    instructions="",
                    options=tuple(OptionSpec(lab, lab) for lab in labels),
                )
            out.append(
                Decision.from_probabilities(
                    q,
                    probs,
                    backend=str(row.get("backend", "offline")),
                    model=model,
                    spread_confidence=float(row.get("confidence", 0.0) or 0.0),
                )
            )
        except Exception as exc:
            raise ValueError(f"{path}: malformed prediction on line {lineno}") from exc
    return out


def _calibrate(args: argparse.Namespace) -> int:
    try:
        examples = [
            e for e in load_labeled_set(args.labeled) if e.question_id == args.decision_id
        ] or load_labeled_set(args.labeled)
        preds = _load_predictions(Path(args.predictions), args.model)
        if len(preds) != len(examples):
            print(
                f"error: {len(preds)} predictions for {len(examples)} labeled examples",
                file=sys.stderr,
            )
            return 2
        record = build_calibration_record(
            preds,
            examples,
            decision_id=args.decision_id,
            model=args.model,
            method=args.method,
            target_precision=args.target_precision,
            floor=args.floor,
            version=args.version,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    path = CalibrationStore(args.out_dir).save(record)
    print(
        json.dumps(
            {
                "path": str(path),
                "tau": record.tau,
                "ece_before": round(record.ece_before, 4),
                "ece_after": round(record.ece_after, 4),
                "n_by_language": dict(record.n_by_language),
                "precision_by_language": {
                    k: round(v, 4) for k, v in record.precision_by_language.items()
                },
            },
            indent=2,
        )
    )
    return 0


_BENCH_QUESTIONS = {
    "route.supervisor": QuestionSpec(
        id="route.supervisor",
        kind=DecisionKind.CHOICE,
        instructions="Choose which agent should handle the request in the state.",
        options=(
            OptionSpec("coder", "Writes, edits or debugs source code."),
            OptionSpec("researcher", "Searches the web for public information."),
            OptionSpec("END", "No specialist needed: small talk or a direct answer."),
        ),
    ),
    "rag.chunk_relevance": QuestionSpec(
        id="rag.chunk_relevance",
        kind=DecisionKind.NOUL,
        instructions="Is the chunk relevant to the query in the state?",
    ),
}
_BENCH_STATES = (
    {"user": "Write a Python function that reverses a linked list."},
    {"user": "¿Quién ganó el mundial de fútbol de 2010?", "chunk": "España ganó en 2010."},
    {"user": "hola, ¿qué tal?"},
    {"user": "Find recent news about LangGraph releases.", "chunk": "Weather is sunny."},
)


async def run_bench(backend: DecisionPort, *, n: int) -> dict[str, object]:
    """Run ``n`` rounds over the fixed states; return latency and outcome stats."""
    from prismal.decisions.types import GatePolicy

    latencies: list[float] = []
    abstained = 0
    total = 0
    policy = GatePolicy(timeout_ms=10_000)
    for _ in range(n):
        for state in _BENCH_STATES:
            start = time.perf_counter()
            out = await backend.decide(state, _BENCH_QUESTIONS, policy=policy)
            latencies.append((time.perf_counter() - start) * 1000.0)
            total += len(out)
            abstained += sum(1 for d in out.values() if d.abstained)
    ordered = sorted(latencies)
    return {
        "backend": backend.name,
        "model": backend.model,
        "requests": len(latencies),
        "p50_ms": round(statistics.median(ordered), 1) if ordered else 0.0,
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1) if ordered else 0.0,
        "abstain_ratio": round(abstained / total, 3) if total else 0.0,
    }


def _bench(args: argparse.Namespace) -> int:
    from prismal.budget.meter import CostMeter
    from prismal.core.config import get_settings
    from prismal.decisions.adapters.factory import build_backend

    settings = get_settings()
    meter = CostMeter(settings=settings)
    backend = build_backend(args.backend, settings, cost_meter=meter)
    if backend is None:
        print(f"error: backend {args.backend!r} is not configured", file=sys.stderr)
        return 2

    async def _go() -> dict[str, object]:
        try:
            return await run_bench(backend, n=args.n)
        finally:
            await backend.aclose()

    report = asyncio.run(_go())
    report["cost_usd"] = round(meter.usage.cost_usd, 8)
    report["input_tokens"] = meter.usage.prompt_tokens
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m prismal.decisions")
    sub = parser.add_subparsers(dest="command")
    cal = sub.add_parser("calibrate", help="Fit a calibration record from labeled predictions")
    cal.add_argument("--labeled", required=True, help="Labeled JSONL set")
    cal.add_argument("--predictions", required=True, help="Raw predictions JSONL (same order)")
    cal.add_argument("--decision-id", required=True)
    cal.add_argument("--model", required=True, help="Pinned model the predictions came from")
    cal.add_argument("--method", choices=["temperature", "isotonic"], default="temperature")
    cal.add_argument("--target-precision", type=float, default=0.97)
    cal.add_argument("--floor", type=float, default=0.90, help="Tier default τ (raise-only)")
    cal.add_argument("--version", default="v1")
    cal.add_argument("--out-dir", default="config/decision_calibration")
    bench = sub.add_parser("bench", help="Latency / cost of one live backend (spends requests)")
    bench.add_argument("--backend", choices=["laya", "jev", "llm"], required=True)
    bench.add_argument("--n", type=int, default=5, help="Rounds over the fixed state set")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "calibrate":
        return _calibrate(args)
    if args.command == "bench":
        return _bench(args)
    parser.print_help()
    return 2
