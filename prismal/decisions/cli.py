"""``python -m prismal.decisions`` — calibration and benchmark tooling (Phase DEC).

* ``calibrate`` (DEC3-06) turns a labeled JSONL set plus the matching raw
  backend predictions (one JSONL row per example: ``{question_id, kind,
  probabilities, confidence?}``) into ``<out-dir>/<decision_id>@<model>.json``.
  Prediction collection (running Laya / Jev over the labeled states) happens
  out-of-band.
* ``bench`` (DEC6-04) measures latency and metered cost of one configured
  backend on a fixed EN/ES question set. It calls the **live** backend (spends
  real requests) — opt-in, never run by the test suite.
* ``cases-check`` (DEC3-07) validates a hand-labelled case file against the
  production question and prints its label / language / tag balance.
* ``collect`` (DEC3-07) asks one Ollama sub-tier the production question for
  every case and writes the ``labeled`` / ``preds`` pair ``calibrate`` reads
  (see :mod:`prismal.decisions.calset`). Live, opt-in.
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

    from prismal.core.config import Settings
    from prismal.decisions.adapters.ollama import OllamaDecisionAdapter
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


def _cases_check(args: argparse.Namespace) -> int:
    from prismal.decisions.calset import check_cases, load_cases, resolve_question

    try:
        question = resolve_question(args.decision_id, routes_profile=args.routes_profile)
        report = check_cases(load_cases(args.cases), question)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 1 if report.errors else 0


def _collect_backend(*, tier: str, settings: Settings) -> OllamaDecisionAdapter:
    """One Ollama sub-tier configured for calibration runs, not for serving.

    One question per request (``max_batch=1``), no rolling-p95 skipping on
    ``accurate`` and no stored calibration/batch records: the run must see every
    case and record the raw distribution.
    """
    from prismal.decisions.adapters.ollama import OllamaDecisionAdapter, resolve_base_url

    model_name = settings.decision_ollama_models.get(tier)
    if not model_name:
        raise ValueError(f"no Ollama model configured for tier {tier!r}")
    return OllamaDecisionAdapter(
        base_url=resolve_base_url(settings),
        model_name=model_name,
        tier=tier,  # type: ignore[arg-type]
        keep_alive=settings.decision_ollama_keep_alive,
        min_version=settings.decision_ollama_min_version,
        max_batch=1,
        accurate_max_p95_ms=10**9,
        budget_ms=settings.decision_ollama_timeout_ms,
        calibration_dir=None,
        settings=settings,
    )


def _model_key(backend: object) -> str:
    """``<name>@<digest12>`` for a preflighted Ollama adapter, else ``backend.model``."""
    preflight = getattr(backend, "preflight", None)
    digest = getattr(preflight, "digest12", "") if preflight is not None else ""
    name = getattr(backend, "model_name", None) or getattr(backend, "model", "unknown")
    return f"{name}@{digest}" if digest else str(name)


def _collect(args: argparse.Namespace) -> int:
    from prismal.core.config import get_settings
    from prismal.decisions.calset import (
        check_cases,
        collect,
        load_cases,
        resolve_question,
        write_collect,
    )

    try:
        question = resolve_question(args.decision_id, routes_profile=args.routes_profile)
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        cases = cases[: args.limit]
    report = check_cases(cases, question)
    if report.errors:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        print("error: fix the case file before collecting", file=sys.stderr)
        return 1
    try:
        backend = _collect_backend(tier=args.tier, settings=get_settings())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  {done}/{total}", file=sys.stderr, flush=True)

    async def _go() -> tuple[object, str]:
        try:
            result = await collect(
                cases, question, backend, timeout_ms=args.timeout_ms, on_progress=progress
            )
            return result, _model_key(backend)
        finally:
            await backend.aclose()

    result, model_key = asyncio.run(_go())
    paths = write_collect(result, args.out_dir, model_key=model_key)  # type: ignore[arg-type]
    summary = json.loads(paths.summary.read_text("utf-8"))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in ("model", "n_cases", "n_predictions", "accuracy", "latency_ms")
            },
            indent=2,
        )
    )
    print(
        "next: python -m prismal.decisions calibrate"
        f" --labeled {paths.labeled} --predictions {paths.predictions}"
        f" --decision-id {question.id} --model {model_key}"
        f" --floor {_tier_floor(question)} --target-precision <target> --version v1"
    )
    return 0


def _tier_floor(question: QuestionSpec) -> float:
    from prismal.decisions.types import GatePolicy

    tau = GatePolicy().tau(question.risk_tier)
    return tau if tau is not None else 1.0


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for ``calibrate``, ``bench``, ``cases-check`` and ``collect``."""
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
    check = sub.add_parser("cases-check", help="Validate a labeled case file (DEC3-07)")
    check.add_argument("--cases", required=True, help="Case JSONL file")
    check.add_argument("--decision-id", required=True)
    check.add_argument("--routes-profile", choices=["base", "advanced"], default="base")
    col = sub.add_parser(
        "collect", help="Raw predictions of one Ollama sub-tier for a case file (live)"
    )
    col.add_argument("--cases", required=True, help="Case JSONL file")
    col.add_argument("--decision-id", required=True)
    col.add_argument("--tier", choices=["fast", "accurate"], required=True)
    col.add_argument("--routes-profile", choices=["base", "advanced"], default="base")
    col.add_argument("--out-dir", default="specs/typed-decision-models/data/runs")
    col.add_argument("--limit", type=int, default=0, help="Only the first N cases (pilot)")
    col.add_argument("--timeout-ms", type=int, default=60_000, help="Per-request timeout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the exit code (2 without a subcommand)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "calibrate":
        return _calibrate(args)
    if args.command == "bench":
        return _bench(args)
    if args.command == "cases-check":
        return _cases_check(args)
    if args.command == "collect":
        return _collect(args)
    parser.print_help()
    return 2
