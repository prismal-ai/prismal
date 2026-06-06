"""Tests for the TTS provider wrapper (Fase F, SPEC-MM-PROV-002)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import MissingDependencyError, TTSError
from prismal.providers import tts as tts_module
from prismal.providers.tts import TTSProvider, TTSResult, get_tts


class TestTypes:
    def test_provider_enum_values(self) -> None:
        assert TTSProvider.PYTTSX3 == "pyttsx3"
        assert TTSProvider.OPENAI == "openai"
        assert TTSProvider.ELEVENLABS == "elevenlabs"


class TestCascade:
    def test_default_cascades_to_openai_when_pyttsx3_missing(self) -> None:
        # In the base test env pyttsx3 (default) and elevenlabs are not installed,
        # so the cascade lands on the LiteLLM-backed openai client.
        client = get_tts()
        assert client.__class__.__name__ == "_OpenAITTSClient"

    def test_elevenlabs_preference_cascades_to_openai(self) -> None:
        client = get_tts(provider="elevenlabs")
        assert client.__class__.__name__ == "_OpenAITTSClient"

    def test_explicit_openai(self) -> None:
        client = get_tts(provider="openai")
        assert client.__class__.__name__ == "_OpenAITTSClient"


class TestOpenAISynthesis:
    @pytest.fixture
    def _mock_speech(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        resp = SimpleNamespace(content=b"FAKEAUDIO")
        mock = AsyncMock(return_value=resp)
        monkeypatch.setattr(tts_module.litellm, "aspeech", mock)
        return mock

    async def test_synthesize_returns_audio(self, _mock_speech: AsyncMock) -> None:
        client = get_tts(provider="openai")
        result = await client.synthesize("hola")
        assert isinstance(result, TTSResult)
        assert result.audio == b"FAKEAUDIO"
        assert result.provider_used == "openai"
        assert result.mime_type in ("audio/wav", "audio/mpeg")

    async def test_format_mp3_sets_mime(self, _mock_speech: AsyncMock) -> None:
        client = get_tts(provider="openai")
        result = await client.synthesize("hola", format="mp3")
        assert result.mime_type == "audio/mpeg"

    async def test_rejects_text_over_max_chars(self, _mock_speech: AsyncMock) -> None:
        from prismal.core.config import Settings

        client = get_tts(provider="openai", settings=Settings(tts_max_chars=5))
        with pytest.raises(TTSError):
            await client.synthesize("this is way too long")

    async def test_wraps_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            tts_module.litellm, "aspeech", AsyncMock(side_effect=RuntimeError("boom"))
        )
        client = get_tts(provider="openai")
        with pytest.raises(TTSError):
            await client.synthesize("hi")

    async def test_reads_audio_via_read_method(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = SimpleNamespace(content=None, read=lambda: b"VIAREAD")
        monkeypatch.setattr(tts_module.litellm, "aspeech", AsyncMock(return_value=resp))
        client = get_tts(provider="openai")
        result = await client.synthesize("hi")
        assert result.audio == b"VIAREAD"

    async def test_missing_audio_bytes_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = SimpleNamespace(content=None)  # no bytes, no read()
        monkeypatch.setattr(tts_module.litellm, "aspeech", AsyncMock(return_value=resp))
        client = get_tts(provider="openai")
        with pytest.raises(TTSError):
            await client.synthesize("hi")


class TestPyttsx3Backend:
    @pytest.fixture
    def _fake_pyttsx3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = types.ModuleType("pyttsx3")

        class _FakeEngine:
            def setProperty(self, *_args: object) -> None:  # noqa: N802 - mimics pyttsx3 API
                return None

            def save_to_file(self, _text: str, path: str) -> None:
                Path(path).write_bytes(b"WAVDATA")

            def runAndWait(self) -> None:  # noqa: N802 - mimics pyttsx3 API
                return None

        module.init = lambda: _FakeEngine()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyttsx3", module)

    async def test_synthesizes_to_wav(self, _fake_pyttsx3: None) -> None:
        client = get_tts(provider="pyttsx3")
        assert client.__class__.__name__ == "_Pyttsx3TTSClient"
        result = await client.synthesize("hola", voice="es")
        assert result.audio == b"WAVDATA"
        assert result.provider_used == "pyttsx3"
        assert result.mime_type == "audio/wav"


class TestElevenLabsBackend:
    @pytest.fixture
    def _fake_elevenlabs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pkg = types.ModuleType("elevenlabs")
        client_mod = types.ModuleType("elevenlabs.client")

        class _FakeEleven:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key
                self.text_to_speech = SimpleNamespace(convert=lambda **_kwargs: [b"AB", b"CD"])

        client_mod.ElevenLabs = _FakeEleven  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "elevenlabs", pkg)
        monkeypatch.setitem(sys.modules, "elevenlabs.client", client_mod)

    async def test_synthesizes_audio(self, _fake_elevenlabs: None) -> None:
        client = get_tts(provider="elevenlabs", settings=Settings(elevenlabs_api_key="key"))
        assert client.__class__.__name__ == "_ElevenLabsTTSClient"
        result = await client.synthesize("hola", format="mp3")
        assert result.audio == b"ABCD"
        assert result.provider_used == "elevenlabs"

    def test_missing_api_key_raises(self, _fake_elevenlabs: None) -> None:
        # With elevenlabs importable but no key, the backend is unavailable and the
        # cascade falls through to openai.
        client = get_tts(provider="elevenlabs", settings=Settings(elevenlabs_api_key=""))
        assert client.__class__.__name__ == "_OpenAITTSClient"


class TestCascadeExhaustion:
    def test_all_backends_failing_raises_tts_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _always_fail(name: str, _settings: object) -> object:
            raise MissingDependencyError(f"{name} missing", extra_to_install="x")

        monkeypatch.setattr(tts_module, "_build_client", _always_fail)
        with pytest.raises(TTSError, match="no TTS backend"):
            get_tts(provider="openai")
