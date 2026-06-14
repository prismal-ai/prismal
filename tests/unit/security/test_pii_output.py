"""Tests for PII redaction on outputs (Phase H — SPEC-HRD-PII-001)."""

from __future__ import annotations

from prismal.core.config import Settings
from prismal.security.pii_sanitizer import redact_output


def test_redact_output_disabled_is_noop() -> None:
    text = "Contact me at jane@example.com or 555-123-4567."
    settings = Settings(hardening_pii_output=False)
    assert redact_output(text, settings=settings) == text


def test_redact_output_redacts_when_enabled() -> None:
    text = "Email jane@example.com, SSN 123-45-6789."
    settings = Settings(hardening_enabled=True, hardening_pii_output=True)
    out = redact_output(text, settings=settings)
    assert "jane@example.com" not in out
    assert "123-45-6789" not in out
    assert "[EMAIL]" in out


def test_redact_output_empty_text() -> None:
    settings = Settings(hardening_enabled=True, hardening_pii_output=True)
    assert redact_output("", settings=settings) == ""
