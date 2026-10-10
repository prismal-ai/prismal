"""Tests for the token-efficiency settings (Phase TKE — SPEC-TKE-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


def test_token_efficiency_defaults_match_spec() -> None:
    s = Settings()
    assert s.token_efficiency_enabled is False
    assert s.prompt_cache_enabled is False
    assert s.prompt_cache_ttl == "5m"
    assert s.prompt_cache_scope == "tenant"
    assert s.prompt_cache_policy_path == "config/prompt_cache.yaml"
    assert s.prompt_canary_secret.get_secret_value() == ""
    assert s.prompt_layout == "legacy"
    assert s.tool_gating_mode == "remove"
    assert s.tool_defer_enabled is False
    assert s.tool_defer_threshold == 30
    assert s.tool_defer_core == []
    assert s.context_mask_keep_turns == 10
    assert s.context_mask_relevance_keep == 0.8
    assert s.context_compaction_high_water == 0
    assert s.context_compaction_low_water == 0
    assert s.artifact_store_backend == "memory"
    assert s.artifact_store_path == "data/artifacts.db"
    assert s.artifact_ttl_s == 86400
    assert s.tool_output_budget_enabled is False
    assert s.tool_output_max_tokens_default == 2000
    assert s.tool_output_max_tokens_by_tool == {}
    assert s.tool_output_toon_enabled is False
    assert s.llm_effort_enabled is False
    assert s.llm_effort_profile == "none"
    assert s.llm_effort_by_agent == {}
    assert s.llm_max_tokens_by_agent == {}
    assert s.handoff_envelope_enabled is False
    assert s.handoff_summary_max_tokens == 1500
    assert s.rag_context_budget_enabled is False
    assert s.rag_context_budget_tokens == 3000
    assert s.rag_retrieve_k == 20
    assert s.rag_rerank_enabled is False
    assert s.rag_passage_compression_enabled is False
    assert s.codeact_mcp_code_mode_enabled is False


def test_token_efficiency_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_TOKEN_EFFICIENCY_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_PROMPT_CACHE_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_PROMPT_CACHE_TTL", "1h")
    monkeypatch.setenv("PRISMAL_TOOL_GATING_MODE", "mask")
    monkeypatch.setenv("PRISMAL_TOOL_DEFER_CORE", '["read_file"]')
    monkeypatch.setenv("PRISMAL_TOOL_OUTPUT_MAX_TOKENS_BY_TOOL", '{"web_search": 500}')
    monkeypatch.setenv("PRISMAL_LLM_EFFORT_BY_AGENT", '{"planner": "high"}')
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_HIGH_WATER", "8000")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_LOW_WATER", "4000")
    s = Settings()
    assert s.token_efficiency_enabled is True
    assert s.prompt_cache_enabled is True
    assert s.prompt_cache_ttl == "1h"
    assert s.tool_gating_mode == "mask"
    assert s.tool_defer_core == ["read_file"]
    assert s.tool_output_max_tokens_by_tool == {"web_search": 500}
    assert s.llm_effort_by_agent == {"planner": "high"}
    assert s.context_compaction_high_water == 8000
    assert s.context_compaction_low_water == 4000


def test_mask_observations_strategy_accepted() -> None:
    assert (
        Settings(context_compaction_strategy="mask_observations").context_compaction_strategy
        == "mask_observations"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_cache_ttl", "10m"),
        ("prompt_cache_scope", "planet"),
        ("prompt_layout", "fancy"),
        ("tool_gating_mode", "hide"),
        ("artifact_store_backend", "redis"),
        ("llm_effort_profile", "max"),
    ],
)
def test_unknown_literals_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match=field.upper()):
        Settings(**{field: value})


def test_unknown_effort_level_rejected() -> None:
    with pytest.raises(ValidationError, match="LLM_EFFORT_BY_AGENT"):
        Settings(llm_effort_by_agent={"planner": "extreme"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_defer_threshold", 0),
        ("tool_output_max_tokens_default", -1),
        ("handoff_summary_max_tokens", -5),
        ("rag_context_budget_tokens", -1),
        ("rag_retrieve_k", 0),
        ("artifact_ttl_s", -1),
        ("context_mask_keep_turns", -1),
        ("context_compaction_high_water", -1),
    ],
)
def test_negative_or_zero_thresholds_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_negative_per_tool_budget_rejected() -> None:
    with pytest.raises(ValidationError, match="TOOL_OUTPUT_MAX_TOKENS_BY_TOOL"):
        Settings(tool_output_max_tokens_by_tool={"x": -1})


def test_low_water_must_be_below_high_water() -> None:
    with pytest.raises(ValidationError, match="LOW_WATER"):
        Settings(context_compaction_high_water=1000, context_compaction_low_water=1000)


def test_low_water_ignored_when_high_water_disabled() -> None:
    s = Settings(context_compaction_high_water=0, context_compaction_low_water=0)
    assert s.context_compaction_high_water == 0


def test_canary_secret_is_secret() -> None:
    s = Settings(prompt_canary_secret="s3cr3t")
    assert "s3cr3t" not in repr(s)
    assert s.prompt_canary_secret.get_secret_value() == "s3cr3t"
