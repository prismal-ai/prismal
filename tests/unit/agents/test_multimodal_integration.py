"""Tests for multimodal opt-in integration wiring (Fase F, F7)."""

from __future__ import annotations

import pytest

from prismal.agents.intent_router import match_intent
from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP, get_recommended_capabilities


class TestIntentRouter:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("please transcribe this voice note", "audio_agent"),
            ("speech-to-text for this clip", "audio_agent"),
            ("summarize this video for me", "video_agent"),
            ("describe the image attached", "vision_agent"),
            ("what's in this photo?", "vision_agent"),
        ],
    )
    def test_multimodal_intents_match(self, text: str, expected: str) -> None:
        assert match_intent(text) == expected

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
        # patterns (they return None or a non-multimodal route).
        result = match_intent(text)
        assert result not in ("vision_agent", "audio_agent", "video_agent")


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
