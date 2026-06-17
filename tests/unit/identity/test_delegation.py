"""Tests for on-behalf-of delegation (Phase IDN — SPEC-IDN-DEL-001).

ID5 "done when": scopes can only narrow along ``propagate``; an expired or
revoked token fails ``validate``; the token threads through
``state["metadata"]["identity"]`` and round-trips serializably.
"""

from __future__ import annotations

import json

import pytest

from prismal.core.exceptions import DelegationError
from prismal.identity.delegation import (
    get_on_behalf,
    mint_on_behalf,
    propagate,
    put_on_behalf,
    revoke,
    validate,
)
from prismal.identity.types import Scope

_ISSUER = "did:key:zIssuer"


def _token():
    return mint_on_behalf(
        subject="user@example.com",
        scopes=(Scope("rag:*"), Scope("tools:write_file")),
        ttl_s=900,
        issuer=_ISSUER,
    )


# ── mint ──────────────────────────────────────────────────────────────────────


def test_mint_sets_subject_scopes_and_chain() -> None:
    token = _token()
    assert token.subject == "user@example.com"
    assert token.scopes == (Scope("rag:*"), Scope("tools:write_file"))
    assert token.chain == (_ISSUER,)  # issuer is the first link
    assert token.token_id  # opaque, non-empty


def test_mint_unique_token_ids() -> None:
    assert _token().token_id != _token().token_id


# ── validate ──────────────────────────────────────────────────────────────────


def test_validate_in_scope_within_ttl() -> None:
    token = _token()
    assert validate(token, action="tool_call", resource="rag:read") is True


def test_validate_out_of_scope_is_false() -> None:
    token = _token()
    assert validate(token, action="tool_call", resource="db:write") is False


def test_validate_expired_is_false() -> None:
    token = _token()
    assert (
        validate(token, action="tool_call", resource="rag:read", now=token.expires_at + 1) is False
    )


def test_validate_revoked_is_false() -> None:
    token = _token()
    revoke(token.token_id)
    assert validate(token, action="tool_call", resource="rag:read") is False


# ── propagate (narrow-only) ────────────────────────────────────────────────────


def test_propagate_appends_to_chain_and_keeps_scopes() -> None:
    token = _token()
    nxt = propagate(token, via="did:key:zAgent")
    assert nxt.chain == (_ISSUER, "did:key:zAgent")
    assert nxt.scopes == token.scopes
    assert nxt.token_id == token.token_id  # same delegation, extended chain


def test_propagate_can_narrow_scopes() -> None:
    token = _token()
    nxt = propagate(token, via="did:key:zAgent", scopes=(Scope("rag:read"),))
    assert nxt.scopes == (Scope("rag:read"),)


def test_propagate_cannot_widen_scopes() -> None:
    token = _token()
    with pytest.raises(DelegationError):
        propagate(token, via="did:key:zAgent", scopes=(Scope("secrets:db"),))


def test_propagated_token_still_validates_within_narrowed_scope() -> None:
    token = _token()
    nxt = propagate(token, via="did:key:zAgent", scopes=(Scope("rag:read"),))
    assert validate(nxt, action="tool_call", resource="rag:read") is True
    assert validate(nxt, action="tool_call", resource="tools:write_file") is False


# ── state threading (state["metadata"]["identity"]) ────────────────────────────


def test_put_then_get_round_trips() -> None:
    token = _token()
    state: dict = {}
    put_on_behalf(state, token)
    restored = get_on_behalf(state)
    assert restored == token


def test_get_on_behalf_absent_is_none() -> None:
    assert get_on_behalf({}) is None


def test_state_payload_is_json_serializable() -> None:
    """The token in state must be checkpoint-safe (no secrets, JSON-serializable)."""
    token = _token()
    state: dict = {}
    put_on_behalf(state, token)
    json.dumps(state)  # must not raise
