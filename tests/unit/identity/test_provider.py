"""Tests for the identity providers (Phase IDN — SPEC-IDN-PRV-001).

ID2 "done when": LocalIdentityProvider issues a verifiable did:key offline; a
revoked DID fails verify; the OIDC adapter maps a (fake) subject to an
AgentIdentity; FakeIdentityProvider is deterministic.
"""

from __future__ import annotations

from prismal.agents.extension import IdentityPort
from prismal.core.config import Settings
from prismal.identity.did import verify_did
from prismal.identity.provider import (
    FakeIdentityProvider,
    LocalIdentityProvider,
    OidcIdentityProvider,
)
from prismal.identity.types import Scope

# ── LocalIdentityProvider ─────────────────────────────────────────────────────


def test_local_provider_conforms_to_port() -> None:
    assert isinstance(LocalIdentityProvider(), IdentityPort)


def test_local_issue_produces_did_key() -> None:
    ident = LocalIdentityProvider().issue(agent_name="coder")
    assert ident.agent_name == "coder"
    assert ident.did.startswith("did:key:z")


def test_local_issue_round_trips_scopes_and_tenant() -> None:
    scopes = (Scope("tools:write_file"), Scope("rag:read"))
    ident = LocalIdentityProvider().issue(agent_name="coder", org_id="acme", scopes=scopes)
    assert ident.org_id == "acme"
    assert ident.scopes == scopes


def test_local_resolve_returns_issued_identity() -> None:
    provider = LocalIdentityProvider()
    ident = provider.issue(agent_name="coder")
    assert provider.resolve(ident.did) == ident


def test_local_resolve_unknown_returns_none() -> None:
    assert LocalIdentityProvider().resolve("did:key:zUnknown") is None


def test_local_verify_true_for_issued_false_after_revoke() -> None:
    provider = LocalIdentityProvider()
    ident = provider.issue(agent_name="coder")
    assert provider.verify(ident.did) is True
    provider.revoke(ident.did)
    assert provider.verify(ident.did) is False


async def test_local_signature_verifies_against_did() -> None:
    """The provider holds the private key and can sign so verify_did accepts it."""
    provider = LocalIdentityProvider()
    ident = provider.issue(agent_name="coder")
    signature = provider.sign(ident.did, ident.did.encode())
    assert await verify_did(ident.did, signature=signature) is True


def test_local_sign_unknown_did_raises() -> None:
    import pytest

    with pytest.raises(KeyError):
        LocalIdentityProvider().sign("did:key:zUnknown", b"payload")


# ── OidcIdentityProvider ──────────────────────────────────────────────────────


def _oidc_settings() -> Settings:
    return Settings(identity_provider="oidc", oidc_issuer="https://login.example.com")


def test_oidc_provider_conforms_to_port() -> None:
    assert isinstance(OidcIdentityProvider(_oidc_settings()), IdentityPort)


def test_oidc_maps_injected_subject_to_credential_ref() -> None:
    provider = OidcIdentityProvider(
        _oidc_settings(),
        subject_resolver=lambda agent_name, org_id: "subject-123",
    )
    ident = provider.issue(agent_name="coder", org_id="acme")
    assert ident.agent_name == "coder"
    assert ident.credential_ref == "subject-123"
    assert provider.verify(ident.did) is True


def test_oidc_default_resolver_derives_subject_from_issuer() -> None:
    provider = OidcIdentityProvider(_oidc_settings())
    ident = provider.issue(agent_name="coder", org_id="acme")
    assert ident.credential_ref is not None
    assert "login.example.com" in ident.credential_ref
    assert "coder" in ident.credential_ref


# ── FakeIdentityProvider ──────────────────────────────────────────────────────


def test_fake_provider_conforms_to_port() -> None:
    assert isinstance(FakeIdentityProvider(), IdentityPort)


def test_fake_is_deterministic() -> None:
    """Issuing the same agent/tenant twice yields the same DID (no I/O, no randomness)."""
    provider = FakeIdentityProvider()
    a = provider.issue(agent_name="coder", org_id="acme")
    b = provider.issue(agent_name="coder", org_id="acme")
    assert a.did == b.did


def test_fake_verify_and_revoke() -> None:
    provider = FakeIdentityProvider()
    ident = provider.issue(agent_name="coder")
    assert provider.verify(ident.did) is True
    assert provider.resolve(ident.did) == ident
    provider.revoke(ident.did)
    assert provider.verify(ident.did) is False
