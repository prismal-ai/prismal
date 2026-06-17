"""Tests for the identity config fields (Phase IDN — SPEC-IDN-CFG-001).

ID1 "done when": settings parse from ``PRISMAL_*`` env vars, the defaults match
SPEC-IDN-CFG-001, and a bad ``identity_mode``/``identity_provider``/
``identity_did_method`` (or an ``oidc`` provider without an issuer) raises
``IdentityConfigError`` at load time.
"""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import IdentityConfigError

# ── Defaults (SPEC-IDN-CFG-001) ──────────────────────────────────────────────


def test_identity_defaults_match_spec() -> None:
    """All identity fields default to the SPEC-IDN-CFG-001 values."""
    s = Settings()
    assert s.identity_enabled is False
    assert s.identity_mode == "warn"
    assert s.identity_provider == "local"
    assert s.identity_did_method == "key"
    assert s.identity_did_web_domain == ""
    assert s.identity_vault == "env"
    assert s.identity_policy_path == "config/identity_policies.yaml"
    assert s.identity_on_behalf_enabled is False
    assert s.identity_on_behalf_ttl_s == 900
    assert s.oidc_issuer == ""
    assert s.oidc_client_id == ""


def test_identity_disabled_by_default_is_opt_in() -> None:
    """The master toggle is off by default (RF-IDN-001..008)."""
    assert Settings().identity_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_identity_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every identity field parses from its PRISMAL_* env var."""
    monkeypatch.setenv("PRISMAL_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_IDENTITY_MODE", "enforce")
    monkeypatch.setenv("PRISMAL_IDENTITY_PROVIDER", "oidc")
    monkeypatch.setenv("PRISMAL_IDENTITY_DID_METHOD", "web")
    monkeypatch.setenv("PRISMAL_IDENTITY_DID_WEB_DOMAIN", "org.example")
    monkeypatch.setenv("PRISMAL_IDENTITY_VAULT", "file")
    monkeypatch.setenv("PRISMAL_IDENTITY_ON_BEHALF_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_IDENTITY_ON_BEHALF_TTL_S", "300")
    monkeypatch.setenv("PRISMAL_OIDC_ISSUER", "https://login.example.com")
    monkeypatch.setenv("PRISMAL_OIDC_CLIENT_ID", "abc123")

    s = Settings()
    assert s.identity_enabled is True
    assert s.identity_mode == "enforce"
    assert s.identity_provider == "oidc"
    assert s.identity_did_method == "web"
    assert s.identity_did_web_domain == "org.example"
    assert s.identity_vault == "file"
    assert s.identity_on_behalf_enabled is True
    assert s.identity_on_behalf_ttl_s == 300
    assert s.oidc_issuer == "https://login.example.com"
    assert s.oidc_client_id == "abc123"


# ── Validation (_validate_identity) ──────────────────────────────────────────


def test_identity_rejects_unknown_mode() -> None:
    with pytest.raises(IdentityConfigError):
        Settings(identity_mode="bogus")


def test_identity_rejects_unknown_provider() -> None:
    with pytest.raises(IdentityConfigError):
        Settings(identity_provider="bogus")


def test_identity_rejects_unknown_did_method() -> None:
    with pytest.raises(IdentityConfigError):
        Settings(identity_did_method="bogus")


def test_identity_rejects_unknown_vault() -> None:
    with pytest.raises(IdentityConfigError):
        Settings(identity_vault="bogus")


def test_identity_oidc_requires_issuer() -> None:
    """Selecting the OIDC provider without an issuer is a config contradiction."""
    with pytest.raises(IdentityConfigError):
        Settings(identity_provider="oidc", oidc_issuer="")


def test_identity_oidc_with_issuer_is_valid() -> None:
    s = Settings(identity_provider="oidc", oidc_issuer="https://login.example.com")
    assert s.identity_provider == "oidc"
