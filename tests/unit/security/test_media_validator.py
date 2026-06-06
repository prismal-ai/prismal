"""Tests for MediaValidator (Fase F, SPEC-MM-SEC-001)."""

from __future__ import annotations

import struct
import wave
from io import BytesIO
from pathlib import Path

import pytest

from prismal.security.media_validator import (
    MediaKind,
    MediaValidationResult,
    MediaValidator,
)

# ── Minimal magic-byte payloads (no real media needed) ────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
MP3 = b"ID3\x04\x00" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 32
GARBAGE = b"not-a-real-media-file-at-all" + b"\x00" * 16


def _wav_bytes(*, framerate: int = 8000, n_frames: int = 8000) -> bytes:
    """Build an in-memory mono 16-bit WAV; duration = n_frames / framerate."""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(struct.pack("<h", 0) * n_frames)
    return buf.getvalue()


class TestSniff:
    @pytest.mark.parametrize(
        ("blob", "mime", "kind"),
        [
            (PNG, "image/png", MediaKind.IMAGE),
            (JPEG, "image/jpeg", MediaKind.IMAGE),
            (GIF, "image/gif", MediaKind.IMAGE),
            (MP3, "audio/mpeg", MediaKind.AUDIO),
            (MP4, "video/mp4", MediaKind.VIDEO),
            (WEBM, "video/webm", MediaKind.VIDEO),
        ],
    )
    def test_detects_known_formats(self, blob: bytes, mime: str, kind: MediaKind) -> None:
        v = MediaValidator()
        detected_mime, detected_kind = v.sniff(blob)
        assert detected_mime == mime
        assert detected_kind is kind

    def test_riff_requires_wave_header(self) -> None:
        v = MediaValidator()
        # RIFF without WAVE tag → not recognised as audio.
        not_wav = b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 16
        _, kind = v.sniff(not_wav)
        assert kind is None
        # RIFF + WAVE → audio/wav.
        mime, kind = v.sniff(_wav_bytes())
        assert mime == "audio/wav"
        assert kind is MediaKind.AUDIO

    def test_unknown_returns_none(self) -> None:
        v = MediaValidator()
        assert v.sniff(GARBAGE) == (None, None)


class TestValidate:
    def test_valid_image_passes(self) -> None:
        v = MediaValidator()
        result = v.validate(PNG, expected_kind=MediaKind.IMAGE)
        assert isinstance(result, MediaValidationResult)
        assert result.ok is True
        assert result.reason is None
        assert result.detected_kind is MediaKind.IMAGE
        assert result.detected_mime == "image/png"
        assert result.size_bytes == len(PNG)

    def test_kind_mismatch_rejected(self) -> None:
        v = MediaValidator()
        # Bytes are an MP4 video but the caller expected an image.
        result = v.validate(MP4, expected_kind=MediaKind.IMAGE)
        assert result.ok is False
        assert result.reason is not None
        assert "image" in result.reason.lower()

    def test_unknown_format_rejected(self) -> None:
        v = MediaValidator()
        result = v.validate(GARBAGE, expected_kind=MediaKind.IMAGE)
        assert result.ok is False
        assert result.detected_kind is None

    def test_oversize_image_rejected(self) -> None:
        v = MediaValidator(max_image_bytes=64)
        big = PNG + b"\x00" * 200
        result = v.validate(big, expected_kind=MediaKind.IMAGE)
        assert result.ok is False
        assert "size" in result.reason.lower() or "large" in result.reason.lower()

    def test_size_limit_is_per_kind(self) -> None:
        # A small audio limit must not reject a large-but-within-image-limit image.
        v = MediaValidator(max_audio_bytes=8)
        result = v.validate(PNG, expected_kind=MediaKind.IMAGE)
        assert result.ok is True

    def test_audio_duration_over_limit_rejected(self) -> None:
        v = MediaValidator(max_audio_duration_s=0.5)
        # 8000 frames @ 8000 Hz = 1.0 s > 0.5 s limit.
        result = v.validate(_wav_bytes(framerate=8000, n_frames=8000))
        assert result.ok is False
        assert "duration" in result.reason.lower()
        assert result.duration_s == pytest.approx(1.0)

    def test_audio_duration_within_limit_passes(self) -> None:
        v = MediaValidator(max_audio_duration_s=2.0)
        result = v.validate(_wav_bytes(framerate=8000, n_frames=8000))
        assert result.ok is True
        assert result.duration_s == pytest.approx(1.0)

    def test_image_has_no_duration(self) -> None:
        v = MediaValidator()
        result = v.validate(PNG, expected_kind=MediaKind.IMAGE)
        assert result.duration_s is None

    def test_accepts_path(self, tmp_path: Path) -> None:
        p = tmp_path / "tiny.png"
        p.write_bytes(PNG)
        v = MediaValidator()
        result = v.validate(p, expected_kind=MediaKind.IMAGE)
        assert result.ok is True
        assert result.size_bytes == len(PNG)

    def test_no_expected_kind_accepts_any_known(self) -> None:
        v = MediaValidator()
        assert v.validate(MP4).ok is True
        assert v.validate(PNG).ok is True


class TestSettingsIntegration:
    def test_settings_supply_defaults(self) -> None:
        from prismal.core.config import Settings

        s = Settings(max_image_bytes=1024)  # minimum allowed by the Settings constraint
        v = MediaValidator(settings=s)
        result = v.validate(PNG + b"\x00" * 2000, expected_kind=MediaKind.IMAGE)
        assert result.ok is False

    def test_kwargs_override_settings(self) -> None:
        from prismal.core.config import Settings

        s = Settings(max_image_bytes=1024)
        v = MediaValidator(settings=s, max_image_bytes=10_000_000)
        result = v.validate(PNG + b"\x00" * 2000, expected_kind=MediaKind.IMAGE)
        assert result.ok is True
