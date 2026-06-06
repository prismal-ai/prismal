"""Text-to-Speech provider wrapper (Fase F, SPEC-MM-PROV-002).

Uniform :class:`TTSClient` over three backends with cascade fallback:

- ``pyttsx3`` — offline, the configured default (optional ``[multimodal]``-adjacent).
- ``openai`` — ``gpt-4o-mini-tts`` and similar via LiteLLM (``litellm.aspeech``).
- ``elevenlabs`` — premium (optional ``[multimodal-premium]`` extra).

``get_tts`` resolves the preferred backend; if it cannot be initialised it
cascades elevenlabs → openai → pyttsx3. All SDK access is isolated here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import litellm

from prismal.core.exceptions import MissingDependencyError, TTSError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.providers.tts")

_MIME_BY_FORMAT: dict[str, str] = {"wav": "audio/wav", "mp3": "audio/mpeg"}


class TTSProvider(StrEnum):
    """Text-to-speech backend."""

    PYTTSX3 = "pyttsx3"
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"


@dataclass(frozen=True)
class TTSResult:
    """Synthesised audio plus metadata."""

    audio: bytes
    mime_type: str
    provider_used: str
    duration_s: float


@runtime_checkable
class TTSClient(Protocol):
    """Uniform Text-to-Speech interface."""

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Synthesise *text* to speech. Raises ``TTSError`` on failure."""
        ...


def _enforce_max_chars(text: str, max_chars: int) -> None:
    """Raise ``TTSError`` if *text* exceeds *max_chars*."""
    if len(text) > max_chars:
        raise TTSError(f"text length {len(text)} exceeds tts_max_chars={max_chars}")


class _OpenAITTSClient:
    """OpenAI TTS backend via ``litellm.aspeech``."""

    def __init__(self, *, model: str, max_chars: int) -> None:
        """Store the TTS model id and the per-call character ceiling."""
        self._model = model
        self._max_chars = max_chars

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Synthesise via ``litellm.aspeech``."""
        _enforce_max_chars(text, self._max_chars)
        otel = OTelManager()
        with otel.start_span("mm.tts.synthesize", attributes={"prismal.tts.model": self._model}):
            try:
                response = await litellm.aspeech(
                    model=self._model,
                    input=text,
                    voice=voice or "alloy",
                    response_format=format,
                )
            except Exception as exc:
                raise TTSError(f"OpenAI TTS failed: {exc!r}") from exc
        return TTSResult(
            audio=_read_binary(response),
            mime_type=_MIME_BY_FORMAT.get(format, "audio/wav"),
            provider_used="openai",
            duration_s=0.0,
        )


class _ElevenLabsTTSClient:
    """ElevenLabs premium backend (optional extra)."""

    def __init__(self, *, api_key: str, max_chars: int) -> None:
        """Initialise the ElevenLabs client, or raise if the extra is missing."""
        try:
            from elevenlabs.client import ElevenLabs
        except ImportError as exc:
            raise MissingDependencyError(
                "ElevenLabs TTS requires the elevenlabs package",
                extra_to_install="multimodal-premium",
            ) from exc
        if not api_key:
            raise TTSError("ElevenLabs TTS requires elevenlabs_api_key")
        self._client = ElevenLabs(api_key=api_key)
        self._max_chars = max_chars

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Synthesise via the ElevenLabs SDK off the event loop."""
        _enforce_max_chars(text, self._max_chars)
        output_format = "mp3_44100_128" if format == "mp3" else "pcm_16000"

        def _run() -> bytes:
            chunks = self._client.text_to_speech.convert(
                text=text, voice_id=voice or "Rachel", output_format=output_format
            )
            return b"".join(chunks)

        try:
            audio = await asyncio.to_thread(_run)
        except Exception as exc:
            raise TTSError(f"ElevenLabs TTS failed: {exc!r}") from exc
        return TTSResult(
            audio=audio,
            mime_type=_MIME_BY_FORMAT.get(format, "audio/mpeg"),
            provider_used="elevenlabs",
            duration_s=0.0,
        )


class _Pyttsx3TTSClient:
    """Offline pyttsx3 backend (always-available default when installed)."""

    def __init__(self, *, max_chars: int) -> None:
        """Verify pyttsx3 is importable, or raise a clear MissingDependencyError."""
        try:
            import pyttsx3
        except ImportError as exc:
            raise MissingDependencyError(
                "offline TTS requires pyttsx3",
                extra_to_install="multimodal",
            ) from exc
        self._pyttsx3 = pyttsx3
        self._max_chars = max_chars

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
    ) -> TTSResult:
        """Synthesise to a temp WAV via pyttsx3 off the event loop."""
        _enforce_max_chars(text, self._max_chars)
        if format != "wav":
            logger.debug("pyttsx3_only_emits_wav", requested=format)
        import tempfile
        from pathlib import Path

        def _run() -> bytes:
            engine = self._pyttsx3.init()
            if voice:
                engine.setProperty("voice", voice)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                out_path = Path(tmp.name)
            try:
                engine.save_to_file(text, str(out_path))
                engine.runAndWait()
                return out_path.read_bytes()
            finally:
                out_path.unlink(missing_ok=True)

        try:
            audio = await asyncio.to_thread(_run)
        except Exception as exc:
            raise TTSError(f"pyttsx3 TTS failed: {exc!r}") from exc
        return TTSResult(
            audio=audio,
            mime_type="audio/wav",
            provider_used="pyttsx3",
            duration_s=0.0,
        )


def _read_binary(response: object) -> bytes:
    """Extract audio bytes from a LiteLLM speech response."""
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    read = getattr(response, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, bytes):
            return data
    raise TTSError("TTS response did not contain audio bytes")


def _build_client(name: str, settings: Settings) -> TTSClient:
    """Construct a single backend client (may raise MissingDependencyError)."""
    if name == TTSProvider.OPENAI:
        return _OpenAITTSClient(model="gpt-4o-mini-tts", max_chars=settings.tts_max_chars)
    if name == TTSProvider.ELEVENLABS:
        return _ElevenLabsTTSClient(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            max_chars=settings.tts_max_chars,
        )
    if name == TTSProvider.PYTTSX3:
        return _Pyttsx3TTSClient(max_chars=settings.tts_max_chars)
    raise TTSError(f"unknown TTS provider: {name!r}")


def get_tts(
    provider: TTSProvider | str | None = None,
    *,
    settings: Settings | None = None,
) -> TTSClient:
    """Resolve a :class:`TTSClient` with cascade fallback.

    The preferred backend (``provider`` or ``settings.tts_provider``) is tried
    first; if it cannot be initialised the resolver cascades through
    elevenlabs → openai → pyttsx3.

    Raises:
        TTSError: If no backend can be initialised.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    preferred = str(provider or settings.tts_provider).lower()
    cascade = [preferred, *[p for p in ("elevenlabs", "openai", "pyttsx3") if p != preferred]]

    errors: list[str] = []
    for name in cascade:
        try:
            client = _build_client(name, settings)
        except (MissingDependencyError, TTSError) as exc:
            errors.append(f"{name}: {exc}")
            logger.debug("tts_backend_unavailable", backend=name, error=str(exc))
            continue
        logger.info("tts_backend_selected", backend=name)
        return client
    raise TTSError(f"no TTS backend available — tried {cascade}: {'; '.join(errors)}")


__all__ = [
    "TTSClient",
    "TTSProvider",
    "TTSResult",
    "get_tts",
]
