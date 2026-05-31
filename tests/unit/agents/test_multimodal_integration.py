"""Tests for multimodal opt-in integration wiring (Fase F, F7)."""

from __future__ import annotations

import pytest

from prismal.agents.intent_router import match_intent
from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP, get_recommended_capabilities


class TestIntentRouter:
    @pytest.mark.parametrize(
        "text",
        [
            "please transcribe this voice note",
            "speech-to-text for this clip",
            "summarize this video for me",
            "describe the image attached",
            "what's in this photo?",
        ],
    )
    def test_multimodal_intents_route_to_pipeline(self, text: str) -> None:
        # All modal intents funnel to the single multimodal_pipeline member.
        assert match_intent(text) == "multimodal_pipeline"

    @pytest.mark.parametrize(
        "text",
        [
            "explain how cron works",
            "what is the capital of France",
            "write a python script to sort a list",
            "tell me a joke",
        ],
    )
    def test_non_multimodal_text_still_falls_through(self, text: str) -> None:
        # Zero regression: ordinary requests must not be captured by multimodal
        # patterns.
        assert match_intent(text) != "multimodal_pipeline"


class TestCapabilityMap:
    def test_multimodal_entries_present(self) -> None:
        assert DEFAULT_CAPABILITY_MAP["vision_agent"] == ["vision", "general"]
        assert DEFAULT_CAPABILITY_MAP["audio_agent"] == ["audio", "general"]
        assert DEFAULT_CAPABILITY_MAP["video_agent"] == [
            "vision",
            "audio",
            "video",
            "general",
        ]
        assert DEFAULT_CAPABILITY_MAP["multimodal_router"] == ["general"]

    def test_get_recommended_capabilities(self) -> None:
        assert get_recommended_capabilities("vision_agent") == ["vision", "general"]
        assert get_recommended_capabilities("nonexistent_agent") is None
