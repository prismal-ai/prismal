"""Tests for Kokoro supervisor route gating (Fase K, K7 / SPEC-KOK-INT-001)."""

from __future__ import annotations

import pytest

import prismal.agents.supervisor as sup
from prismal.agents.intent_router import match_intent


class TestIntentRouter:
    @pytest.mark.parametrize(
        "text",
        [
            "please deliberate on whether we should migrate",
            "use kokoro to decide this",
            "weigh the perspectives before deciding",
            "que el panel decida la estrategia",
            "deliberar sobre la propuesta",
            "let the three voices decide",
        ],
    )
    def test_deliberation_intents_route_to_kokoro(self, text: str) -> None:
        assert match_intent(text) == "kokoro"

    def test_debate_still_routes_to_debate_agent(self) -> None:
        # Existing routing unchanged: "debate" keeps its route.
        assert match_intent("start a debate about microservices") == "debate_agent"

    def test_consensus_still_routes_to_debate_consensus(self) -> None:
        assert match_intent("reach a consensus on the design") == "debate_consensus"

    def test_unrelated_text_returns_none(self) -> None:
        assert match_intent("write a haiku about the sea") is None


class TestEffectiveValidRoutes:
    def test_kokoro_route_absent_by_default(self) -> None:
        routes = sup.effective_valid_routes(enable_advanced=False)
        assert "kokoro" not in routes

    def test_kokoro_route_present_when_enabled(self) -> None:
        routes = sup.effective_valid_routes(enable_advanced=False, enable_kokoro=True)
        for member in sup.KOKORO_MEMBERS:
            assert member in routes

    def test_zero_regression_when_kokoro_off(self) -> None:
        # New default param must not change the legacy result.
        for advanced in (False, True):
            for multimodal in (False, True):
                assert sup.effective_valid_routes(
                    advanced, multimodal, enable_kokoro=False
                ) == sup.effective_valid_routes(advanced, multimodal)


class TestBuildSystemPrompt:
    def test_kokoro_section_appended_when_enabled(self) -> None:
        prompt = sup.build_system_prompt(enable_advanced=False, enable_kokoro=True)
        assert "kokoro" in prompt
        assert "spirit/values" in prompt

    def test_prompt_byte_identical_when_kokoro_off(self) -> None:
        for advanced in (False, True):
            for multimodal in (False, True):
                assert sup.build_system_prompt(
                    advanced, multimodal, enable_kokoro=False
                ) == sup.build_system_prompt(advanced, multimodal)


class TestIntentShortCircuitGating:
    @pytest.fixture
    def _trimmed(self) -> list:
        from langchain_core.messages import HumanMessage

        return [HumanMessage(content="please deliberate on this decision")]

    def test_routes_to_kokoro_when_enabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _S:
            enable_subgraphs = False
            multimodal_enabled = False
            kokoro_enabled = True
            skynet_enabled = False

        monkeypatch.setattr(sup, "get_settings", lambda: _S())
        result = sup._intent_short_circuit(_trimmed, "sess")
        assert result is not None
        assert result[0] == "kokoro"

    def test_falls_through_when_kokoro_disabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _S:
            enable_subgraphs = False
            multimodal_enabled = False
            kokoro_enabled = False
            skynet_enabled = False

        monkeypatch.setattr(sup, "get_settings", lambda: _S())
        # Route not in valid set → None → supervisor falls through to the LLM.
        assert sup._intent_short_circuit(_trimmed, "sess") is None
