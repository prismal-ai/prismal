"""Unit tests for InputSanitizer — L1 security layer."""

from __future__ import annotations

import pytest

from lightagent.security.sanitizer import MAX_INPUT_LENGTH, InputSanitizer


@pytest.fixture
def sanitizer() -> InputSanitizer:
    """Return a fresh InputSanitizer instance."""
    return InputSanitizer()


def test_strip_removes_null_byte(sanitizer: InputSanitizer) -> None:
    """Null byte (0x00) must be removed from input."""
    assert sanitizer.strip_control_chars("\x00hello") == "hello"


def test_strip_removes_bell(sanitizer: InputSanitizer) -> None:
    """Bell character (0x07) must be removed from input."""
    assert sanitizer.strip_control_chars("\x07test") == "test"


def test_strip_removes_0x1f(sanitizer: InputSanitizer) -> None:
    """Unit separator (0x1F) must be removed."""
    assert sanitizer.strip_control_chars("\x1f") == ""


def test_strip_removes_del_0x7f(sanitizer: InputSanitizer) -> None:
    """DEL character (0x7F) must be removed."""
    assert sanitizer.strip_control_chars("\x7f") == ""


def test_strip_preserves_tab(sanitizer: InputSanitizer) -> None:
    """Horizontal tab (0x09) must be preserved."""
    assert sanitizer.strip_control_chars("col1\tcol2") == "col1\tcol2"


def test_strip_preserves_newline(sanitizer: InputSanitizer) -> None:
    """Newline (0x0A) must be preserved."""
    assert sanitizer.strip_control_chars("line1\nline2") == "line1\nline2"


def test_strip_preserves_carriage_return(sanitizer: InputSanitizer) -> None:
    """Carriage return (0x0D) must be preserved."""
    assert sanitizer.strip_control_chars("line1\r\nline2") == "line1\r\nline2"


def test_strip_preserves_high_bytes(sanitizer: InputSanitizer) -> None:
    """Bytes above 0x7F (UTF-8 multibyte) must NOT be stripped."""
    assert sanitizer.strip_control_chars("café") == "café"


def test_strip_multiple_control_chars(sanitizer: InputSanitizer) -> None:
    """Multiple consecutive control chars must all be removed."""
    assert sanitizer.strip_control_chars("\x00\x01\x02abc\x03") == "abc"


def test_normalize_nfkc_ligature(sanitizer: InputSanitizer) -> None:
    """fi ligature (U+FB01) must decompose to 'fi'."""
    assert sanitizer.normalize_unicode("\ufb01le") == "file"


def test_normalize_nfkc_fullwidth(sanitizer: InputSanitizer) -> None:
    """Full-width ASCII must normalize to ASCII."""
    assert (
        sanitizer.normalize_unicode("\uff49\uff47\uff4e\uff4f\uff52\uff45") == "ignore"
    )


def test_normalize_nfkc_superscript(sanitizer: InputSanitizer) -> None:
    """Superscript digit (U+00B2) must normalize to plain digit '2'."""
    assert sanitizer.normalize_unicode("x\u00b2") == "x2"


def test_normalize_nfc_unchanged(sanitizer: InputSanitizer) -> None:
    """Normal ASCII must pass through NFKC unchanged."""
    assert sanitizer.normalize_unicode("Hello world!") == "Hello world!"


def test_enforce_length_within_limit(sanitizer: InputSanitizer) -> None:
    """Input within limit must be returned unchanged."""
    text = "short"
    assert sanitizer.enforce_length_limit(text) == text


def test_enforce_length_truncates_exactly(sanitizer: InputSanitizer) -> None:
    """Input exceeding limit must be truncated to exactly max_chars."""
    text = "a" * 100
    result = sanitizer.enforce_length_limit(text, max_chars=50)
    assert len(result) == 50


def test_enforce_length_default_is_32768(sanitizer: InputSanitizer) -> None:
    """Default max_chars must equal MAX_INPUT_LENGTH (32768)."""
    text = "a" * (MAX_INPUT_LENGTH + 1)
    assert len(sanitizer.enforce_length_limit(text)) == MAX_INPUT_LENGTH


def test_sanitize_chains_all_steps(sanitizer: InputSanitizer) -> None:
    """sanitize() must chain control-char removal, normalization, and length cap."""
    raw = "\x00\ufb01le" + "a" * 200
    result = sanitizer.sanitize(raw, max_chars=10)
    assert "\x00" not in result
    assert result.startswith("fi")
    assert len(result) == 10


def test_sanitize_clean_input_unchanged(sanitizer: InputSanitizer) -> None:
    """Clean ASCII input must pass through all pipeline steps unchanged."""
    text = "What is the weather in Caracas?"
    assert sanitizer.sanitize(text) == text


def test_sanitize_empty_string(sanitizer: InputSanitizer) -> None:
    """Empty string must be returned as empty string."""
    assert sanitizer.sanitize("") == ""


def test_sanitize_null_byte_injection(sanitizer: InputSanitizer) -> None:
    """Null bytes in injection attempts must be fully removed."""
    raw = "\x00ignore\x00previous"
    assert "\x00" not in sanitizer.sanitize(raw)


def test_sanitize_custom_max_chars(sanitizer: InputSanitizer) -> None:
    """sanitize() must forward custom max_chars to enforce_length_limit."""
    text = "x" * 50
    result = sanitizer.sanitize(text, max_chars=10)
    assert len(result) == 10
