"""Tests for media security extensions (Fase F): sanitize_media, check_media_op, log_media."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.audit import AuditLogger
from prismal.security.media_validator import MediaKind
from prismal.security.sanitizer import InputSanitizer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GARBAGE = b"definitely-not-media" + b"\x00" * 16


class TestSanitizeMedia:
    def test_rejects_invalid_media(self) -> None:
        from prismal.core.exceptions import MediaValidationError

        sanitizer = InputSanitizer()
        with pytest.raises(MediaValidationError):
            sanitizer.sanitize_media(GARBAGE, MediaKind.IMAGE)

    def test_returns_bytes_for_valid_media(self) -> None:
        sanitizer = InputSanitizer()
        out = sanitizer.sanitize_media(PNG, MediaKind.IMAGE)
        assert isinstance(out, bytes)
        assert out.startswith(b"\x89PNG")

    def test_strips_exif_geolocation_from_image(self) -> None:
        pil = pytest.importorskip("PIL")
        from PIL import Image

        # Build a JPEG carrying an EXIF metadata tag (Software, 0x0131).
        img = Image.new("RGB", (4, 4), color=(10, 20, 30))
        exif = img.getexif()
        exif[0x0131] = "PrismalGPS 1.0"  # benign tag standing in for sensitive metadata
        buf = BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        with_exif = buf.getvalue()

        # Sanity: the original carries EXIF.
        assert Image.open(BytesIO(with_exif)).getexif()

        sanitizer = InputSanitizer()
        cleaned = sanitizer.sanitize_media(with_exif, MediaKind.IMAGE)

        # Cleaned image must no longer carry EXIF metadata.
        assert not Image.open(BytesIO(cleaned)).getexif()
        assert pil  # importorskip handle used


class TestCheckMediaOp:
    def test_allows_op_within_workspace(self, tmp_path: Path) -> None:
        target = tmp_path / "media" / "in.png"
        assert (
            ActionInterceptor.check_media_op("read", target, workspace_root=str(tmp_path)) is True
        )
        assert (
            ActionInterceptor.check_media_op("write", target, workspace_root=str(tmp_path)) is True
        )

    def test_blocks_op_outside_workspace(self, tmp_path: Path) -> None:
        outside = Path("/etc/shadow")
        assert (
            ActionInterceptor.check_media_op("read", outside, workspace_root=str(tmp_path)) is False
        )

    def test_blocks_blocked_prefix_even_without_workspace(self) -> None:
        assert ActionInterceptor.check_media_op("read", "/etc/passwd") is False

    def test_rejects_unknown_op(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="op"):
            ActionInterceptor.check_media_op("delete", tmp_path / "x.png")


class TestLogMedia:
    def test_writes_hash_and_modality_never_content(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_path)
        audit.log_media(
            "validated",
            sha256="abc123",
            modality="image",
            size_bytes=2048,
            duration_s=None,
        )
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["sha256"] == "abc123"
        assert record["modality"] == "image"
        assert record["size_bytes"] == 2048
        # The blob content must never be present.
        assert "content" not in record
