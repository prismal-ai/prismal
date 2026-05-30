"""Tests for VideoAgent (Fase F, SPEC-MM-AGT-003)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prismal.agents.multimodal.video_agent import (
    FrameDescription,
    VideoAgent,
    VideoResult,
)
from prismal.agents.multimodal.vision_agent import VisionResult
from prismal.core.exceptions import VideoAgentError
from prismal.security.media_validator import MediaValidator

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
NOT_VIDEO = b"not a video file" + b"\x00" * 16


def _video(tmp_path: Path, blob: bytes = MP4) -> Path:
    p = tmp_path / "clip.mp4"
    p.write_bytes(blob)
    return p


def _vision_agent(desc: str = "a frame") -> AsyncMock:
    agent = AsyncMock()
    agent.analyze = AsyncMock(
        return_value=VisionResult(description=desc, objects=[], ocr_text=None, model_used="vlm")
    )
    return agent


class TestSummarize:
    async def test_full_pipeline(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png", tmp_path / "f1.png"]
        for f in frames:
            f.write_bytes(b"\x89PNG\r\n\x1a\n")
        agent = VideoAgent(
            vision_agent=_vision_agent("frame desc"),
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(return_value="audio transcript"),
            fusion_fn=AsyncMock(return_value="the summary"),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path), fps=1.0)
        assert isinstance(result, VideoResult)
        assert result.transcript == "audio transcript"
        assert result.summary == "the summary"
        assert result.total_frames_processed == 2
        assert result.frame_descriptions == [
            FrameDescription(frame_index=0, timestamp_s=0.0, description="frame desc"),
            FrameDescription(frame_index=1, timestamp_s=1.0, description="frame desc"),
        ]

    async def test_fusion_receives_transcript_and_frames(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png"]
        frames[0].write_bytes(b"\x89PNG\r\n\x1a\n")
        fusion = AsyncMock(return_value="sum")
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(return_value="spoken words"),
            fusion_fn=fusion,
            media_validator=MediaValidator(),
        )
        await agent.summarize(_video(tmp_path))
        assert fusion.call_args.args[0] == "spoken words"
        assert len(fusion.call_args.args[1]) == 1

    async def test_max_frames_forwarded_to_extractor(self, tmp_path: Path) -> None:
        extractor = AsyncMock(return_value=[])
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=extractor,
            transcribe_fn=AsyncMock(return_value=""),
            fusion_fn=AsyncMock(return_value="s"),
            media_validator=MediaValidator(),
        )
        await agent.summarize(_video(tmp_path), fps=2.0, max_frames=10)
        assert extractor.call_args.args[1] == 2.0
        assert extractor.call_args.args[2] == 10


class TestDegradation:
    async def test_invalid_video_degrades(self, tmp_path: Path) -> None:
        extractor = AsyncMock()
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=extractor,
            transcribe_fn=AsyncMock(),
            fusion_fn=AsyncMock(),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path, NOT_VIDEO))
        assert result.summary == ""
        extractor.assert_not_awaited()

    async def test_invalid_video_raises_when_strict(self, tmp_path: Path) -> None:
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(),
            transcribe_fn=AsyncMock(),
            fusion_fn=AsyncMock(),
            media_validator=MediaValidator(),
            degrade_gracefully=False,
        )
        with pytest.raises(VideoAgentError):
            await agent.summarize(_video(tmp_path, NOT_VIDEO))

    async def test_extraction_error_degrades(self, tmp_path: Path) -> None:
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(side_effect=RuntimeError("ffmpeg failed")),
            transcribe_fn=AsyncMock(return_value=""),
            fusion_fn=AsyncMock(return_value="s"),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path))
        assert result.summary == ""
        assert result.total_frames_processed == 0

    async def test_transcribe_error_degrades(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png"]
        frames[0].write_bytes(b"\x89PNG\r\n\x1a\n")
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(side_effect=RuntimeError("no audio")),
            fusion_fn=AsyncMock(return_value="summary"),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path))
        assert result.transcript == ""
        assert result.summary == "summary"

    async def test_transcribe_error_raises_when_strict(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png"]
        frames[0].write_bytes(b"\x89PNG\r\n\x1a\n")
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(side_effect=RuntimeError("no audio")),
            fusion_fn=AsyncMock(return_value="s"),
            media_validator=MediaValidator(),
            degrade_gracefully=False,
        )
        with pytest.raises(VideoAgentError):
            await agent.summarize(_video(tmp_path))

    async def test_fusion_error_degrades(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png"]
        frames[0].write_bytes(b"\x89PNG\r\n\x1a\n")
        agent = VideoAgent(
            vision_agent=_vision_agent(),
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(return_value="words"),
            fusion_fn=AsyncMock(side_effect=RuntimeError("fuse fail")),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path))
        assert result.summary == ""
        assert result.transcript == "words"

    async def test_failing_frame_is_skipped(self, tmp_path: Path) -> None:
        frames = [tmp_path / "f0.png", tmp_path / "f1.png"]
        for f in frames:
            f.write_bytes(b"\x89PNG\r\n\x1a\n")
        vision = _vision_agent()
        vision.analyze = AsyncMock(
            side_effect=[
                VisionResult(description="ok", objects=[], ocr_text=None, model_used="v"),
                RuntimeError("frame decode failed"),
            ]
        )
        agent = VideoAgent(
            vision_agent=vision,
            frame_extractor_fn=AsyncMock(return_value=frames),
            transcribe_fn=AsyncMock(return_value=""),
            fusion_fn=AsyncMock(return_value="s"),
            media_validator=MediaValidator(),
        )
        result = await agent.summarize(_video(tmp_path))
        assert result.total_frames_processed == 1
