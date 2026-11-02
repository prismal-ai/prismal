"""DEC3-09: latency of one Ollama sub-tier under k concurrent decisions.

Uses the production path (``calset`` question + state builders, the calibration-only
``OllamaDecisionAdapter`` that ``collect`` uses) and one shared adapter, as one prismal
process would. For each concurrency level it reports per-decision p50/p95, the share of
decisions over a latency budget, and throughput. Run it once per ``OLLAMA_NUM_PARALLEL``
value (the server setting; restart Ollama between runs)::

    PRISMAL_LLM_PROVIDER="" uv run python specs/typed-decision-models/bench/concurrency_bench.py \\
        --cases specs/typed-decision-models/data/es/route.supervisor.jsonl \\
        --decision-id route.supervisor --tier accurate --concurrency 1 2 4 --n 60 \\
        --label num_parallel=1 --out /tmp/route-accurate-np1.json

Latency is wall time around ``decide`` (queueing included), which is what a caller's
budget sees; the adapter's own ``latency_ms`` is reported next to it. Each level runs on
its own slice of cases (and the warm-up on another), so no level is answered from the
prompt cache that an earlier level filled — a real turn is always a new state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


def _pct(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def _run_level(
    backend: Any, question: Any, states: list[dict[str, str]], k: int, budget_ms: float
) -> dict[str, Any]:
    from prismal.decisions.types import GatePolicy

    policy = GatePolicy(timeout_ms=60_000)
    sem = asyncio.Semaphore(k)
    wall: list[float] = []
    adapter_ms: list[float] = []
    abstained = 0

    async def one(state: dict[str, str]) -> None:
        nonlocal abstained
        async with sem:
            start = time.perf_counter()
            out = await backend.decide(state, {question.id: question}, policy=policy)
            wall.append((time.perf_counter() - start) * 1000)
            d = out.get(question.id)
            if d is None or d.abstained:
                abstained += 1
            else:
                adapter_ms.append(d.latency_ms)

    started = time.perf_counter()
    await asyncio.gather(*(one(s) for s in states))
    elapsed = time.perf_counter() - started
    return {
        "concurrency": k,
        "n": len(states),
        "abstained": abstained,
        "wall_p50_ms": round(statistics.median(wall), 1),
        "wall_p95_ms": round(_pct(wall, 0.95), 1),
        "adapter_p50_ms": round(statistics.median(adapter_ms), 1) if adapter_ms else None,
        "over_budget": round(sum(w > budget_ms for w in wall) / len(wall), 3),
        "throughput_per_s": round(len(states) / elapsed, 2),
    }


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    from prismal.core.config import get_settings
    from prismal.decisions.calset import build_case_state, load_cases, resolve_question
    from prismal.decisions.cli import _collect_backend, _model_key
    from prismal.decisions.types import GatePolicy

    settings = get_settings()
    question = resolve_question(args.decision_id, routes_profile="base")
    cases = load_cases(args.cases)
    needed = args.n * len(args.concurrency) + 3
    if len(cases) < needed:
        raise SystemExit(f"need {needed} cases for disjoint slices, have {len(cases)}")
    states = [dict(build_case_state(c).fields) for c in cases[:needed]]
    warm, states = states[-3:], states[:-3]
    backend = _collect_backend(tier=args.tier, settings=settings)
    budget = args.budget_ms or settings.decision_ollama_timeout_ms
    try:
        for state in warm:  # warm-up: load the model, fill the KV cache
            await backend.decide(
                state, {question.id: question}, policy=GatePolicy(timeout_ms=60_000)
            )
        levels = [
            await _run_level(backend, question, states[i * args.n : (i + 1) * args.n], k, budget)
            for i, k in enumerate(args.concurrency)
        ]
        return {
            "label": args.label,
            "decision_id": args.decision_id,
            "tier": args.tier,
            "model": _model_key(backend),
            "budget_ms": budget,
            "levels": levels,
        }
    finally:
        await backend.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--tier", choices=["fast", "accurate"], required=True)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--n", type=int, default=60, help="Cases per level (disjoint slices)")
    parser.add_argument("--budget-ms", type=float, default=0, help="Default: settings budget")
    parser.add_argument("--label", default="", help="Free text, e.g. num_parallel=2")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(main_async(args))
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", "utf-8")
    print(text, file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
