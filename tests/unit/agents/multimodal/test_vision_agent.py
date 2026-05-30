"""Tests for VisionAgent (Fase F, SPEC-MM-AGT-001)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.multimodal.vision_agent import (
    DetectedObject,
    VisionAgent,
    VisionResult,
)
from prismal.core.exceptions import VisionAgentError
from prismal.security.media_validator import MediaValidator

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NOT_IMAGE = b"this is not an image at all" + b"\x00" * 16


class TestTypes:
    def test_result_frozen(self) -> None:
        r = VisionResult(description="d", objects=[], ocr_text=None, model_used="m")
        with pytest.raises((AttributeError, TypeError)):
            r.description = "x"  # type: ignore[misc]

    def test_detected_object_fields(self) -> None:
        obj = DetectedObject(label="dog", confidence=0.9, bbox=None)
        assert obj.label == "dog"
        assert obj.confidence == 0.9


class TestAnalyze:
    async def test_returns_description(self) -> None:
        vision_fn = AsyncMock(return_value="A golden retriever on a beach.")
        agent = VisionAgent(vision_fn=vision_fn, media_validator=MediaValidator())
        result = await agent.analyze(PNG)
        assert result.description == "A golden retriever on a beach."
        assert result.ocr_text is None
        assert result.used_fallback is False
        vision_fn.assert_awaited_once()

    async def test_uses_custom_prompt(self) -> None:
        vision_fn = AsyncMock(return_value="desc")
        agent = VisionAgent(vision_fn=vision_fn, media_validator=MediaValidator())
        await agent.analyze(PNG, prompt="count the dogs")
        assert vision_fn.call_args.args[1] == "count the dogs"

    async def test_ocr_runs_when_requested(self) -> None:
        vision_fn = AsyncMock(return_value="desc")
        ocr_fn = AsyncMock(return_value="OCR: hello")
        agent = VisionAgent(vision_fn=vision_fn, ocr_fn=ocr_fn, media_validator=MediaValidator())
        result = await agent.analyze(PNG, with_ocr=True)
        assert result.ocr_text == "OCR: hello"
        ocr_fn.assert_awaited_once()

    async def test_ocr_skipped_by_default(self) -> None:
        vision_fn = AsyncMock(return_value="desc")
        ocr_fn = AsyncMock(return_value="OCR")
        agent = VisionAgent(vision_fn=vision_fn, ocr_fn=ocr_fn, media_validator=MediaValidator())
        result = await agent.analyze(PNG)
        assert result.ocr_text is None
        ocr_fn.assert_not_awaited()

    async def test_accepts_path(self, tmp_path: Path) -> None:
        p = tmp_path / "x.png"
        p.write_bytes(PNG)
        vision_fn = AsyncMock(return_value="from path")
        agent = VisionAgent(vision_fn=vision_fn, media_validator=MediaValidator())
        result = await agent.analyze(p)
        assert result.description == "from path"


class TestDegradation:
    async def test_invalid_media_degrades_gracefully(self) -> None:
        vision_fn = AsyncMock(return_value="never called")
        agent = VisionAgent(vision_fn=vision_fn, media_validator=MediaValidator())
        result = await agent.analyze(NOT_IMAGE)
        assert result.used_fallback is True
        vision_fn.assert_not_awaited()

    async def test_invalid_media_raises_when_strict(self) -> None:
        agent = VisionAgent(
            vision_fn=AsyncMock(),
            media_validator=MediaValidator(),
            degrade_gracefully=False,
        )
        with pytest.raises(VisionAgentError):
            await agent.analyze(NOT_IMAGE)

    async def test_vlm_error_degrades(self) -> None:
        vision_fn = AsyncMock(side_effect=RuntimeError("vlm down"))
        agent = VisionAgent(vision_fn=vision_fn, media_validator=MediaValidator())
        result = await agent.analyze(PNG)
        assert result.used_fallback is True

    async def test_vlm_error_raises_when_strict(self) -> None:
        vision_fn = AsyncMock(side_effect=RuntimeError("vlm down"))
        agent = VisionAgent(
            vision_fn=vision_fn, media_validator=MediaValidator(), degrade_gracefully=False
        )
        with pytest.raises(VisionAgentError):
            await agent.analyze(PNG)


class TestDefaultBackends:
    @pytest.fixture
    def _mock_vision_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content="a cat on a sofa"))
        monkeypatch.setattr(
            "prismal.providers.vision.get_vision_llm", lambda **_kwargs: llm
        )

    async def test_default_vision_fn_calls_vlm(self, _mock_vision_llm: None) -> None:
        agent = VisionAgent(media_validator=MediaValidator())
        result = await agent.analyze(PNG)
        assert result.description == "a cat on a sofa"

    async def test_default_ocr_fn_runs_second_pass(self, _mock_vision_llm: None) -> None:
        agent = VisionAgent(media_validator=MediaValidator())
        result = await agent.analyze(PNG, with_ocr=True)
        assert result.ocr_text == "a cat on a sofa"

    async def test_ocr_failure_raises_when_strict(self) -> None:
        agent = VisionAgent(
            vision_fn=AsyncMock(return_value="desc"),
            ocr_fn=AsyncMock(side_effect=RuntimeError("ocr down")),
            media_validator=MediaValidator(),
            degrade_gracefully=False,
        )
        with pytest.raises(VisionAgentError):
            await agent.analyze(PNG, with_ocr=True)

    async def test_ocr_failure_degrades(self) -> None:
        agent = VisionAgent(
            vision_fn=AsyncMock(return_value="desc"),
            ocr_fn=AsyncMock(side_effect=RuntimeError("ocr down")),
            media_validator=MediaValidator(),
        )
        result = await agent.analyze(PNG, with_ocr=True)
        assert result.description == "desc"
        assert result.ocr_text is None
