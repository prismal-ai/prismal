"""Tests for multimodal supervisor route gating (Fase F, P3)."""

from __future__ import annotations

import pytest

from prismal.agents.supervisor import (
    MULTIMODAL_MEMBERS,
    build_system_prompt,
    effective_valid_routes,
)


class TestEffectiveValidRoutes:
    def test_multimodal_routes_absent_by_default(self) -> None:
        routes = effective_valid_routes(enable_advanced=False)
        assert "multimodal_pipeline" not in routes

    def test_multimodal_routes_present_when_enabled(self) -> None:
        routes = effective_valid_routes(enable_advanced=False, enable_multimodal=True)
        assert "multimodal_pipeline" in routes
        for member in MULTIMODAL_MEMBERS:
            assert member in routes

    def test_zero_regression_when_multimodal_off(self) -> None:
        # New default param must not change the legacy result.
        assert effective_valid_routes(enable_advanced=False, enable_multimodal=False) == (
            effective_valid_routes(enable_advanced=False)
        )
        assert effective_valid_routes(enable_advanced=True, enable_multimodal=False) == (
            effective_valid_routes(enable_advanced=True)
        )


class TestBuildSystemPrompt:
    def test_multimodal_section_appended_when_enabled(self) -> None:
        prompt = build_system_prompt(enable_advanced=False, enable_multimodal=True)
        assert "multimodal_pipeline" in prompt

    def test_prompt_byte_identical_when_multimodal_off(self) -> None:
        assert build_system_prompt(enable_advanced=False, enable_multimodal=False) == (
            build_system_prompt(enable_advanced=False)
        )


class TestIntentShortCircuitGating:
    @pytest.fixture
    def _trimmed(self) -> list:
        from langchain_core.messages import HumanMessage

        return [HumanMessage(content="please transcribe this voice note")]

    def test_routes_to_pipeline_when_multimodal_enabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import prismal.agents.supervisor as sup

        class _S:
            enable_subgraphs = False
            multimodal_enabled = True
            kokoro_enabled = False

        monkeypatch.setattr(sup, "get_settings", lambda: _S())
        result = sup._intent_short_circuit(_trimmed, "sess")
        assert result is not None
        assert result[0] == "multimodal_pipeline"

    def test_falls_through_when_multimodal_disabled(
        self, _trimmed: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import prismal.agents.supervisor as sup

        class _S:
            enable_subgraphs = False
            multimodal_enabled = False
            kokoro_enabled = False

        monkeypatch.setattr(sup, "get_settings", lambda: _S())
        # Route not in valid set → None → supervisor falls through to the LLM.
        assert sup._intent_short_circuit(_trimmed, "sess") is None
