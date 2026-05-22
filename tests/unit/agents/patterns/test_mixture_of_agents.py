"""Unit tests for MixtureOfAgents (prismal.agents.patterns.mixture_of_agents).

MoA: N proposer models produce independent responses in parallel; an
aggregator LLM synthesizes them. Optionally refines via additional
aggregator passes.

All LLM calls are mocked via ProviderRegistry patching.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.agents.patterns.mixture_of_agents import (
    MixtureOfAgents,
    MoAError,
    MoAResult,
)
from prismal.core.exceptions import PrismalError

PROVIDER_REGISTRY_PATH = "prismal.agents.patterns.mixture_of_agents.ProviderRegistry"


def _response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _scripted_llm(*texts: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[_response(t) for t in texts])
    return llm


def _per_model_registry(
    responses_by_model: dict[str, list[str]],
) -> MagicMock:
    """Build a ProviderRegistry mock whose get_llm(model) returns distinct LLMs."""
    llms = {m: _scripted_llm(*responses_by_model[m]) for m in responses_by_model}
    registry = MagicMock()

    def get_llm(model: str | None = None) -> MagicMock:
        return llms[model] if model else llms[list(llms.keys())[0]]

    registry.get_llm.side_effect = get_llm
    return registry, llms


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_moa_result_is_instantiable() -> None:
    r = MoAResult(
        final_answer="synthesis",
        layer_outputs=[["a", "b", "c"], ["synthesis"]],
        providers_used=["gpt-4o", "claude"],
    )
    assert r.final_answer == "synthesis"
    assert r.providers_used == ["gpt-4o", "claude"]


def test_moa_error_inherits_from_prismal_error() -> None:
    assert issubclass(MoAError, PrismalError)


# ── Constructor ──────────────────────────────────────────────────────────────


def test_init_stores_config() -> None:
    with patch(PROVIDER_REGISTRY_PATH):
        moa = MixtureOfAgents(
            proposer_models=["a", "b"],
            aggregator_model="c",
            n_aggregator_layers=2,
            settings=MagicMock(),
        )
    assert moa._proposer_models == ["a", "b"]  # noqa: SLF001
    assert moa._aggregator_model == "c"  # noqa: SLF001
    assert moa._n_aggregator_layers == 2  # noqa: SLF001


def test_init_rejects_empty_proposer_models() -> None:
    with pytest.raises(ValueError):
        MixtureOfAgents(proposer_models=[], settings=MagicMock())


def test_init_rejects_nonpositive_aggregator_layers() -> None:
    with pytest.raises(ValueError):
        MixtureOfAgents(proposer_models=["a"], n_aggregator_layers=0, settings=MagicMock())


# ── generate() happy path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_runs_proposers_in_parallel_then_aggregates() -> None:
    responses = {
        "gpt-4o": ["proposer 1 output"],
        "claude": ["proposer 2 output"],
        "gemini": ["proposer 3 output"],
        "aggregator-model": ["final synthesis"],
    }
    registry, llms = _per_model_registry(responses)

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["gpt-4o", "claude", "gemini"],
            aggregator_model="aggregator-model",
            settings=MagicMock(),
        )
        result = await moa.generate(query="some question", state={})

    assert isinstance(result, MoAResult)
    assert result.final_answer == "final synthesis"
    # Layer 0: 3 proposer outputs
    assert len(result.layer_outputs) == 2
    assert sorted(result.layer_outputs[0]) == sorted(
        ["proposer 1 output", "proposer 2 output", "proposer 3 output"]
    )
    # Layer 1: 1 aggregator output
    assert result.layer_outputs[1] == ["final synthesis"]
    assert sorted(result.providers_used) == sorted(["gpt-4o", "claude", "gemini"])


@pytest.mark.asyncio
async def test_generate_uses_default_aggregator_when_none() -> None:
    """aggregator_model=None defaults to the first proposer."""
    responses = {"gpt-4o": ["prop1", "aggregated"]}
    registry, llms = _per_model_registry(responses)

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["gpt-4o"],
            aggregator_model=None,
            settings=MagicMock(),
        )
        result = await moa.generate(query="q", state={})

    assert result.final_answer == "aggregated"


# ── Partial failure tolerance ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_continues_when_one_proposer_fails() -> None:
    """If 1 of 3 proposers errors, the other 2 outputs still feed the aggregator."""
    ok_llm_1 = _scripted_llm("output 1")
    failing_llm = MagicMock()
    failing_llm.ainvoke = AsyncMock(side_effect=RuntimeError("proposer 2 crashed"))
    ok_llm_3 = _scripted_llm("output 3")
    aggregator_llm = _scripted_llm("aggregated")

    registry = MagicMock()
    llm_map = {
        "p1": ok_llm_1,
        "p2": failing_llm,
        "p3": ok_llm_3,
        "agg": aggregator_llm,
    }
    registry.get_llm.side_effect = lambda model=None: llm_map[model]

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["p1", "p2", "p3"],
            aggregator_model="agg",
            settings=MagicMock(),
        )
        result = await moa.generate(query="q", state={})

    assert result.final_answer == "aggregated"
    # Only 2 of 3 proposers surface in layer 0
    assert sorted(result.layer_outputs[0]) == sorted(["output 1", "output 3"])
    assert sorted(result.providers_used) == sorted(["p1", "p3"])


@pytest.mark.asyncio
async def test_generate_raises_moa_error_when_all_proposers_fail() -> None:
    failing_llm = MagicMock()
    failing_llm.ainvoke = AsyncMock(side_effect=RuntimeError("crash"))
    registry = MagicMock()
    registry.get_llm.return_value = failing_llm

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["p1", "p2"],
            aggregator_model="agg",
            settings=MagicMock(),
        )
        with pytest.raises(MoAError):
            await moa.generate(query="q", state={})


# ── Multiple aggregator layers ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_with_multiple_aggregator_layers() -> None:
    """n_aggregator_layers=2 produces 2 aggregator passes."""
    responses = {
        "p1": ["prop A"],
        "p2": ["prop B"],
        # Aggregator produces 2 outputs over 2 calls
        "agg": ["pass 1 synth", "pass 2 final"],
    }
    registry, llms = _per_model_registry(responses)

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["p1", "p2"],
            aggregator_model="agg",
            n_aggregator_layers=2,
            settings=MagicMock(),
        )
        result = await moa.generate(query="q", state={})

    assert result.final_answer == "pass 2 final"
    # 3 layers total: proposers + 2 aggregator passes
    assert len(result.layer_outputs) == 3
    assert sorted(result.layer_outputs[0]) == sorted(["prop A", "prop B"])
    assert result.layer_outputs[1] == ["pass 1 synth"]
    assert result.layer_outputs[2] == ["pass 2 final"]


# ── Proposer prompt contents ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregator_prompt_includes_proposer_outputs() -> None:
    """The aggregator must see all proposer outputs in its prompt."""
    responses = {
        "p1": ["alpha response"],
        "p2": ["beta response"],
        "agg": ["final"],
    }
    registry, llms = _per_model_registry(responses)

    with patch(PROVIDER_REGISTRY_PATH) as reg_cls:
        reg_cls.return_value = registry
        moa = MixtureOfAgents(
            proposer_models=["p1", "p2"],
            aggregator_model="agg",
            settings=MagicMock(),
        )
        await moa.generate(query="the query", state={})

    agg_call = llms["agg"].ainvoke.call_args_list[0]
    user_content = str(agg_call.args[0][1].content)
    assert "alpha response" in user_content
    assert "beta response" in user_content
    assert "the query" in user_content
