"""Tests for AudioAgent (Fase F, SPEC-MM-AGT-002)."""

from __future__ import annotations

import struct
import wave
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.multimodal.audio_agent import AudioAgent, AudioResult
from prismal.core.exceptions import AudioAgentError
from prismal.providers.stt import STTResult
from prismal.providers.tts import TTSResult
from prismal.security.media_validator import MediaValidator


def _wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


NOT_AUDIO = b"definitely not audio" + b"\x00" * 16


def _stt_client(text: str = "hola, ¿qué tal?") -> AsyncMock:
    client = AsyncMock()
    client.transcribe = AsyncMock(
        return_value=STTResult(text=text, language="es", segments=[], provider_used="openai")
    )
    return client


def _tts_client() -> AsyncMock:
    client = AsyncMock()
    client.synthesize = AsyncMock(
        return_value=TTSResult(
            audio=b"REPLYAUDIO", mime_type="audio/wav", provider_used="pyttsx3", duration_s=0.0
        )
    )
    return client


class TestProcess:
    async def test_transcribe_and_reason_without_tts(self) -> None:
        agent = AudioAgent(
            stt_client=_stt_client(),
            reason_fn=AsyncMock(return_value="Todo bien, ¿en qué te ayudo?"),
            media_validator=MediaValidator(),
        )
        result = await agent.process(_wav())
        assert isinstance(result, AudioResult)
        assert result.transcript == "hola, ¿qué tal?"
        assert result.response_text == "Todo bien, ¿en qué te ayudo?"
        assert result.response_audio is None
        assert result.response_mime is None
        assert result.stt_provider_used == "openai"
        assert result.tts_provider_used is None

    async def test_with_tts_synthesizes_reply(self) -> None:
        agent = AudioAgent(
            stt_client=_stt_client(),
            tts_client=_tts_client(),
            reason_fn=AsyncMock(return_value="respuesta"),
            media_validator=MediaValidator(),
        )
        result = await agent.process(_wav(), with_tts=True)
        assert result.response_audio == b"REPLYAUDIO"
        assert result.response_mime == "audio/wav"
        assert result.tts_provider_used == "pyttsx3"

    async def test_reason_fn_receives_transcript(self) -> None:
        reason = AsyncMock(return_value="ok")
        agent = AudioAgent(
            stt_client=_stt_client("transcribed words"),
            reason_fn=reason,
            media_validator=MediaValidator(),
        )
        await agent.process(_wav())
        assert reason.call_args.args[0] == "transcribed words"

    async def test_language_hint_forwarded_to_stt(self) -> None:
        stt = _stt_client()
        agent = AudioAgent(
            stt_client=stt, reason_fn=AsyncMock(return_value="x"), media_validator=MediaValidator()
        )
        await agent.process(_wav(), language="es")
        assert stt.transcribe.call_args.kwargs["language"] == "es"


class TestDegradation:
    async def test_invalid_audio_degrades(self) -> None:
        stt = _stt_client()
        agent = AudioAgent(
            stt_client=stt, reason_fn=AsyncMock(), media_validator=MediaValidator()
        )
        result = await agent.process(NOT_AUDIO)
        assert result.transcript == ""
        stt.transcribe.assert_not_awaited()

    async def test_invalid_audio_raises_when_strict(self) -> None:
        agent = AudioAgent(
            stt_client=_stt_client(),
            reason_fn=AsyncMock(),
            media_validator=MediaValidator(),
            degrade_gracefully=False,
        )
        with pytest.raises(AudioAgentError):
            await agent.process(NOT_AUDIO)

    async def test_stt_error_degrades(self) -> None:
        stt = AsyncMock()
        stt.transcribe = AsyncMock(side_effect=RuntimeError("stt down"))
        agent = AudioAgent(
            stt_client=stt, reason_fn=AsyncMock(), media_validator=MediaValidator()
        )
        result = await agent.process(_wav())
        assert result.transcript == ""
        assert result.response_text == ""

    async def test_reason_error_keeps_transcript(self) -> None:
        agent = AudioAgent(
            stt_client=_stt_client("hi"),
            reason_fn=AsyncMock(side_effect=RuntimeError("llm down")),
            media_validator=MediaValidator(),
        )
        result = await agent.process(_wav())
        assert result.transcript == "hi"
        assert result.response_text == ""
        assert result.stt_provider_used == "openai"

    async def test_tts_failure_degrades(self) -> None:
        tts = AsyncMock()
        tts.synthesize = AsyncMock(side_effect=RuntimeError("tts down"))
        agent = AudioAgent(
            stt_client=_stt_client(),
            tts_client=tts,
            reason_fn=AsyncMock(return_value="reply"),
            media_validator=MediaValidator(),
        )
        result = await agent.process(_wav(), with_tts=True)
        assert result.response_text == "reply"
        assert result.response_audio is None


class TestDefaultBackends:
    async def test_default_stt_and_reason_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("prismal.providers.stt.get_stt", lambda **_k: _stt_client("hey"))
        registry = MagicMock()
        registry.get_llm.return_value = MagicMock(
            ainvoke=AsyncMock(return_value=type("M", (), {"content": "hello back"})())
        )
        monkeypatch.setattr("prismal.providers.ProviderRegistry", lambda *_a, **_k: registry)
        agent = AudioAgent(media_validator=MediaValidator())
        result = await agent.process(_wav())
        assert result.transcript == "hey"
        assert result.response_text == "hello back"

    async def test_default_tts_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("prismal.providers.tts.get_tts", lambda **_k: _tts_client())
        agent = AudioAgent(
            stt_client=_stt_client(),
            reason_fn=AsyncMock(return_value="reply"),
            media_validator=MediaValidator(),
        )
        result = await agent.process(_wav(), with_tts=True)
        assert result.response_audio == b"REPLYAUDIO"
