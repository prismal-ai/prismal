"""Audio loader (Fase F, SPEC-MM-RAG-002).

Transcribes audio and emits one ``Document`` per chunk (grouping STT segments
up to ``segment_chunk_chars``), tagged ``modality="audio"`` with timestamps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.documents import Document

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.providers.stt import STTClient, STTSegment

logger = get_logger("prismal.rag.loaders.audio_loader")


class AudioLoader:
    """Loads audio and emits textual chunks per transcribed segment group."""

    def __init__(
        self,
        *,
        stt_client: STTClient | None = None,
        segment_chunk_chars: int = 1000,
    ) -> None:
        """Store the STT client (built lazily) and the chunk size."""
        self._stt_client = stt_client
        self._segment_chunk_chars = segment_chunk_chars

    async def load(self, path: Path) -> list[Document]:
        """Transcribe and return chunked Documents tagged ``modality="audio"``."""
        client = self._resolve_client()
        result = await client.transcribe(path)
        if not result.segments:
            return [
                Document(
                    page_content=result.text,
                    metadata={
                        "modality": "audio",
                        "source_uri": str(path),
                        "source": str(path),
                        "language": result.language,
                    },
                )
            ]
        docs = self._chunk_segments(result.segments, path, result.language)
        logger.info("audio_loaded", source=str(path), chunks=len(docs))
        return docs

    def _chunk_segments(
        self, segments: list[STTSegment], path: Path, language: str
    ) -> list[Document]:
        """Group consecutive segments into ≤ segment_chunk_chars documents."""
        docs: list[Document] = []
        buffer: list[str] = []
        start_s = segments[0].start_s
        end_s = segments[0].end_s
        for seg in segments:
            candidate = " ".join([*buffer, seg.text]).strip()
            if buffer and len(candidate) > self._segment_chunk_chars:
                docs.append(
                    self._make_doc(" ".join(buffer).strip(), path, language, start_s, end_s)
                )
                buffer = [seg.text]
                start_s = seg.start_s
            else:
                buffer.append(seg.text)
            end_s = seg.end_s
        if buffer:
            docs.append(self._make_doc(" ".join(buffer).strip(), path, language, start_s, end_s))
        return docs

    @staticmethod
    def _make_doc(
        text: str, path: Path, language: str, start_s: float, end_s: float
    ) -> Document:
        return Document(
            page_content=text,
            metadata={
                "modality": "audio",
                "source_uri": str(path),
                "source": str(path),
                "language": language,
                "start_s": start_s,
                "end_s": end_s,
            },
        )

    def _resolve_client(self) -> STTClient:
        if self._stt_client is None:
            from prismal.providers.stt import get_stt

            self._stt_client = get_stt()
        return self._stt_client


__all__ = ["AudioLoader"]
