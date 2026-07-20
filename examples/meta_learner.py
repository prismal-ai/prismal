"""MetaLearner demo: score execution traces and run the review cycle (T-212).

Feeds the meta-learner deterministic fake traces (in production ``fetch_traces``
queries the Langfuse HTTP API) and a fake LLM registry, so the full cycle —
score → flag low performers → generate proposals → save with a human-review
sentinel — runs OFFLINE with no LLM, no network, and no Langfuse credentials.
Proposals are written to a temporary directory, never into ``skills/custom/``.

Run::

    python examples/meta_learner.py
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import prismal.agents.meta_learner as meta_learner_module
from prismal.agents.meta_learner import MetaLearner, TraceScore

# Fake Langfuse traces (what ``fetch_traces`` would return for the last 7 days).
FAKE_TRACES: list[dict[str, Any]] = [
    {
        "id": "trace-001",
        "input": "Summarise the Q2 report",
        "output": "The Q2 report shows 12% revenue growth driven by the EU region.",
    },
    {
        "id": "trace-002",
        "input": "Convert 5 km to miles",
        "output": "I am not sure how to do unit conversions.",
    },
    {
        "id": "trace-003",
        "input": "List three uses of DuckDB",
        "output": "Analytics on Parquet files, embedded OLAP, and fast CSV queries.",
    },
]

# Deterministic self-assessment per trace (what the evaluator LLM would emit).
FAKE_SCORES: dict[str, dict[str, Any]] = {
    "trace-001": {"task_completion": 0.95, "accuracy": 0.9, "efficiency": 0.85, "issues": []},
    "trace-002": {
        "task_completion": 0.2,
        "accuracy": 0.4,
        "efficiency": 0.3,
        "issues": ["failed to answer", "missing unit-conversion skill"],
    },
    "trace-003": {"task_completion": 0.9, "accuracy": 0.95, "efficiency": 0.8, "issues": []},
}

FAKE_PROPOSALS = (
    "1. **Root causes**: no unit-conversion capability is routed or available.\n"
    "2. **System prompt improvements**: add 'For unit conversions, compute "
    "step by step and show the formula.'\n"
    "3. **New skills to add**: a unit_converter skill (km/miles, C/F, kg/lb).\n"
    "4. **Routing improvements**: route conversion intents to data_analyst."
)


class _FakeLLM:
    """Deterministic stand-in for the evaluator/proposal LLM."""

    async def ainvoke(self, messages: list[Any]) -> Any:
        prompt = str(messages[0].content)
        match = re.search(r"TRACE ID: (\S+)", prompt)
        # A "TRACE ID:" prompt is score_traces(); otherwise generate_proposals().
        content = json.dumps(FAKE_SCORES.get(match.group(1), {})) if match else FAKE_PROPOSALS

        class _Response:
            def __init__(self, text: str) -> None:
                self.content = text

        return _Response(content)


class FakeProviderRegistry:
    """A ``ProviderRegistry`` stand-in (no real provider, no network)."""

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> _FakeLLM:
        return _FakeLLM()


class OfflineMetaLearner(MetaLearner):
    """MetaLearner whose trace source is a canned list instead of Langfuse."""

    async def fetch_traces(self, days: int = 7) -> list[dict[str, Any]]:
        """Return the fake traces (production queries the Langfuse API)."""
        return FAKE_TRACES


def _print_score(score: TraceScore) -> None:
    flag = "LOW " if score.overall < 0.6 else "ok  "
    print(
        f"  [{flag}] {score.trace_id}: overall={score.overall:.2f} "
        f"(completion={score.task_completion:.2f}, accuracy={score.accuracy:.2f}, "
        f"efficiency={score.efficiency:.2f})"
    )
    for issue in score.issues:
        print(f"           issue: {issue}")


async def main() -> None:
    """Score fake traces, then run the full review/improvement cycle."""
    # The meta-learner wires ProviderRegistry().get_llm() internally; swap in
    # the deterministic fake. A real deployment leaves the registry untouched.
    meta_learner_module.ProviderRegistry = FakeProviderRegistry  # type: ignore[assignment,misc]

    with tempfile.TemporaryDirectory(prefix="meta_proposals_") as tmp:
        proposals_dir = Path(tmp)
        learner = OfflineMetaLearner(proposals_dir=proposals_dir, score_threshold=0.6)

        # ── 1. Score traces ────────────────────────────────────────────────
        traces = await learner.fetch_traces(days=7)
        print(f"Fetched {len(traces)} trace(s) from the (fake) last 7 days:\n")
        scores = await learner.score_traces(traces)
        for score in scores:
            _print_score(score)

        # ── 2. Full review cycle (score → proposals → human-review gate) ──
        print("\nRunning the full meta-learning review cycle...\n")
        summary = await learner.review(days=7)
        print(summary)

        # ── 3. Show the artefacts behind the human-review gate ────────────
        proposal_files = sorted(proposals_dir.glob("proposals_*.md"))
        sentinel = proposals_dir / "human_review_required.txt"
        print(f"\nArtefacts in {proposals_dir}:")
        for path in [*proposal_files, sentinel]:
            print(f"  - {path.name}")
        if proposal_files:
            print("\n--- proposal excerpt ---")
            lines = proposal_files[0].read_text(encoding="utf-8").splitlines()
            print("\n".join(lines[:12]))


if __name__ == "__main__":
    asyncio.run(main())
