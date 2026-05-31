"""Video agent (Fase F, SPEC-MM-AGT-003).

Composite pipeline: validate → extract frames (FFmpeg via SandboxExecutor) +
transcribe audio track → describe frames (VisionAgent) → fuse into a summary.
Frame extraction, transcription and fusion are injected callables so the agent
is testable without FFmpeg or LLMs. Degrades gracefully by default.

Note: when a ``transcribe_fn`` is injected it receives the *video* path and is
responsible for any audio extraction; the built-in default extracts the audio
track via the sandbox before transcribing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from prismal.core.exceptions import VideoAgentError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.media_validator import MediaKind, MediaValidator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.multimodal.audio_agent import AudioAgent
    from prismal.agents.multimodal.vision_agent import VisionAgent
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.multimodal.video_agent")

FrameExtractorFn = Callable[[Path, float, int], Awaitable[list[Path]]]
TranscribeFn = Callable[[Path], Awaitable[str]]
FusionFn = Callable[[str, "list[FrameDescription]"], Awaitable[str]]


@dataclass(frozen=True)
class FrameDescription:
    """Description of a single sampled frame."""

    frame_index: int
    timestamp_s: float
    description: str


@dataclass(frozen=True)
class VideoResult:
    """Result of summarising a video."""

    transcript: str
    frame_descriptions: list[FrameDescription] = field(default_factory=list)
    summary: str = ""
    total_frames_processed: int = 0
    duration_s: float = 0.0


class VideoAgent:
    """Video understanding pipeline (frames + audio transcript + fusion).

    Args:
        vision_agent: Describes each sampled frame; built lazily if None.
        audio_agent: Used by the default transcription path; built lazily.
        frame_extractor_fn: ``async (video, fps, max_frames) -> [frame_path]``.
        transcribe_fn: ``async (video) -> transcript``; defaults to sandbox audio
            extraction + the audio agent.
        fusion_fn: ``async (transcript, frames) -> summary``.
        media_validator: Validator run before extraction.
        degrade_gracefully: When True (default), failures yield a fallback result.
        settings: Injectable settings.
    """

    def __init__(
        self,
        *,
        vision_agent: VisionAgent | None = None,
        audio_agent: AudioAgent | None = None,
        frame_extractor_fn: FrameExtractorFn | None = None,
        transcribe_fn: TranscribeFn | None = None,
        fusion_fn: FusionFn | None = None,
        media_validator: MediaValidator | None = None,
        degrade_gracefully: bool = True,
        settings: Settings | None = None,
    ) -> None:
        """Store collaborators; defaults are resolved lazily on first use."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._vision_agent = vision_agent
        self._audio_agent = audio_agent
        self._frame_extractor_fn = frame_extractor_fn or self._default_frame_extractor
        self._transcribe_fn = transcribe_fn or self._default_transcribe
        self._fusion_fn = fusion_fn or self._make_default_fusion_fn()
        self._validator = media_validator or MediaValidator(settings=settings)
        self._degrade = degrade_gracefully

    async def summarize(
        self,
        video: Path,
        *,
        fps: float | None = None,
        max_frames: int | None = None,
    ) -> VideoResult:
        """Extract frames + transcribe audio + synthesise a summary."""
        otel = OTelManager()
        sample_fps = fps if fps is not None else self._settings.video_sample_fps
        frame_cap = max_frames if max_frames is not None else self._settings.max_frames_per_video

        with otel.start_span("mm.video.validate"):
            validation = self._validator.validate(video, expected_kind=MediaKind.VIDEO)
        if not validation.ok:
            return self._fail(f"invalid video: {validation.reason}")

        try:
            with otel.start_span("mm.video.extract_frames"):
                frame_paths = await self._frame_extractor_fn(video, sample_fps, frame_cap)
        except Exception as exc:
            return self._fail(f"frame extraction failed: {exc!r}")

        descriptions = await self._describe_frames(frame_paths, sample_fps)

        try:
            with otel.start_span("mm.video.transcribe_audio"):
                transcript = await self._transcribe_fn(video)
        except Exception as exc:
            if not self._degrade:
                raise VideoAgentError(f"audio transcription failed: {exc!r}") from exc
            logger.warning("video_transcribe_failed", error=str(exc))
            transcript = ""

        try:
            with otel.start_span("mm.video.fuse"):
                summary = await self._fusion_fn(transcript, descriptions)
        except Exception as exc:
            if not self._degrade:
                raise VideoAgentError(f"fusion failed: {exc!r}") from exc
            logger.warning("video_fusion_failed", error=str(exc))
            summary = ""

        duration_s = descriptions[-1].timestamp_s if descriptions else 0.0
        return VideoResult(
            transcript=transcript,
            frame_descriptions=descriptions,
            summary=str(summary).strip(),
            total_frames_processed=len(descriptions),
            duration_s=duration_s,
        )

    async def _describe_frames(self, frame_paths: list[Path], fps: float) -> list[FrameDescription]:
        """Describe each frame in parallel via the vision agent."""
        if not frame_paths:
            return []
        vision = self._resolve_vision_agent()
        results = await asyncio.gather(
            *(vision.analyze(p) for p in frame_paths), return_exceptions=True
        )
        descriptions: list[FrameDescription] = []
        step = 1.0 / fps if fps > 0 else 1.0
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning("video_frame_failed", index=index, error=str(result))
                continue
            descriptions.append(
                FrameDescription(
                    frame_index=index,
                    timestamp_s=round(index * step, 3),
                    description=result.description,
                )
            )
        return descriptions

    def _fail(self, reason: str) -> VideoResult:
        """Raise or return a graceful fallback VideoResult."""
        logger.warning("video_agent_degraded", reason=reason)
        if not self._degrade:
            raise VideoAgentError(reason)
        return VideoResult(transcript="", frame_descriptions=[], summary="")

    def _resolve_vision_agent(self) -> VisionAgent:
        if self._vision_agent is None:
            from prismal.agents.multimodal.vision_agent import VisionAgent

            self._vision_agent = VisionAgent(settings=self._settings)
        return self._vision_agent

    def _resolve_audio_agent(self) -> AudioAgent:
        if self._audio_agent is None:
            from prismal.agents.multimodal.audio_agent import AudioAgent

            self._audio_agent = AudioAgent(settings=self._settings)
        return self._audio_agent

    async def _default_frame_extractor(  # pragma: no cover - requires FFmpeg + sandbox
        self, video: Path, fps: float, max_frames: int
    ) -> list[Path]:
        """Extract frames with FFmpeg inside the SandboxExecutor."""
        import shlex
        import tempfile

        from prismal.sandbox.executor import SandboxExecutor

        out_dir = Path(tempfile.mkdtemp(prefix="prismal_frames_"))
        pattern = shlex.quote(str(out_dir / "frame_%04d.png"))
        cmd = (
            f"ffmpeg -i {shlex.quote(str(video))} -vf fps={float(fps)} "
            f"-frames:v {int(max_frames)} {pattern}"
        )
        await asyncio.to_thread(SandboxExecutor().run_command, cmd)
        return sorted(out_dir.glob("frame_*.png"))

    async def _default_transcribe(self, video: Path) -> str:  # pragma: no cover - needs FFmpeg
        """Extract the audio track via sandbox FFmpeg, then transcribe it."""
        import shlex
        import tempfile

        from prismal.sandbox.executor import SandboxExecutor

        out_dir = Path(tempfile.mkdtemp(prefix="prismal_audio_"))
        audio_path = out_dir / "audio.wav"
        cmd = (
            f"ffmpeg -i {shlex.quote(str(video))} -vn -ac 1 -ar 16000 "
            f"{shlex.quote(str(audio_path))}"
        )
        await asyncio.to_thread(SandboxExecutor().run_command, cmd)
        result = await self._resolve_audio_agent().process(audio_path)
        return result.transcript

    def _make_default_fusion_fn(self) -> FusionFn:
        """Build a fusion function backed by a multimodal LLM."""

        async def _fuse(  # pragma: no cover - requires a multimodal LLM
            transcript: str, frames: list[FrameDescription]
        ) -> str:
            from langchain_core.messages import HumanMessage, SystemMessage

            from prismal.providers.multimodal import get_multimodal_llm
            from prismal.security.prompt_builder import SecurePromptBuilder

            frame_text = "\n".join(f"[{f.timestamp_s:.1f}s] {f.description}" for f in frames)
            user = f"Audio transcript:\n{transcript}\n\nFrame descriptions:\n{frame_text}"
            llm = get_multimodal_llm(settings=self._settings)
            builder = SecurePromptBuilder()
            safe = builder.build(
                system="Summarise this video from its transcript and frame descriptions.",
                user=user,
            )
            response = await llm.ainvoke(
                [
                    SystemMessage(content=safe[0]["content"]),
                    HumanMessage(content=safe[1]["content"]),
                ]
            )
            return str(response.content)

        return _fuse


__all__ = [
    "FrameDescription",
    "FrameExtractorFn",
    "FusionFn",
    "TranscribeFn",
    "VideoAgent",
    "VideoResult",
]
