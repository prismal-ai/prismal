"""Media validation gate (Fase F, SPEC-MM-SEC-001).

``MediaValidator`` runs *before* any multimodal agent touches incoming media.
It verifies the declared format against magic bytes, enforces per-kind size
limits, and — when the duration can be determined cheaply (WAV via the stdlib
``wave`` module) — enforces a duration ceiling.

Design notes:
    - No optional dependency (``libmagic``/``python-magic``) is required: a
      small hardcoded magic-byte table covers the supported formats.
    - Video/MP3 duration cannot be computed without parsing the container, so
      ``duration_s`` is ``None`` for those and the duration check is skipped
      here; the ``VideoAgent`` enforces video duration via ``ffprobe`` inside
      the sandbox.
"""

from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismal.core.config import Settings


class MediaKind(StrEnum):
    """Coarse media category used for per-kind limits."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass(frozen=True)
class MediaValidationResult:
    """Outcome of validating a media blob.

    Attributes:
        ok: Whether the media passed every check.
        reason: Human-readable rejection reason, or ``None`` when ``ok``.
        detected_mime: Sniffed MIME type, or ``None`` if unrecognised.
        detected_kind: Sniffed :class:`MediaKind`, or ``None`` if unrecognised.
        size_bytes: Size of the media in bytes.
        duration_s: Duration in seconds when determinable, else ``None``.
    """

    ok: bool
    reason: str | None
    detected_mime: str | None
    detected_kind: MediaKind | None
    size_bytes: int
    duration_s: float | None


# Magic bytes → (mime, kind). RIFF needs a follow-up WAVE check; MP4 is matched
# by the ``ftyp`` box at offset 4 rather than a fixed box size.
_MAGIC_BYTES: dict[bytes, tuple[str, MediaKind]] = {
    b"\x89PNG\r\n\x1a\n": ("image/png", MediaKind.IMAGE),
    b"\xff\xd8\xff": ("image/jpeg", MediaKind.IMAGE),
    b"GIF87a": ("image/gif", MediaKind.IMAGE),
    b"GIF89a": ("image/gif", MediaKind.IMAGE),
    b"ID3": ("audio/mpeg", MediaKind.AUDIO),
    b"\x1aE\xdf\xa3": ("video/webm", MediaKind.VIDEO),
}

# Bytes read off the front of a blob for sniffing.
_SNIFF_BYTES = 32


class MediaValidator:
    """Validates media before it reaches a multimodal agent.

    Args:
        max_image_bytes: Per-image size ceiling (defaults from settings).
        max_audio_bytes: Per-audio size ceiling.
        max_video_bytes: Per-video size ceiling.
        max_audio_duration_s: Max audio duration in seconds.
        max_video_duration_s: Max video duration in seconds.
        strict: Reserved for stricter EXIF/metadata rejection (currently a no-op
            beyond the standard checks).
        settings: Settings to read defaults from. Keyword args, when provided,
            override the corresponding setting.

    Example::

        validator = MediaValidator()
        result = validator.validate(blob, expected_kind=MediaKind.IMAGE)
        if not result.ok:
            raise MediaValidationError(result.reason)
    """

    def __init__(
        self,
        *,
        max_image_bytes: int | None = None,
        max_audio_bytes: int | None = None,
        max_video_bytes: int | None = None,
        max_audio_duration_s: float | None = None,
        max_video_duration_s: float | None = None,
        strict: bool = False,
        settings: Settings | None = None,
    ) -> None:
        """Initialize the validator, resolving limits from settings/overrides."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._max_bytes: dict[MediaKind, int] = {
            MediaKind.IMAGE: max_image_bytes
            if max_image_bytes is not None
            else settings.max_image_bytes,
            MediaKind.AUDIO: max_audio_bytes
            if max_audio_bytes is not None
            else settings.max_audio_bytes,
            MediaKind.VIDEO: max_video_bytes
            if max_video_bytes is not None
            else settings.max_video_bytes,
        }
        self._max_duration_s: dict[MediaKind, float] = {
            MediaKind.AUDIO: max_audio_duration_s
            if max_audio_duration_s is not None
            else settings.max_audio_duration_s,
            MediaKind.VIDEO: max_video_duration_s
            if max_video_duration_s is not None
            else settings.max_video_duration_s,
        }
        self.strict = strict

    def sniff(self, media: bytes | Path) -> tuple[str | None, MediaKind | None]:
        """Detect MIME and kind from magic bytes, without imposing limits.

        Args:
            media: Raw bytes or a path to read the header from.

        Returns:
            ``(mime, kind)`` or ``(None, None)`` when unrecognised.
        """
        head = self._read_head(media)
        # RIFF....WAVE → WAV audio.
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return "audio/wav", MediaKind.AUDIO
        # MP4 family: 'ftyp' box at offset 4.
        if head[4:8] == b"ftyp":
            return "video/mp4", MediaKind.VIDEO
        for signature, (mime, kind) in _MAGIC_BYTES.items():
            if head.startswith(signature):
                return mime, kind
        return None, None

    def validate(
        self,
        media: bytes | Path,
        *,
        expected_kind: MediaKind | None = None,
    ) -> MediaValidationResult:
        """Validate magic bytes, size and (when determinable) duration.

        Args:
            media: Raw bytes or a path.
            expected_kind: If given, reject media whose detected kind differs.

        Returns:
            A :class:`MediaValidationResult` (never raises for invalid media;
            callers decide whether to raise ``MediaValidationError``).
        """
        blob = media.read_bytes() if isinstance(media, Path) else media
        size_bytes = len(blob)
        mime, kind = self.sniff(blob)

        if kind is None:
            return MediaValidationResult(
                ok=False,
                reason="unknown or unsupported media format (magic bytes not recognised)",
                detected_mime=None,
                detected_kind=None,
                size_bytes=size_bytes,
                duration_s=None,
            )

        if expected_kind is not None and kind is not expected_kind:
            return MediaValidationResult(
                ok=False,
                reason=f"expected {expected_kind.value} but detected {kind.value}",
                detected_mime=mime,
                detected_kind=kind,
                size_bytes=size_bytes,
                duration_s=None,
            )

        max_bytes = self._max_bytes[kind]
        if size_bytes > max_bytes:
            return MediaValidationResult(
                ok=False,
                reason=f"media too large: {size_bytes} bytes exceeds {max_bytes} limit",
                detected_mime=mime,
                detected_kind=kind,
                size_bytes=size_bytes,
                duration_s=None,
            )

        duration_s = self._probe_duration(blob, mime)
        if duration_s is not None and kind in self._max_duration_s:
            max_duration = self._max_duration_s[kind]
            if duration_s > max_duration:
                return MediaValidationResult(
                    ok=False,
                    reason=f"duration {duration_s:.2f}s exceeds {max_duration}s limit",
                    detected_mime=mime,
                    detected_kind=kind,
                    size_bytes=size_bytes,
                    duration_s=duration_s,
                )

        return MediaValidationResult(
            ok=True,
            reason=None,
            detected_mime=mime,
            detected_kind=kind,
            size_bytes=size_bytes,
            duration_s=duration_s,
        )

    @staticmethod
    def _read_head(media: bytes | Path) -> bytes:
        """Read the first bytes of the media for sniffing."""
        if isinstance(media, Path):
            with media.open("rb") as fh:
                return fh.read(_SNIFF_BYTES)
        return media[:_SNIFF_BYTES]

    @staticmethod
    def _probe_duration(blob: bytes, mime: str | None) -> float | None:
        """Return duration in seconds when cheaply determinable, else None.

        Only WAV is supported here (via the stdlib ``wave`` module); other
        formats require container parsing and are left to downstream stages.
        """
        if mime != "audio/wav":
            return None
        try:
            with wave.open(BytesIO(blob), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                if rate <= 0:
                    return None
                return frames / float(rate)
        except (wave.Error, EOFError, struct.error):
            return None


__all__ = [
    "MediaKind",
    "MediaValidationResult",
    "MediaValidator",
]
