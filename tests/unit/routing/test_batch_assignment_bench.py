"""BatchAssignmentPolicy latency bound: N=200, K=8, p95 <= 50 ms (SPEC-AMR-BAT-002, SHOULD)."""

from __future__ import annotations

import math
import random
import statistics
import time

import pytest

from prismal.core.config import Settings
from prismal.routing.policies.batch_assignment import BatchAssignmentPolicy, BatchItem
from prismal.routing.types import TIER_ORDER, ModelTier, TaskFeatures

pytestmark = pytest.mark.slow

_K = 8
_N = 200
_REFS = [f"model-{j}" for j in range(_K)]
_TIER = {ref: TIER_ORDER[j * len(TIER_ORDER) // _K] for j, ref in enumerate(_REFS)}


class _Pred:
    version = "bench"

    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self._acc = {
            (intent, ref): min(0.99, 0.3 + 0.08 * j + rng.uniform(-0.05, 0.05))
            for intent in ("a", "b", "c", "d")
            for j, ref in enumerate(_REFS)
        }

    def p_success(self, features: TaskFeatures, model_ref: str) -> float:
        return self._acc[(features.intent or "a", model_ref)]

    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int:
        return 200 + 40 * _REFS.index(model_ref)

    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None:
        return None


def _price(model_ref: str, tokens_in: int, tokens_out: int) -> float:
    j = _REFS.index(model_ref)
    return (0.0002 * 2.2**j) * (tokens_in + 4 * tokens_out) / 1000


def _items(seed: int) -> list[BatchItem]:
    rng = random.Random(seed)
    return [
        BatchItem(
            f"o{i:03d}",
            TaskFeatures(
                agent="worker",
                intent=rng.choice("abcd"),
                est_input_tokens=rng.randint(200, 4000),
                needs_tools=rng.random() < 0.3,
                needs_structured_output=False,
            ),
        )
        for i in range(_N)
    ]


def test_assign_p95_under_50ms() -> None:
    policy = BatchAssignmentPolicy(
        predictor=_Pred(1),  # type: ignore[arg-type]
        candidates=_REFS,
        settings=Settings(routing_model_concurrency=dict.fromkeys(_REFS, 40)),
        price_fn=_price,
        tier_of=_TIER.__getitem__,
    )
    items = _items(2)
    policy.assign(items, alpha=0.7)  # warm-up
    timings: list[float] = []
    results = []
    for _ in range(40):
        start = time.perf_counter()
        results.append(policy.assign(items, alpha=0.7))
        timings.append((time.perf_counter() - start) * 1000)
    p95 = sorted(timings)[math.ceil(0.95 * len(timings)) - 1]  # nearest-rank
    assert all(r == results[0] for r in results)
    assert results[0].feasible
    assert len(results[0].choices) == _N
    assert ModelTier.AUTO not in {c.tier for c in results[0].choices.values()}
    assert p95 <= 50.0, f"p95={p95:.1f} ms (median {statistics.median(timings):.1f} ms)"
