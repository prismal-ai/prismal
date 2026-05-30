"""Audio agent (Fase F, SPEC-MM-AGT-002).

Voice-to-voice cascade: validate → STT → reason (LLM) → optional TTS. The STT
and TTS clients and the reasoning function are injected so the agent is testable
without real speech backends. Degrades gracefully by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import AudioAgentError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.media_validator import MediaKind, MediaValidator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.core.config import Settings
    from prismal.providers.stt import STTClient
    from prismal.providers.tts import TTSClient

logger = get_logger("prismal.agents.multimodal.audio_agent")

_DEFAULT_SYSTEM = "You are a helpful voice assistant. Reply concisely to the user's message."


@dataclass(frozen=True)
class AudioResult:
    """Result of an audio (voice-to-voice) turn."""

    transcript: str
    response_text: str
    response_audio: bytes | None
    response_mime: str | None
    stt_provider_used: str
    tts_provider_used: str | None
    duration_s: float


class AudioAgent:
    """Voice-to-voice pipeline: STT → reasoning → optional TTS.

    Args:
        stt_client: Injected STT client; defaults to ``get_stt()``.
        tts_client: Injected TTS client; defaults to ``get_tts()``.
        reason_fn: ``async (transcript, state) -> response_text``; defaults to a
            provider LLM call through SecurePromptBuilder.
        media_validator: Validator run before transcription.
        degrade_gracefully: When True (default), failures yield a fallback result.
        settings: Injectable settings.
    """

    def __init__(
        self,
        *,
        stt_client: STTClient | None = None,
        tts_client: TTSClient | None = None,
        reason_fn: Callable[[str, Any], Awaitable[str]] | None = None,
        media_validator: MediaValidator | None = None,
        degrade_gracefully: bool = True,
        settings: Settings | None = None,
    ) -> None:
        """Store collaborators; STT/TTS clients are resolved lazily when needed."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._stt_client = stt_client
        self._tts_client = tts_client
        self._reason_fn = reason_fn or self._make_default_reason_fn()
        self._validator = media_validator or MediaValidator(settings=settings)
        self._degrade = degrade_gracefully

    async def process(
        self,
        audio: bytes | Path,
        *,
        state: Any | None = None,
        language: str | None = None,
        with_tts: bool = False,
    ) -> AudioResult:
        """Run the full voice-to-voice pipeline."""
        otel = OTelManager()
        blob = audio.read_bytes() if isinstance(audio, Path) else audio

        with otel.start_span("mm.audio.validate"):
            validation = self._validator.validate(blob, expected_kind=MediaKind.AUDIO)
        if not validation.ok:
            return self._fail(f"invalid audio: {validation.reason}", duration_s=0.0)

        duration_s = validation.duration_s or 0.0
        try:
            stt = self._resolve_stt()
            with otel.start_span("mm.audio.stt"):
                stt_result = await stt.transcribe(audio, language=language)
        except Exception as exc:
            return self._fail(f"transcription failed: {exc!r}", duration_s=duration_s)

        try:
            with otel.start_span("mm.audio.reason"):
                response_text = await self._reason_fn(stt_result.text, state)
        except Exception as exc:
            return self._fail(
                f"reasoning failed: {exc!r}",
                duration_s=duration_s,
                transcript=stt_result.text,
                stt_provider=stt_result.provider_used,
            )

        response_audio: bytes | None = None
        response_mime: str | None = None
        tts_provider: str | None = None
        if with_tts:
            try:
                tts = self._resolve_tts()
                with otel.start_span("mm.audio.tts"):
                    tts_result = await tts.synthesize(response_text)
                response_audio = tts_result.audio
                response_mime = tts_result.mime_type
                tts_provider = tts_result.provider_used
            except Exception as exc:
                if not self._degrade:
                    raise AudioAgentError(f"TTS failed: {exc!r}") from exc
                logger.warning("audio_tts_failed", error=str(exc))

        return AudioResult(
            transcript=stt_result.text,
            response_text=str(response_text).strip(),
            response_audio=response_audio,
            response_mime=response_mime,
            stt_provider_used=stt_result.provider_used,
            tts_provider_used=tts_provider,
            duration_s=duration_s,
        )

    def _resolve_stt(self) -> STTClient:
        if self._stt_client is None:
            from prismal.providers.stt import get_stt

            self._stt_client = get_stt(settings=self._settings)
        return self._stt_client

    def _resolve_tts(self) -> TTSClient:
        if self._tts_client is None:
            from prismal.providers.tts import get_tts

            self._tts_client = get_tts(settings=self._settings)
        return self._tts_client

    def _fail(
        self,
        reason: str,
        *,
        duration_s: float,
        transcript: str = "",
        stt_provider: str = "",
    ) -> AudioResult:
        """Raise or return a graceful fallback AudioResult."""
        logger.warning("audio_agent_degraded", reason=reason)
        if not self._degrade:
            raise AudioAgentError(reason)
        return AudioResult(
            transcript=transcript,
            response_text="",
            response_audio=None,
            response_mime=None,
            stt_provider_used=stt_provider,
            tts_provider_used=None,
            duration_s=duration_s,
        )

    def _make_default_reason_fn(self) -> Callable[[str, Any], Awaitable[str]]:
        """Build an LLM-backed reasoning function via the provider registry."""

        async def _reason(transcript: str, _state: Any) -> str:
            from langchain_core.messages import HumanMessage, SystemMessage

            from prismal.providers import ProviderRegistry
            from prismal.security.prompt_builder import SecurePromptBuilder

            llm = ProviderRegistry(self._settings).get_llm()
            builder = SecurePromptBuilder()
            safe = builder.build(system=_DEFAULT_SYSTEM, user=transcript)
            response = await llm.ainvoke(
                [
                    SystemMessage(content=safe[0]["content"]),
                    HumanMessage(content=safe[1]["content"]),
                ]
            )
            return str(response.content)

        return _reason


__all__ = ["AudioAgent", "AudioResult"]
