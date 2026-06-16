"""Tests for the identity value objects (Phase IDN — SPEC-IDN-TYP-001).

ID1 "done when": value objects round-trip; ``Scope.matches`` globs the resource;
secrets live only inside ``Credential.value`` (a ``SecretStr``) and never leak
into ``repr``.
"""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import SecretStr

from prismal.identity.types import (
    AgentIdentity,
    Credential,
    OnBehalfToken,
    PolicyDecision,
    PolicyEffect,
    Scope,
)

# ── Scope ───────────────────────────────────────────────────────────────────


def test_scope_matches_exact_resource() -> None:
    """A scope covers a resource it names exactly."""
    assert Scope("rag:read").matches("tool_call", "rag:read") is True


def test_scope_matches_glob_resource() -> None:
    """A wildcard scope covers any resource under its prefix."""
    assert Scope("rag:*").matches("tool_call", "rag:read") is True
    assert Scope("rag:*").matches("tool_call", "rag:write") is True


def test_scope_rejects_resource_outside_pattern() -> None:
    """A scope does not cover a resource its pattern does not match."""
    assert Scope("rag:read").matches("tool_call", "rag:write") is False
    assert Scope("tools:write_file").matches("tool_call", "tools:delete_file") is False


def test_scope_is_frozen() -> None:
    """Scope is an immutable value object."""
    s = Scope("rag:read")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.resource = "rag:*"  # type: ignore[misc]


# ── AgentIdentity ─────────────────────────────────────────────────────────────


def test_agent_identity_defaults() -> None:
    """An identity needs only a DID and an agent name; the rest defaults empty."""
    ident = AgentIdentity(did="did:key:z6Mkabc", agent_name="coder")
    assert ident.did == "did:key:z6Mkabc"
    assert ident.agent_name == "coder"
    assert ident.org_id is None
    assert ident.scopes == ()
    assert ident.credential_ref is None
    assert ident.expires_at is None


def test_agent_identity_round_trips_scopes_and_tenant() -> None:
    """Scopes and tenant survive construction."""
    scopes = (Scope("tools:write_file"), Scope("rag:read"))
    ident = AgentIdentity(
        did="did:web:org.example:agents:coder",
        agent_name="coder",
        org_id="acme",
        scopes=scopes,
        credential_ref="vault://acme/coder",
    )
    assert ident.org_id == "acme"
    assert ident.scopes == scopes
    assert ident.credential_ref == "vault://acme/coder"


def test_agent_identity_is_frozen() -> None:
    """AgentIdentity is immutable."""
    ident = AgentIdentity(did="did:key:z6Mkabc", agent_name="coder")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ident.did = "did:key:other"  # type: ignore[misc]


# ── Credential ────────────────────────────────────────────────────────────────


def test_credential_holds_a_secret() -> None:
    """The secret is reachable only via get_secret_value()."""
    cred = Credential(ref="vault://acme/coder", value=SecretStr("s3cr3t"))
    assert cred.value.get_secret_value() == "s3cr3t"


def test_credential_never_leaks_secret_in_repr() -> None:
    """The raw secret must not appear in repr (SecretStr masks it)."""
    cred = Credential(ref="vault://acme/coder", value=SecretStr("s3cr3t"))
    assert "s3cr3t" not in repr(cred)


# ── OnBehalfToken ─────────────────────────────────────────────────────────────


def test_on_behalf_token_defaults_empty_chain() -> None:
    """A freshly minted token starts with an empty delegation chain."""
    tok = OnBehalfToken(
        token_id="t1",
        subject="user@example.com",
        scopes=(Scope("rag:read"),),
        expires_at=1_000.0,
    )
    assert tok.subject == "user@example.com"
    assert tok.chain == ()


# ── PolicyEffect / PolicyDecision ─────────────────────────────────────────────


def test_policy_effect_values() -> None:
    """The three effects round-trip from their string values."""
    assert PolicyEffect.ALLOW == "allow"
    assert PolicyEffect.DENY == "deny"
    assert PolicyEffect.REQUIRE_HITL == "require_hitl"
    assert PolicyEffect("require_hitl") is PolicyEffect.REQUIRE_HITL


def test_policy_decision_defaults_empty_reason() -> None:
    """A decision carries an effect and the rule that produced it."""
    d = PolicyDecision(effect=PolicyEffect.DENY, rule="deny-egress")
    assert d.effect is PolicyEffect.DENY
    assert d.rule == "deny-egress"
    assert d.reason == ""
