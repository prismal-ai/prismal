"""Tests for the STT provider wrapper (Fase F, SPEC-MM-PROV-001)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prismal.core.exceptions import MissingDependencyError, STTError
from prismal.providers import stt as stt_module
from prismal.providers.stt import (
    STTProvider,
    STTResult,
    STTSegment,
    get_stt,
)


class TestTypes:
    def test_provider_enum_values(self) -> None:
        assert STTProvider.OPENAI == "openai"
        assert STTProvider.LOCAL == "local"

    def test_result_is_frozen(self) -> None:
        result = STTResult(text="hi", language="en", segments=[], provider_used="openai")
        with pytest.raises((AttributeError, TypeError)):
            result.text = "changed"  # type: ignore[misc]


class TestGetStt:
    def test_default_resolves_openai(self) -> None:
        client = get_stt()
        assert client.__class__.__name__ == "_OpenAISTTClient"

    def test_explicit_openai(self) -> None:
        client = get_stt(provider="openai", model="whisper-1")
        assert client.__class__.__name__ == "_OpenAISTTClient"

    def test_local_without_extra_raises_missing_dependency(self) -> None:
        # faster-whisper is not installed in the base test env.
        with pytest.raises(MissingDependencyError) as exc:
            get_stt(provider="local")
        assert exc.value.extra_to_install == "multimodal-local"

    def test_unknown_provider_raises_stt_error(self) -> None:
        with pytest.raises(STTError):
            get_stt(provider="nonsense")


class TestOpenAITranscription:
    @pytest.fixture
    def _mock_litellm(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        resp = SimpleNamespace(
            text="hola mundo",
            language="es",
            segments=[
                {"start": 0.0, "end": 1.2, "text": "hola"},
                {"start": 1.2, "end": 2.0, "text": "mundo"},
            ],
        )
        mock = AsyncMock(return_value=resp)
        monkeypatch.setattr(stt_module.litellm, "atranscription", mock)
        return mock

    async def test_transcribe_maps_response(self, _mock_litellm: AsyncMock) -> None:
        client = get_stt(provider="openai", model="whisper-1")
        result = await client.transcribe(b"\x00\x01fake-audio")
        assert result.text == "hola mundo"
        assert result.language == "es"
        assert result.provider_used == "openai"
        assert result.segments == [
            STTSegment(start_s=0.0, end_s=1.2, text="hola"),
            STTSegment(start_s=1.2, end_s=2.0, text="mundo"),
        ]

    async def test_transcribe_passes_language_and_prompt(self, _mock_litellm: AsyncMock) -> None:
        client = get_stt(provider="openai", model="whisper-1")
        await client.transcribe(b"x", language="es", prompt="contexto")
        kwargs = _mock_litellm.call_args.kwargs
        assert kwargs["language"] == "es"
        assert kwargs["prompt"] == "contexto"
        assert kwargs["model"] == "whisper-1"

    async def test_transcribe_wraps_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            stt_module.litellm,
            "atranscription",
            AsyncMock(side_effect=RuntimeError("api down")),
        )
        client = get_stt(provider="openai")
        with pytest.raises(STTError):
            await client.transcribe(b"x")

    async def test_missing_segments_yields_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = SimpleNamespace(text="just text", language="en")
        monkeypatch.setattr(stt_module.litellm, "atranscription", AsyncMock(return_value=resp))
        client = get_stt(provider="openai")
        result = await client.transcribe(b"x")
        assert result.segments == []
        assert result.text == "just text"


class TestLocalTranscription:
    @pytest.fixture
    def _fake_faster_whisper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = types.ModuleType("faster_whisper")

        class _Seg:
            def __init__(self, start: float, end: float, text: str) -> None:
                self.start = start
                self.end = end
                self.text = text

        class _WhisperModel:
            def __init__(self, name: str) -> None:
                self.name = name

            def transcribe(
                self, _source: object, language: str | None = None, initial_prompt: str | None = None
            ) -> tuple[list[_Seg], SimpleNamespace]:
                return (
                    [_Seg(0.0, 1.0, "hola"), _Seg(1.0, 2.0, " mundo")],
                    SimpleNamespace(language=language or "es"),
                )

        module.WhisperModel = _WhisperModel  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "faster_whisper", module)

    async def test_local_transcribe_maps_segments(self, _fake_faster_whisper: None) -> None:
        client = get_stt(provider="local")
        assert client.__class__.__name__ == "_LocalSTTClient"
        result = await client.transcribe(b"audio-bytes", language="es")
        assert result.text == "hola mundo"
        assert result.language == "es"
        assert result.provider_used == "local"
        assert result.segments[0] == STTSegment(start_s=0.0, end_s=1.0, text="hola")

    async def test_local_transcribe_accepts_path(
        self, _fake_faster_whisper: None, tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        client = get_stt(provider="local")
        result = await client.transcribe(audio)
        assert result.text == "hola mundo"

    async def test_local_transcribe_wraps_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = types.ModuleType("faster_whisper")

        class _WhisperModel:
            def __init__(self, name: str) -> None:
                self.name = name

            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[list[object], object]:
                raise RuntimeError("model exploded")

        module.WhisperModel = _WhisperModel  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "faster_whisper", module)
        client = get_stt(provider="local")
        with pytest.raises(STTError):
            await client.transcribe(b"x")
