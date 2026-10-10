"""Synthetic LoCoMo-style memory eval (SPEC-MEM-EVL-001, MEM6-04).

LoCoMo itself is CC BY-NC 4.0 (MEM0-05), so the repo ships a seeded generator with
the same question categories instead of the dataset.
"""

from __future__ import annotations

import json

from prismal.eval.memory_locomo import (
    CATEGORIES,
    MemoryEvalReport,
    UngatedRecallPort,
    compare_reports,
    generate_locomo_style,
    run_memory_eval,
)
from prismal.memory.v2.types import MemoryScope


def test_generator_is_deterministic_and_covers_categories() -> None:
    a = generate_locomo_style(seed=7, conversations=3)
    b = generate_locomo_style(seed=7, conversations=3)
    assert a == b
    assert generate_locomo_style(seed=8, conversations=3) != a
    cats = {q.category for c in a.conversations for q in c.questions}
    assert cats == set(CATEGORIES)
    for conv in a.conversations:
        ids = {t.id for t in conv.turns}
        assert all(set(q.evidence) <= ids for q in conv.questions)
        stamps = [t.ts for t in conv.turns]
        assert stamps == sorted(stamps)


def test_temporal_questions_expect_the_latest_value() -> None:
    ds = generate_locomo_style(seed=1, conversations=2)
    for conv in ds.conversations:
        for q in conv.questions:
            if q.category == "temporal":
                last = max((t for t in conv.turns if t.id in q.evidence), key=lambda t: t.ts)
                assert q.answer.lower() in last.text.lower()


async def test_ungated_baseline_report_shape() -> None:
    ds = generate_locomo_style(seed=3, conversations=2)
    report = await run_memory_eval(lambda scope: UngatedRecallPort(), ds, budget_tokens=1200)
    assert isinstance(report, MemoryEvalReport)
    assert 0.0 <= report.accuracy <= 1.0
    assert set(report.accuracy_by_category) == set(CATEGORIES)
    assert report.stored_records == sum(
        1 for c in ds.conversations for t in c.turns if t.speaker == "user"
    )
    assert report.full_history_tokens_per_turn > 0
    assert report.recall_p95_ms >= 0.0
    json.dumps(report.to_dict())  # serializable for the CLI


async def test_each_conversation_gets_its_own_tenant() -> None:
    seen: list[MemoryScope] = []

    def factory(scope: MemoryScope) -> UngatedRecallPort:
        seen.append(scope)
        return UngatedRecallPort()

    ds = generate_locomo_style(seed=2, conversations=3)
    await run_memory_eval(factory, ds, budget_tokens=500)
    assert len({s.org_id for s in seen}) == 3


async def test_compare_reports_flags_targets() -> None:
    ds = generate_locomo_style(seed=4, conversations=2)
    legacy = await run_memory_eval(lambda s: UngatedRecallPort(k=5), ds, budget_tokens=10_000)
    same = compare_reports(legacy, legacy)
    assert same["accuracy_delta_pts"] == 0.0
    assert same["meets"]["recall_quality"] is False  # needs >= +5 pts
    assert same["meets"]["bounded_context"] is True
