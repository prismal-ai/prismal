"""Tests for the gateway exception hierarchy (Phase GW — SPEC-GW-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    GatewayConfigError,
    GatewayCredentialError,
    GatewayError,
    GatewayPaymentRequired,
    GatewayPolicyError,
    GatewayQuotaExhausted,
    PrismalError,
)


def test_gateway_error_is_prismal_error() -> None:
    assert issubclass(GatewayError, PrismalError)


@pytest.mark.parametrize(
    "exc",
    [
        GatewayConfigError,
        GatewayCredentialError,
        GatewayPolicyError,
        GatewayQuotaExhausted,
        GatewayPaymentRequired,
    ],
)
def test_subclasses_extend_gateway_error(exc: type[GatewayError]) -> None:
    assert issubclass(exc, GatewayError)


def test_policy_error_carries_reason_gateway_agent() -> None:
    err = GatewayPolicyError("variant_not_allowed", gateway="openrouter", agent="coder")
    assert err.reason == "variant_not_allowed"
    assert err.gateway == "openrouter"
    assert err.agent == "coder"
    assert "variant_not_allowed" in str(err)


def test_quota_exhausted_carries_window() -> None:
    err = GatewayQuotaExhausted(gateway="opencode-go", window="5h")
    assert (err.gateway, err.window) == ("opencode-go", "5h")
    assert "5h" in str(err)


def test_payment_required_carries_reason() -> None:
    err = GatewayPaymentRequired(gateway="openrouter", reason="key_limit")
    assert (err.gateway, err.reason) == ("openrouter", "key_limit")


def test_credential_error_names_ref_only() -> None:
    err = GatewayCredentialError("openrouter_api_key@acme", gateway="openrouter")
    assert err.auth_ref == "openrouter_api_key@acme"
    assert "openrouter_api_key@acme" in str(err)
