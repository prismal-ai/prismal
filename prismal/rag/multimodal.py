"""Multimodal RAG engine (Fase F, SPEC-MM-RAG-001).

Indexes text + image captions + audio/video transcripts into a ChromaDB store
with ``modality`` and ``source_uri`` metadata, and searches with optional
modality filtering. Without a cross-modal embedder it falls back to embedding
the textual captions/transcripts (see DD-MM-003).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from prismal.agents.multimodal.modality_router import Modality
from prismal.core.exceptions import MultimodalRAGError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.media_validator import MediaKind, MediaValidator

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings

    from prismal.core.config import Settings
    from prismal.rag.loaders.audio_loader import AudioLoader
    from prismal.rag.loaders.image_loader import ImageLoader
    from prismal.rag.loaders.video_loader import VideoLoader
    from prismal.rag.vector_store import ChromaVectorStore

logger = get_logger("prismal.rag.multimodal")

# Chunk-metadata modality string → canonical Modality.
_META_TO_MODALITY: dict[str, Modality] = {
    "image": Modality.IMAGE,
    "audio": Modality.AUDIO,
    "video": Modality.VIDEO,
    "video_frame": Modality.VIDEO,
    "text": Modality.TEXT,
}


@dataclass(frozen=True)
class MultimodalRetrievedChunk:
    """A retrieved chunk carrying its modality and original source URI."""

    chunk_id: str
    content: str
    modality: Modality
    source_uri: str
    score: float
    metadata: dict[str, Any]


def _run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* to completion from sync code, even under a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # A loop is already running in this thread — execute in a fresh one.
    result: dict[str, Any] = {}

    def _runner() -> None:
        result["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()
    return result["value"]


class MultimodalRAGEngine:
    """RAG engine with cross-modal indexing and modality-filtered search."""

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        *,
        cross_modal_embedder: Embeddings | None = None,
        image_loader: ImageLoader | None = None,
        audio_loader: AudioLoader | None = None,
        video_loader: VideoLoader | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Store the vector store, optional embedder, and injectable loaders."""
        self._store = vector_store
        self._embedder = cross_modal_embedder
        self._image_loader = image_loader
        self._audio_loader = audio_loader
        self._video_loader = video_loader
        self._settings = settings
        self._validator = MediaValidator(settings=settings)
        if cross_modal_embedder is None:
            logger.warning(
                "multimodal_rag_textual_fallback",
                detail="no cross_modal_embedder; indexing textual captions/transcripts",
            )

    def index(self, path: Path) -> dict[Modality, int]:
        """Index a file or directory; return chunk counts per modality."""
        counts: dict[Modality, int] = {}
        targets = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for target in targets:
            docs = self._load_one(target)
            if not docs:
                continue
            self._store.add_documents(docs)
            for doc in docs:
                modality = self._chunk_modality(doc.metadata)
                counts[modality] = counts.get(modality, 0) + 1
        return counts

    def _load_one(self, target: Path) -> list[Document]:
        """Detect the file kind and load it through the matching loader."""
        otel = OTelManager()
        _, kind = self._validator.sniff(target)
        media_loaders: dict[MediaKind, tuple[Callable[[], Any], str]] = {
            MediaKind.IMAGE: (self._resolve_image_loader, "mm.rag.index_image"),
            MediaKind.AUDIO: (self._resolve_audio_loader, "mm.rag.index_audio"),
            MediaKind.VIDEO: (self._resolve_video_loader, "mm.rag.index_video"),
        }
        entry = media_loaders.get(kind) if kind is not None else None
        try:
            if entry is None:
                return self._load_document(target)
            resolve, span_name = entry
            with otel.start_span(span_name):
                docs = _run_sync(resolve().load(target))
            return cast("list[Document]", docs)
        except Exception as exc:
            raise MultimodalRAGError(f"failed to index {target}: {exc!r}") from exc

    def _load_document(self, target: Path) -> list[Document]:
        """Load a non-media document, tagging it ``modality="text"``."""
        from prismal.rag.loaders.document_loader import (
            DocumentProcessorFactory,
            UnsupportedDocumentTypeError,
        )

        try:
            docs = DocumentProcessorFactory().load(target)
        except UnsupportedDocumentTypeError:
            logger.info("multimodal_rag_skipped_unknown", source=str(target))
            return []
        for doc in docs:
            doc.metadata.setdefault("modality", "text")
            doc.metadata.setdefault("source_uri", str(target))
        return docs

    async def search(
        self,
        query: str,
        *,
        k: int = 5,
        modalities: list[Modality] | None = None,
    ) -> list[MultimodalRetrievedChunk]:
        """Search, optionally filtering by modality, returning top-k chunks."""
        otel = OTelManager()
        # Over-fetch so post-filtering by modality can still return k results.
        fetch_k = k * 4 if modalities else k
        with otel.start_span("mm.rag.search", attributes={"prismal.mm.k": k}):
            try:
                hits = await asyncio.to_thread(self._store.similarity_search, query, fetch_k)
            except Exception as exc:
                raise MultimodalRAGError(f"multimodal search failed: {exc!r}") from exc

        wanted = set(modalities) if modalities else None
        chunks: list[MultimodalRetrievedChunk] = []
        for index, (doc, score) in enumerate(hits):
            modality = self._chunk_modality(doc.metadata)
            if wanted is not None and modality not in wanted:
                continue
            source_uri = str(doc.metadata.get("source_uri", doc.metadata.get("source", "")))
            chunks.append(
                MultimodalRetrievedChunk(
                    chunk_id=f"{source_uri}#{index}",
                    content=doc.page_content,
                    modality=modality,
                    source_uri=source_uri,
                    score=float(score),
                    metadata=dict(doc.metadata),
                )
            )
            if len(chunks) >= k:
                break
        return chunks

    @staticmethod
    def _chunk_modality(metadata: dict[str, Any]) -> Modality:
        """Map a document's metadata modality string to a canonical Modality."""
        return _META_TO_MODALITY.get(str(metadata.get("modality", "text")), Modality.TEXT)

    def _resolve_image_loader(self) -> ImageLoader:
        if self._image_loader is None:
            from prismal.rag.loaders.image_loader import ImageLoader

            self._image_loader = ImageLoader()
        return self._image_loader

    def _resolve_audio_loader(self) -> AudioLoader:
        if self._audio_loader is None:
            from prismal.rag.loaders.audio_loader import AudioLoader

            self._audio_loader = AudioLoader()
        return self._audio_loader

    def _resolve_video_loader(self) -> VideoLoader:
        if self._video_loader is None:
            from prismal.rag.loaders.video_loader import VideoLoader

            self._video_loader = VideoLoader()
        return self._video_loader


__all__ = ["MultimodalRAGEngine", "MultimodalRetrievedChunk"]
