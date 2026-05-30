"""Video loader (Fase F, SPEC-MM-RAG-002).

Composes the audio track (via :class:`AudioLoader`) with sampled-frame
descriptions (via :class:`VideoAgent`), emitting ``modality="video_frame"`` and
``modality="audio"`` Documents that all point at the original video URI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.documents import Document

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.agents.multimodal.video_agent import VideoAgent
    from prismal.rag.loaders.audio_loader import AudioLoader
    from prismal.rag.loaders.image_loader import ImageLoader

logger = get_logger("prismal.rag.loaders.video_loader")


class VideoLoader:
    """Loads a video into frame-description and audio-transcript Documents."""

    def __init__(
        self,
        *,
        audio_loader: AudioLoader | None = None,
        image_loader: ImageLoader | None = None,
        video_agent: VideoAgent | None = None,
        fps: float = 1.0,
        max_frames: int = 60,
    ) -> None:
        """Store collaborators (built lazily) and sampling parameters."""
        self._audio_loader = audio_loader
        self._image_loader = image_loader
        self._video_agent = video_agent
        self._fps = fps
        self._max_frames = max_frames

    async def load(self, path: Path) -> list[Document]:
        """Return frame-description + audio Documents for the video at *path*."""
        video_agent = self._resolve_video_agent()
        result = await video_agent.summarize(path, fps=self._fps, max_frames=self._max_frames)

        docs: list[Document] = [
            Document(
                page_content=frame.description,
                metadata={
                    "modality": "video_frame",
                    "source_uri": str(path),
                    "source": str(path),
                    "timestamp_s": frame.timestamp_s,
                    "frame_index": frame.frame_index,
                },
            )
            for frame in result.frame_descriptions
            if frame.description
        ]

        for audio_doc in await self._load_audio(path):
            audio_doc.metadata["source_uri"] = str(path)
            audio_doc.metadata.setdefault("modality", "audio")
            docs.append(audio_doc)

        logger.info("video_loaded", source=str(path), docs=len(docs))
        return docs

    async def _load_audio(self, path: Path) -> list[Document]:
        """Transcribe the video's audio track via the audio loader, if possible."""
        try:
            return await self._resolve_audio_loader().load(path)
        except Exception as exc:
            logger.warning("video_audio_load_failed", error=str(exc))
            return []

    def _resolve_video_agent(self) -> VideoAgent:
        if self._video_agent is None:
            from prismal.agents.multimodal.video_agent import VideoAgent

            self._video_agent = VideoAgent()
        return self._video_agent

    def _resolve_audio_loader(self) -> AudioLoader:
        if self._audio_loader is None:
            from prismal.rag.loaders.audio_loader import AudioLoader

            self._audio_loader = AudioLoader()
        return self._audio_loader


__all__ = ["VideoLoader"]
