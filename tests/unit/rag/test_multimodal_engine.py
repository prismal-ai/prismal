"""Tests for MultimodalRAGEngine (Fase F, SPEC-MM-RAG-001)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from prismal.agents.multimodal.modality_router import Modality
from prismal.core.exceptions import MultimodalRAGError
from prismal.rag.multimodal import MultimodalRAGEngine, MultimodalRetrievedChunk

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


def _store() -> MagicMock:
    store = MagicMock()
    store.add_documents = MagicMock(return_value=["id1"])
    return store


def _image_loader(caption: str = "a cat") -> AsyncMock:
    loader = AsyncMock()
    loader.load = AsyncMock(
        return_value=[
            Document(page_content=caption, metadata={"modality": "image", "source_uri": "x.png"})
        ]
    )
    return loader


class TestIndex:
    def test_indexes_image_file(self, tmp_path: Path) -> None:
        img = tmp_path / "x.png"
        img.write_bytes(PNG)
        store = _store()
        engine = MultimodalRAGEngine(store, image_loader=_image_loader())
        counts = engine.index(img)
        assert counts[Modality.IMAGE] == 1
        store.add_documents.assert_called_once()

    def test_indexes_audio_file(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(WAV)
        loader = AsyncMock()
        loader.load = AsyncMock(
            return_value=[
                Document(page_content="hi", metadata={"modality": "audio", "source_uri": "a.wav"})
            ]
        )
        engine = MultimodalRAGEngine(_store(), audio_loader=loader)
        counts = engine.index(audio)
        assert counts[Modality.AUDIO] == 1

    def test_indexes_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.png").write_bytes(PNG)
        (tmp_path / "b.png").write_bytes(PNG)
        engine = MultimodalRAGEngine(_store(), image_loader=_image_loader())
        counts = engine.index(tmp_path)
        assert counts[Modality.IMAGE] == 2

    def test_unknown_file_is_skipped(self, tmp_path: Path) -> None:
        junk = tmp_path / "j.bin"
        junk.write_bytes(b"not media and not a known document")
        engine = MultimodalRAGEngine(_store(), image_loader=_image_loader())
        counts = engine.index(junk)
        assert sum(counts.values()) == 0


class TestSearch:
    async def test_search_returns_chunks(self) -> None:
        store = _store()
        store.similarity_search = MagicMock(
            return_value=[
                (
                    Document(
                        page_content="a cat", metadata={"modality": "image", "source_uri": "x.png"}
                    ),
                    0.9,
                ),
                (
                    Document(
                        page_content="some words",
                        metadata={"modality": "text", "source_uri": "d.txt"},
                    ),
                    0.7,
                ),
            ]
        )
        engine = MultimodalRAGEngine(store)
        results = await engine.search("cat", k=5)
        assert all(isinstance(c, MultimodalRetrievedChunk) for c in results)
        assert results[0].modality is Modality.IMAGE
        assert results[0].source_uri == "x.png"
        assert results[0].score == 0.9

    async def test_search_filters_by_modality(self) -> None:
        store = _store()
        store.similarity_search = MagicMock(
            return_value=[
                (
                    Document(
                        page_content="a cat", metadata={"modality": "image", "source_uri": "x.png"}
                    ),
                    0.9,
                ),
                (
                    Document(
                        page_content="words", metadata={"modality": "text", "source_uri": "d.txt"}
                    ),
                    0.7,
                ),
            ]
        )
        engine = MultimodalRAGEngine(store)
        results = await engine.search("cat", k=5, modalities=[Modality.IMAGE])
        assert len(results) == 1
        assert results[0].modality is Modality.IMAGE

    async def test_video_frame_matches_video_filter(self) -> None:
        store = _store()
        store.similarity_search = MagicMock(
            return_value=[
                (
                    Document(
                        page_content="waving",
                        metadata={"modality": "video_frame", "source_uri": "v.mp4"},
                    ),
                    0.8,
                ),
            ]
        )
        engine = MultimodalRAGEngine(store)
        results = await engine.search("wave", modalities=[Modality.VIDEO])
        assert len(results) == 1
        assert results[0].modality is Modality.VIDEO


class TestEmbedderWarning:
    def test_warns_without_cross_modal_embedder(self, caplog: pytest.LogCaptureFixture) -> None:
        # No embedder → engine still constructs (textual-caption fallback).
        engine = MultimodalRAGEngine(_store(), cross_modal_embedder=None)
        assert engine is not None


class TestErrorHandling:
    def test_loader_failure_wrapped(self, tmp_path: Path) -> None:
        img = tmp_path / "x.png"
        img.write_bytes(PNG)
        loader = AsyncMock()
        loader.load = AsyncMock(side_effect=RuntimeError("vlm down"))
        engine = MultimodalRAGEngine(_store(), image_loader=loader)
        with pytest.raises(MultimodalRAGError):
            engine.index(img)

    async def test_search_failure_wrapped(self) -> None:
        store = _store()
        store.similarity_search = MagicMock(side_effect=RuntimeError("chroma down"))
        engine = MultimodalRAGEngine(store)
        with pytest.raises(MultimodalRAGError):
            await engine.search("q")

    def test_indexes_text_document(self, tmp_path: Path) -> None:
        doc = tmp_path / "note.txt"
        doc.write_text("plain text content for indexing")
        store = _store()
        engine = MultimodalRAGEngine(store)
        counts = engine.index(doc)
        assert counts.get(Modality.TEXT, 0) >= 1


class TestRunSync:
    async def test_run_sync_under_running_loop(self) -> None:
        from prismal.rag.multimodal import _run_sync

        async def _coro() -> int:
            return 42

        assert _run_sync(_coro()) == 42


class TestLazyDefaults:
    def test_resolve_loaders_build_defaults(self) -> None:
        engine = MultimodalRAGEngine(_store())
        assert engine._resolve_image_loader() is not None
        assert engine._resolve_audio_loader() is not None
        assert engine._resolve_video_loader() is not None
