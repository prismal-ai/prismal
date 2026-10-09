"""Agentic-memory settings, exceptions and instruments (SPEC-MEM-CFG-001, ERR-001, OTEL-001)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings
from prismal.core.exceptions import (
    AgenticMemoryError,
    MemoryBackendUnavailable,
    MemoryConfigError,
    MemoryForgetDenied,
    MemoryTenancyError,
    PlaybookDeltaError,
    PrismalError,
)
from prismal.monitoring.otel import OTelManager


def test_defaults_match_spec() -> None:
    s = Settings()
    assert s.memory_v2_enabled is False
    assert s.memory_mode == "shadow"
    assert s.memory_v2_backend == "legacy_long_term"
    assert s.memory_write_gate == "decision"
    assert s.memory_write_low_confidence == "skip"
    assert s.memory_relation_k == 5
    assert s.memory_recall_budget_tokens == 1200
    assert s.memory_recall_max_hops == 3
    assert s.memory_sufficiency_tau == 0.8
    assert s.memory_consolidation_cron == "0 3 * * *"
    assert s.memory_consolidation_use_batch is True
    assert s.memory_consolidation_max_items == 5000
    assert s.memory_retention_days_by_type == {"episodic": 90}
    assert s.memory_superseded_retention_days == 30
    assert s.memory_graph_backend == "none"
    assert s.memory_playbooks_enabled is False
    assert s.memory_playbook_budget_tokens == 600
    assert s.memory_playbook_max_deltas == 10
    assert s.memory_playbook_min_stable_runs == 5
    assert s.memory_playbook_keep_versions == 10


def test_legacy_memory_backend_setting_is_untouched() -> None:
    # MEM0-04: the legacy LongTermMemoryStore owns ``memory_backend``; v2 uses its own key.
    assert Settings().memory_backend == "memory"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"memory_recall_budget_tokens": 0},
        {"memory_playbook_budget_tokens": 0},
        {"memory_sufficiency_tau": 0.0},
        {"memory_sufficiency_tau": 1.0},
        {"memory_consolidation_cron": "not a cron"},
        {"memory_retention_days_by_type": {"bogus": 3}},
        {"memory_retention_days_by_type": {"episodic": -1}},
        {"memory_recall_max_hops": 0},
        {"memory_relation_k": 0},
        {"memory_consolidation_max_items": 0},
        {"memory_playbook_max_deltas": 0},
        {"memory_playbook_keep_versions": 0},
    ],
)
def test_invalid_values_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_enforce_decision_gate_requires_decisions() -> None:
    with pytest.raises(ValidationError, match="DECISIONS_ENABLED"):
        Settings(memory_v2_enabled=True, memory_mode="enforce", memory_write_gate="decision")
    ok = Settings(
        memory_v2_enabled=True,
        memory_mode="enforce",
        memory_write_gate="decision",
        decisions_enabled=True,
    )
    assert ok.memory_mode == "enforce"
    assert Settings(memory_v2_enabled=True, memory_mode="shadow").memory_mode == "shadow"


def test_exception_hierarchy() -> None:
    for exc in (
        MemoryConfigError,
        MemoryTenancyError,
        MemoryForgetDenied,
        MemoryBackendUnavailable,
        PlaybookDeltaError,
    ):
        assert issubclass(exc, AgenticMemoryError)
    assert issubclass(AgenticMemoryError, PrismalError)
    assert not issubclass(AgenticMemoryError, MemoryError)


def test_memory_instruments_registered() -> None:
    mgr = OTelManager.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    meter = MagicMock()
    mgr._meter = meter
    mgr._register_mem_metrics()
    assert {
        "memory_writes",
        "memory_recall_stops",
        "memory_consolidations",
        "memory_forget",
        "memory_taint_blocks",
        "memory_tenancy_violations",
    } <= set(mgr._counters)
    assert "memory_recall_tokens" in mgr._histograms
