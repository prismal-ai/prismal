"""Tests that prismal.security re-exports the guardrails-modernization public API (GRD3-04)."""

from __future__ import annotations


def test_structured_output_guard_reexported_from_security_package() -> None:
    from prismal.security import StructuredOutputGuard, StructuredOutputVerdict

    assert StructuredOutputGuard is not None
    assert StructuredOutputVerdict is not None


def test_structured_output_guard_in_security_all() -> None:
    import prismal.security as security

    assert "StructuredOutputGuard" in security.__all__
    assert "StructuredOutputVerdict" in security.__all__
