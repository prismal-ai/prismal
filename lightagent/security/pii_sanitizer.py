"""PII sanitizer — replaces personally identifiable information with tokens.

Used by :class:`~lightagent.memory.long_term_store.LongTermMemoryStore` to
enforce SPEC-039 AC-039-2: *any* value written to the long-term store must
be stripped of email addresses, phone numbers, SSNs, and credit card
numbers BEFORE it touches persistent storage. Raw PII never reaches disk.

The PII regex patterns mirror the ones declared in
``lightagent/security/patterns/injection_patterns.yaml`` under the
``output_patterns.pii`` group so the scanner and the sanitizer stay in
sync.
"""

from __future__ import annotations

import re
from re import Pattern

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.security.pii_sanitizer")


# PII detection patterns. Mirrors ``output_patterns.pii`` in
# ``injection_patterns.yaml``; kept in sync intentionally.
_EMAIL_RE: Pattern[str] = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_US_RE: Pattern[str] = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?[0-9]{3}\)?[-.\s]?)[0-9]{3}[-.\s]?[0-9]{4}\b"
)
_SSN_RE: Pattern[str] = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[\s\-]?(?!00)\d{2}[\s\-]?(?!0000)\d{4}\b"
)
_CREDIT_CARD_RE: Pattern[str] = re.compile(
    r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"
)


class PIISanitizer:
    """Replaces PII tokens in free text with opaque placeholders.

    Designed as a safety net for long-term memory persistence: calling
    :meth:`sanitize` on a value guarantees that no email / phone / SSN /
    credit card number survives in the output string, even at the cost of
    removing otherwise useful context. Order of substitution matters —
    credit cards and SSNs are matched before phone numbers because the
    phone regex is permissive enough to overlap with 10-digit card
    fragments.
    """

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Replace detected PII in ``text`` with typed placeholders.

        The method is safe to call on any string, including empty strings
        and strings that contain no PII (returned unchanged apart from
        being cast to ``str`` if needed). A DEBUG log entry is emitted
        only when substitutions actually happened — we *never* log the
        original value because doing so would defeat the whole point of
        sanitization.

        Args:
            text: Arbitrary text value about to be persisted.

        Returns:
            The same text with any PII occurrences replaced by
            ``[EMAIL]``, ``[PHONE]``, ``[SSN]`` or ``[CREDIT_CARD]``.
        """
        if not text:
            return text

        original_len = len(text)

        # Match credit card and SSN first so their digits are not consumed
        # by the more permissive phone regex.
        sanitized = _CREDIT_CARD_RE.sub("[CREDIT_CARD]", text)
        sanitized = _SSN_RE.sub("[SSN]", sanitized)
        sanitized = _EMAIL_RE.sub("[EMAIL]", sanitized)
        sanitized = _PHONE_US_RE.sub("[PHONE]", sanitized)

        if sanitized != text:
            logger.debug(
                "pii_sanitized",
                original_length=original_len,
                sanitized_length=len(sanitized),
            )

        return sanitized


__all__ = ["PIISanitizer"]
