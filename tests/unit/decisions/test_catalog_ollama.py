"""Catalog contract shared by the HITL rewrite and the Ollama tier routing (DEC delta)."""

from __future__ import annotations

import pytest

from prismal.decisions.catalog import (
    HITL_QUESTION_IDS,
    INTEGRATION_DECISION_IDS,
    LOW_FREQUENCY_INTEGRATIONS,
    integration_of,
)


def test_hitl_asks_concrete_one_fact_questions_only() -> None:
    assert HITL_QUESTION_IDS == (
        "hitl.destructive",
        "hitl.rule_change",
        "hitl.prior_approval",
        "hitl.health_safety",
        "hitl.money",
    )
    assert INTEGRATION_DECISION_IDS["hitl"] == HITL_QUESTION_IDS
    assert "hitl.band" not in {q for ids in INTEGRATION_DECISION_IDS.values() for q in ids}


def test_low_frequency_integrations_follow_oll_007() -> None:
    assert frozenset({"eval_judge", "hitl", "swarm"}) == LOW_FREQUENCY_INTEGRATIONS


@pytest.mark.parametrize(
    ("qid", "integration"),
    [
        ("route.supervisor", "route"),
        ("hitl.money", "hitl"),
        ("safety.prescreen", "safety"),
        ("swarm.worker_output", "swarm"),
        ("unknown.question", None),
    ],
)
def test_integration_of(qid: str, integration: str | None) -> None:
    assert integration_of(qid) == integration
