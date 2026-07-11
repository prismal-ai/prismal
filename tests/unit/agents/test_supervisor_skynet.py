"""Tests for Skynet supervisor route gating (Fase S, S5 / SPEC-SKY-INT-001)."""

from __future__ import annotations

import pytest

from prismal.agents.intent_router import match_intent
from prismal.agents.supervisor import (
    SKYNET_MEMBERS,
    _intent_short_circuit,
    build_system_prompt,
    effective_valid_routes,
)
from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP


class TestIntentRouter:
    @pytest.mark.parametrize(
        "text",
        [
            "use skynet for this",
            "research these 8 competitors in parallel",
            "fan this out to multiple agents",
            "run a swarm over these tickets",
            "split this across agents",
            "investiga estas empresas en paralelo",
            "lanza un enjambre de agentes",
        ],
    )
    def test_swarm_intents_route_to_skynet(self, text: str) -> None:
        assert match_intent(text) == "skynet"

    def test_debate_still_routes_to_debate_agent(self) -> None:
        # Existing routing unchanged: "debate" keeps its route.
        assert match_intent("start a debate about microservices") == "debate_agent"

    def test_kokoro_still_routes_to_kokoro(self) -> None:
        assert match_intent("please deliberate on this decision") == "kokoro"

    def test_parallel_research_phrase_without_parallel_wording_is_none(self) -> None:
        assert match_intent("research the history of Rome") is None

    def test_unrelated_text_returns_none(self) -> None:
        assert match_intent("write a haiku about the sea") is None


class TestEffectiveValidRoutes:
    def test_skynet_route_absent_by_default(self) -> None:
        routes = effective_valid_routes(enable_advanced=False)
        assert "skynet" not in routes

    def test_skynet_route_present_when_enabled(self) -> None:
        routes = effective_valid_routes(enable_advanced=False, enable_skynet=True)
        for member in SKYNET_MEMBERS:
            assert member in routes

    def test_zero_regression_when_skynet_off(self) -> None:
        # New default param must not change the legacy result.
        for advanced in (False, True):
            for multimodal in (False, True):
                for kokoro in (False, True):
                    assert effective_valid_routes(
                        advanced, multimodal, kokoro, enable_skynet=False
                    ) == effective_valid_routes(advanced, multimodal, kokoro)


class TestBuildSystemPrompt:
    def test_skynet_section_appended_when_enabled(self) -> None:
        prompt = build_system_prompt(enable_advanced=False, enable_skynet=True)
        assert "skynet" in prompt
        assert "parallel" in prompt

    def test_prompt_byte_identical_when_skynet_off(self) -> None:
        for advanced in (False, True):
            for multimodal in (False, True):
                for kokoro in (False, True):
                    assert build_system_prompt(
                        advanced, multimodal, kokoro, enable_skynet=False
                    ) == build_system_prompt(advanced, multimodal, kokoro)


class TestIntentShortCircuitGating:
    @pytest.fixture
    def _trimmed(self) -> list:
        from langchain_core.messages import HumanMessage

        return [HumanMessage(content="fan this out to multiple agents")]

    def test_routes_to_skynet_when_enabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _S:
            enable_subgraphs = False
            multimodal_enabled = False
            kokoro_enabled = False
            skynet_enabled = True
            blind_review_pipeline_enabled = False

        monkeypatch.setattr("prismal.agents.supervisor.get_settings", lambda: _S())
        result = _intent_short_circuit(_trimmed, "sess")
        assert result is not None
        assert result[0] == "skynet"

    def test_falls_through_when_skynet_disabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _S:
            enable_subgraphs = False
            multimodal_enabled = False
            kokoro_enabled = False
            skynet_enabled = False
            blind_review_pipeline_enabled = False

        monkeypatch.setattr("prismal.agents.supervisor.get_settings", lambda: _S())
        # Route not in valid set → None → supervisor falls through to the LLM.
        assert _intent_short_circuit(_trimmed, "sess") is None


class TestCapabilityMap:
    def test_skynet_worker_declared(self) -> None:
        """S5-04: the worker's default capability set is declared (Fase Y)."""
        assert "skynet_worker" in DEFAULT_CAPABILITY_MAP
        assert DEFAULT_CAPABILITY_MAP["skynet_worker"]

    def test_skynet_route_declared(self) -> None:
        assert "skynet" in DEFAULT_CAPABILITY_MAP
