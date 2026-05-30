"""Tests for the modality router (Fase F, SPEC-MM-AGT-004)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.multimodal.modality_router import (
    Modality,
    ModalityClassification,
    classify_modality,
    make_modality_router_node,
)


def _msg(content: object, **kwargs: object) -> HumanMessage:
    return HumanMessage(content=content, additional_kwargs=dict(kwargs))


class TestClassifyModality:
    def test_plain_text(self) -> None:
        result = classify_modality(_msg("hello there"))
        assert result.modality is Modality.TEXT

    def test_image_attachment_by_mime(self) -> None:
        result = classify_modality(_msg("look", attachments=[{"mime_type": "image/png"}]))
        assert result.modality is Modality.IMAGE
        assert "image/png" in result.detected_attachments
        assert result.confidence > 0.5

    def test_audio_attachment_by_mime(self) -> None:
        result = classify_modality(_msg("", attachments=[{"mime_type": "audio/wav"}]))
        assert result.modality is Modality.AUDIO

    def test_video_attachment_by_mime(self) -> None:
        result = classify_modality(_msg("", attachments=[{"mime_type": "video/mp4"}]))
        assert result.modality is Modality.VIDEO

    def test_mixed_when_multiple_modalities(self) -> None:
        result = classify_modality(
            _msg("", attachments=[{"mime_type": "image/png"}, {"mime_type": "audio/wav"}])
        )
        assert result.modality is Modality.MIXED
        assert len(result.detected_attachments) == 2

    def test_content_block_image_url(self) -> None:
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xxx"}},
            {"type": "text", "text": "describe"},
        ]
        result = classify_modality(_msg(content))
        assert result.modality is Modality.IMAGE

    def test_regex_transcribe_intent(self) -> None:
        result = classify_modality(_msg("please transcribe this voice note"))
        assert result.modality is Modality.AUDIO

    def test_regex_image_intent_spanish(self) -> None:
        result = classify_modality(_msg("describe la imagen adjunta"))
        assert result.modality is Modality.IMAGE

    def test_regex_video_intent(self) -> None:
        result = classify_modality(_msg("summarize this video for me"))
        assert result.modality is Modality.VIDEO

    def test_result_is_frozen(self) -> None:
        result = ModalityClassification(
            modality=Modality.TEXT, confidence=1.0, detected_attachments=[]
        )
        with pytest.raises((AttributeError, TypeError)):
            result.modality = Modality.IMAGE  # type: ignore[misc]


class TestRouterNode:
    async def test_routes_image_to_vision_agent(self) -> None:
        node = make_modality_router_node()
        state = {
            "messages": [_msg("look", attachments=[{"mime_type": "image/png"}])],
            "metadata": {},
        }
        update = await node(state)
        assert update["next"] == "vision_agent"
        assert update["metadata"]["mm"]["router"]["modality"] == "image"

    async def test_routes_audio_to_audio_agent(self) -> None:
        node = make_modality_router_node()
        state = {"messages": [_msg("", attachments=[{"mime_type": "audio/wav"}])], "metadata": {}}
        update = await node(state)
        assert update["next"] == "audio_agent"

    async def test_text_routes_to_text(self) -> None:
        node = make_modality_router_node()
        state = {"messages": [_msg("just chatting")], "metadata": {}}
        update = await node(state)
        assert update["next"] == "text"

    async def test_empty_messages_routes_to_text(self) -> None:
        node = make_modality_router_node()
        update = await node({"messages": [], "metadata": {}})
        assert update["next"] == "text"

    async def test_force_modality_via_metadata(self) -> None:
        node = make_modality_router_node()
        state = {
            "messages": [_msg("hello")],
            "metadata": {"mm": {"force_modality": "video"}},
        }
        update = await node(state)
        assert update["next"] == "video_agent"

    async def test_llm_fallback_on_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import AsyncMock, MagicMock

        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=type("M", (), {"content": "image"})())
        monkeypatch.setattr("prismal.providers.multimodal.get_multimodal_llm", lambda **_k: llm)
        node = make_modality_router_node(use_llm_fallback=True)
        # An empty-content message with no attachments classifies as UNKNOWN.
        state = {"messages": [_msg("")], "metadata": {}}
        update = await node(state)
        assert update["next"] == "vision_agent"
        assert update["metadata"]["mm"]["router"]["used_fallback_llm"] is True

    async def test_mixed_routes_to_fusion(self) -> None:
        node = make_modality_router_node()
        state = {
            "messages": [
                _msg("", attachments=[{"mime_type": "image/png"}, {"mime_type": "audio/wav"}])
            ],
            "metadata": {},
        }
        update = await node(state)
        assert update["next"] == "fusion"
