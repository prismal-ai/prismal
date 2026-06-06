"""Speech-to-Text provider wrapper (Fase F, SPEC-MM-PROV-001).

Exposes a uniform :class:`STTClient` interface over two backends:

- ``openai`` — Whisper API via LiteLLM (``litellm.atranscription``).
- ``local`` — offline ``faster-whisper`` (optional ``[multimodal-local]`` extra).

All provider SDK access is isolated here per the provider-isolation rule.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import litellm

from prismal.core.exceptions import MissingDependencyError, STTError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.providers.stt")


class STTProvider(StrEnum):
    """Speech-to-text backend."""

    OPENAI = "openai"  # Whisper API
    LOCAL = "local"  # faster-whisper


@dataclass(frozen=True)
class STTSegment:
    """A transcribed segment with timestamps."""

    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class STTResult:
    """Result of a transcription.

    Attributes:
        text: Full concatenated transcript.
        language: Detected (or requested) ISO-639-1 language.
        segments: Timestamped segments; empty when the backend omits them.
        provider_used: Identifier of the backend that produced the result.
    """

    text: str
    language: str
    segments: list[STTSegment]
    provider_used: str


@runtime_checkable
class STTClient(Protocol):
    """Uniform Speech-to-Text interface."""

    async def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> STTResult:
        """Transcribe audio to text. Raises ``STTError`` on backend failure."""
        pass


def _coerce_segments(raw: object) -> list[STTSegment]:
    """Map a backend's segment list (dicts or objects) to STTSegment."""
    if not isinstance(raw, list):
        return []
    segments: list[STTSegment] = []
    for item in raw:
        if isinstance(item, dict):
            mapping = cast("dict[str, Any]", item)
            start = mapping.get("start", 0.0)
            end = mapping.get("end", 0.0)
            text = mapping.get("text", "")
        else:
            start = getattr(item, "start", 0.0)
            end = getattr(item, "end", 0.0)
            text = getattr(item, "text", "")
        segments.append(STTSegment(start_s=float(start), end_s=float(end), text=str(text)))
    return segments


class _OpenAISTTClient:
    """Whisper-API backend via LiteLLM."""

    def __init__(self, model: str) -> None:
        """Store the Whisper model id (e.g. ``"whisper-1"``)."""
        self._model = model

    async def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> STTResult:
        """Transcribe via ``litellm.atranscription`` (verbose JSON for segments)."""
        if isinstance(audio, Path):
            file_arg: object = audio.open("rb")
        else:
            buf = BytesIO(audio)
            buf.name = "audio.wav"
            file_arg = buf

        otel = OTelManager()
        with otel.start_span("mm.stt.transcribe", attributes={"prismal.stt.model": self._model}):
            try:
                response = await litellm.atranscription(
                    model=self._model,
                    file=file_arg,
                    language=language,
                    prompt=prompt,
                    response_format="verbose_json",
                )
            except Exception as exc:
                raise STTError(f"Whisper transcription failed: {exc!r}") from exc
            finally:
                if isinstance(file_arg, BytesIO) is False and hasattr(file_arg, "close"):
                    file_arg.close()

        text = str(getattr(response, "text", "") or "")
        detected = getattr(response, "language", None)
        return STTResult(
            text=text,
            language=str(detected or language or ""),
            segments=_coerce_segments(getattr(response, "segments", None)),
            provider_used="openai",
        )


class _LocalSTTClient:
    """Offline ``faster-whisper`` backend (optional extra)."""

    def __init__(self, model: str) -> None:
        """Load a faster-whisper model, or raise if the extra is missing."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise MissingDependencyError(
                "local STT requires faster-whisper",
                extra_to_install="multimodal-local",
            ) from exc
        self._model_name = model
        self._model = WhisperModel(model)

    async def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> STTResult:
        """Transcribe off the main loop via ``asyncio.to_thread``."""
        source: str | BytesIO
        source = str(audio) if isinstance(audio, Path) else BytesIO(audio)

        def _run() -> tuple[str, str, list[STTSegment]]:
            segments_iter, info = self._model.transcribe(
                source, language=language, initial_prompt=prompt
            )
            segs = [
                STTSegment(start_s=float(s.start), end_s=float(s.end), text=str(s.text))
                for s in segments_iter
            ]
            full = "".join(s.text for s in segs).strip()
            return full, str(getattr(info, "language", language or "")), segs

        try:
            text, detected, segs = await asyncio.to_thread(_run)
        except Exception as exc:
            raise STTError(f"local transcription failed: {exc!r}") from exc
        return STTResult(text=text, language=detected, segments=segs, provider_used="local")


def get_stt(
    provider: STTProvider | str | None = None,
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> STTClient:
    """Resolve an :class:`STTClient` from ``settings.stt_provider`` or an override.

    Args:
        provider: Backend override; defaults to ``settings.stt_provider``.
        model: Backend model id (``"whisper-1"`` for openai, ``"base"`` for local).
        settings: Injectable settings.

    Returns:
        A ready-to-use STTClient.

    Raises:
        STTError: If the requested provider is unknown.
        MissingDependencyError: If the local backend's extra is not installed.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    resolved = str(provider or settings.stt_provider).lower()

    if resolved == STTProvider.OPENAI:
        return _OpenAISTTClient(model or "whisper-1")
    if resolved == STTProvider.LOCAL:
        return _LocalSTTClient(model or "base")
    raise STTError(f"unknown STT provider: {resolved!r}")


__all__ = [
    "STTClient",
    "STTProvider",
    "STTResult",
    "STTSegment",
    "get_stt",
]
