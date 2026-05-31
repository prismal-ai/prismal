"""Input sanitizer module — Security Layer L1.

Applies a chain of text transformations to remove potentially dangerous
content from user input before it reaches the guardrails or LLM.
"""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.security.media_validator import MediaKind, MediaValidator

logger = get_logger("prismal.security.sanitizer")

# Matches ASCII control chars except \t (0x09), \n (0x0A), \r (0x0D)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_INPUT_LENGTH: int = 32_768
"""Default maximum input length in characters."""


class InputSanitizer:
    """L1 security layer: control char removal, unicode normalization, length cap.

    Sanitization pipeline (in order):
    1. Strip ASCII control characters (preserves \\t, \\n, \\r).
    2. NFKC unicode normalization to defeat homoglyph attacks.
    3. Enforce length limit to prevent resource exhaustion.
    """

    def strip_control_chars(self, text: str) -> str:
        """Remove ASCII control characters, preserving \\t, \\n, \\r.

        Args:
            text: Input string.

        Returns:
            String with control characters removed.
        """
        return _CONTROL_CHAR_RE.sub("", text)

    def normalize_unicode(self, text: str) -> str:
        """Apply NFKC unicode normalization to defeat homoglyph attacks.

        Args:
            text: Input string.

        Returns:
            NFKC-normalized string.
        """
        return unicodedata.normalize("NFKC", text)

    def enforce_length_limit(self, text: str, max_chars: int = MAX_INPUT_LENGTH) -> str:
        """Truncate text to max_chars if it exceeds the limit.

        Args:
            text: Input string.
            max_chars: Maximum allowed length (default: 32768).

        Returns:
            Original string if within limit, or truncated string.
        """
        if len(text) > max_chars:
            logger.warning(
                "input_truncated",
                original_length=len(text),
                max_chars=max_chars,
            )
            return text[:max_chars]
        return text

    def sanitize(self, text: str, max_chars: int = MAX_INPUT_LENGTH) -> str:
        """Apply the full sanitization pipeline.

        Steps: strip control chars → normalize unicode → enforce length.

        Args:
            text: Raw input text.
            max_chars: Maximum allowed length after sanitization.

        Returns:
            Sanitized string.
        """
        text = self.strip_control_chars(text)
        text = self.normalize_unicode(text)
        return self.enforce_length_limit(text, max_chars)

    def sanitize_media(
        self,
        blob: bytes,
        kind: MediaKind,
        *,
        validator: MediaValidator | None = None,
    ) -> bytes:
        """Validate media and strip risky metadata before it reaches an agent.

        Runs :class:`MediaValidator` first (magic bytes, size, duration); on
        rejection raises ``MediaValidationError``. For images, EXIF/metadata is
        stripped best-effort (requires Pillow; skipped with a warning when it is
        unavailable or the bytes are not a decodable image).

        Args:
            blob: Raw media bytes.
            kind: Expected media kind.
            validator: Optional injected validator (defaults to a fresh one).

        Returns:
            Sanitized media bytes (EXIF-stripped for valid images).

        Raises:
            MediaValidationError: If the media fails validation.
        """
        from prismal.core.exceptions import MediaValidationError
        from prismal.security.media_validator import MediaKind as _MediaKind
        from prismal.security.media_validator import MediaValidator

        validator = validator or MediaValidator()
        result = validator.validate(blob, expected_kind=kind)
        if not result.ok:
            raise MediaValidationError(result.reason or "invalid media")
        if kind is _MediaKind.IMAGE:
            return self._strip_image_metadata(blob)
        return blob

    @staticmethod
    def _strip_image_metadata(blob: bytes) -> bytes:
        """Re-encode an image without metadata; return original on any failure."""
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:
            logger.warning("exif_strip_skipped_no_pillow")
            return blob
        try:
            with Image.open(BytesIO(blob)) as img:
                img.load()
                image_format = img.format
                mode, size, raw = img.mode, img.size, img.tobytes()
            # Rebuild from raw pixels only — drops EXIF and every metadata chunk.
            clean = Image.frombytes(mode, size, raw)
            out = BytesIO()
            clean.save(out, format=image_format)
            return out.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            logger.warning("exif_strip_failed")
            return blob


__all__ = ["MAX_INPUT_LENGTH", "InputSanitizer"]
