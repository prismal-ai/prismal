"""Tests for multimodal RAG loaders (Fase F, SPEC-MM-RAG-002)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from prismal.agents.multimodal.vision_agent import VisionResult
from prismal.providers.stt import STTResult, STTSegment
from prismal.rag.loaders import (
    AudioLoader,
    DocumentProcessorFactory,
    ImageLoader,
    VideoLoader,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_backward_compatible_document_factory_import() -> None:
    # The pre-refactor import path must still resolve.
    assert DocumentProcessorFactory is not None


class TestImageLoader:
    async def test_emits_caption_document(self, tmp_path: Path) -> None:
        img = tmp_path / "pic.png"
        img.write_bytes(PNG)
        vision = AsyncMock()
        vision.analyze = AsyncMock(
            return_value=VisionResult(
                description="a sunset over the sea", objects=[], ocr_text=None, model_used="vlm"
            )
        )
        loader = ImageLoader(vision_agent=vision)
        docs = await loader.load(img)
        assert len(docs) == 1
        assert docs[0].page_content == "a sunset over the sea"
        assert docs[0].metadata["modality"] == "image"
        assert docs[0].metadata["source_uri"] == str(img)


class TestAudioLoader:
    async def test_emits_segment_documents(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFFxxxxWAVE")
        stt = AsyncMock()
        stt.transcribe = AsyncMock(
            return_value=STTResult(
                text="hello world",
                language="en",
                segments=[
                    STTSegment(0.0, 1.0, "hello"),
                    STTSegment(1.0, 2.0, "world"),
                ],
                provider_used="openai",
            )
        )
        loader = AudioLoader(stt_client=stt)
        docs = await loader.load(audio)
        assert len(docs) >= 1
        assert all(d.metadata["modality"] == "audio" for d in docs)
        assert docs[0].metadata["source_uri"] == str(audio)
        assert "start_s" in docs[0].metadata

    async def test_falls_back_to_full_text_without_segments(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFFxxxxWAVE")
        stt = AsyncMock()
        stt.transcribe = AsyncMock(
            return_value=STTResult(
                text="full transcript", language="en", segments=[], provider_used="openai"
            )
        )
        loader = AudioLoader(stt_client=stt)
        docs = await loader.load(audio)
        assert len(docs) == 1
        assert docs[0].page_content == "full transcript"


class TestVideoLoader:
    async def test_combines_audio_and_frames(self, tmp_path: Path) -> None:
        clip = tmp_path / "v.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")

        audio_loader = AsyncMock()
        from langchain_core.documents import Document

        audio_loader.load = AsyncMock(
            return_value=[Document(page_content="said hi", metadata={"modality": "audio"})]
        )
        video_agent = AsyncMock()
        from prismal.agents.multimodal.video_agent import FrameDescription, VideoResult

        video_agent.summarize = AsyncMock(
            return_value=VideoResult(
                transcript="said hi",
                frame_descriptions=[FrameDescription(0, 0.0, "a person waving")],
                summary="greeting clip",
                total_frames_processed=1,
                duration_s=1.0,
            )
        )
        loader = VideoLoader(audio_loader=audio_loader, video_agent=video_agent)
        docs = await loader.load(clip)
        modalities = {d.metadata["modality"] for d in docs}
        assert "video_frame" in modalities
        assert all(d.metadata["source_uri"] == str(clip) for d in docs)


class TestSettingsInjection:
    def test_loaders_construct_without_args(self) -> None:
        # Lazy defaults must not require network at construction.
        assert ImageLoader() is not None
        assert AudioLoader() is not None
        assert VideoLoader() is not None
