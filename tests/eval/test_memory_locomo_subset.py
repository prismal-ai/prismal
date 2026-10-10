"""LoCoMo-style subset: legacy recall vs memory v2 (Phase MEM — MEM6-04, SPEC-MEM-EVL-001).

Synthetic, seeded, MIT-licensed set (LoCoMo itself is CC BY-NC, never committed).
Retrieval QA without an LLM; v2 runs the real ``MemoryService`` with gate ``off``
and no decision backend, so this measures the deterministic layers (dedupe, typed
storage, budgeted multi-hop recall) — decision-gated numbers need a calibrated DEC
backend and are out of scope for the offline suite.
"""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.eval.memory_locomo import (
    UngatedRecallPort,
    compare_reports,
    generate_locomo_style,
    run_memory_eval,
)
from prismal.memory.v2.service import build_memory_service

pytestmark = [pytest.mark.slow]

BUDGET = 1200


async def test_v2_meets_plan_targets_against_legacy_recall() -> None:
    settings = Settings(
        memory_v2_enabled=True,
        memory_v2_backend="memory",
        memory_write_gate="off",
        memory_mode="enforce",
    )
    dataset = generate_locomo_style(seed=7, conversations=5, sessions=200)
    legacy = await run_memory_eval(
        lambda _scope: UngatedRecallPort(), dataset, budget_tokens=BUDGET
    )
    v2 = await run_memory_eval(
        lambda scope: build_memory_service(settings, org_id=scope.org_id),
        dataset,
        budget_tokens=BUDGET,
    )

    verdict = compare_reports(legacy, v2, budget_tokens=BUDGET)

    assert verdict["meets"] == {
        "bounded_context": True,
        "token_savings": True,
        "recall_quality": True,
        "write_efficiency": True,
    }, verdict
    assert v2.recall_p95_ms <= 250.0
