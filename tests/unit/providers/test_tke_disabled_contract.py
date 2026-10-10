"""Disabled-path contract (Phase TKE — SPEC-TKE-COMPAT-001, TKE9-02).

With ``token_efficiency_enabled=False`` — even if every sub-flag is set — the
tool list, prompt builder output, ``ChatLiteLLM`` construction and metered
``Usage`` numbers are identical to v3.12.0 behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_litellm import ChatLiteLLM

from prismal.agents.extension.providers import CompositeToolProvider
from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.providers.cost import compute_cost_usd
from prismal.providers.registry import ProviderRegistry
from prismal.security.prompt_builder import SecurePromptBuilder


def _subflags_on() -> Settings:
    """Every TKE sub-flag on, master off (built lazily — .env-independent collection)."""
    return Settings(
        token_efficiency_enabled=False,
        prompt_cache_enabled=True,
        prompt_layout="cache_stable",
        tool_gating_mode="mask",
        tool_defer_enabled=True,
        tool_output_budget_enabled=True,
        tool_output_toon_enabled=True,
        llm_effort_enabled=True,
        llm_effort_profile="recommended",
        handoff_envelope_enabled=True,
        rag_context_budget_enabled=True,
        context_compaction_strategy="mask_observations",
        context_compaction_high_water=1000,
        context_compaction_low_water=500,
    )


def test_chatlitellm_construction_identical() -> None:
    baseline = ProviderRegistry(settings=Settings()).get_llm("claude-sonnet-4-5")
    candidate = ProviderRegistry(settings=_subflags_on()).get_llm("claude-sonnet-4-5", agent="x")
    assert type(baseline) is type(candidate) is ChatLiteLLM
    assert baseline.model_dump(exclude={"client"}) == candidate.model_dump(exclude={"client"})


def test_prompt_builder_legacy_layout() -> None:
    builder = SecurePromptBuilder(settings=_subflags_on())
    messages = builder.build("System.", "hello")
    assert messages[0]["content"] == f"System.\n\n<!-- canary:{builder.canary} -->"
    assert messages[1]["content"] == "<user_input>hello</user_input>"


def test_usage_numbers_identical() -> None:
    msg = SimpleNamespace(
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 100,
            "input_token_details": {"cache_read": 900},
        },
        response_metadata={},
    )
    usage = CostMeter(settings=_subflags_on()).record_response(msg, "claude-sonnet-4-5")
    assert (usage.prompt_tokens, usage.completion_tokens) == (1000, 100)
    assert usage.cost_usd == compute_cost_usd("claude-sonnet-4-5", 1000, 100).cost_usd
    assert usage.cache_read_tokens == 0 and usage.cost_source == ""


def test_default_composite_keeps_remove_semantics() -> None:
    tool = MagicMock()
    tool.name = "t"
    provider = MagicMock()
    provider.get_tools.return_value = [tool]
    composite = CompositeToolProvider([provider])
    assert composite.get_tools(agent_name="coder", phase="planning") == [tool]
    assert (
        composite.allowed_tool_names(agent_name="coder", capabilities=None, phase="planning")
        is None
    )
