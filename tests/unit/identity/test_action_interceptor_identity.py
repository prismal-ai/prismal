"""ActionInterceptor ↔ identity PolicyEngine integration (Phase IDN — ID6-01/03).

The interceptor gains ``check_identity_policy`` — the identity analogue of the
Phase H ``check_tool_policy``: it consults the identity ``PolicyEngine``, audits a
DENY with the identity DID (never a secret), and raises ``PolicyDenied`` on a hard
deny; ``ALLOW``/``REQUIRE_HITL`` are returned for the caller to honour.
"""

from __future__ import annotations

import pytest

from prismal.core.exceptions import PolicyDenied
from prismal.identity.policy import IdentityPolicy, PolicyEngine
from prismal.identity.types import AgentIdentity, PolicyEffect, Scope
from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.permissions import PermissionManager


class _SpyAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log_event(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _coder() -> AgentIdentity:
    return AgentIdentity(
        did="did:key:zCoder", agent_name="coder", scopes=(Scope("tools:write_file"),)
    )


def _interceptor() -> tuple[ActionInterceptor, _SpyAudit]:
    audit = _SpyAudit()
    return ActionInterceptor(permission_manager=PermissionManager(), audit_logger=audit), audit


def test_allow_returns_decision() -> None:
    engine = PolicyEngine(
        [
            IdentityPolicy(
                "coder", "tool_call", "tools:write_file", PolicyEffect.ALLOW, "tools:write_file"
            )
        ]
    )
    interceptor, _ = _interceptor()
    decision = interceptor.check_identity_policy(
        identity=_coder(), action="tool_call", resource="tools:write_file", policy_engine=engine
    )
    assert decision.effect is PolicyEffect.ALLOW


def test_deny_raises_and_audits_identity_did() -> None:
    engine = PolicyEngine([IdentityPolicy("*", "tool_call", "secrets:*", PolicyEffect.DENY)])
    interceptor, audit = _interceptor()
    with pytest.raises(PolicyDenied):
        interceptor.check_identity_policy(
            identity=_coder(),
            action="tool_call",
            resource="secrets:db_password",
            policy_engine=engine,
        )
    assert audit.events, "a denied decision must be audited"
    event_type, payload = audit.events[-1]
    assert event_type == "identity_policy_denied"
    # the record carries the identity DID (ID6-03) and only identifiers/rules —
    # the payload keys are a fixed, secret-free set (no credential value).
    assert payload["identity"] == "did:key:zCoder"
    assert set(payload) == {"identity", "action", "resource", "rule", "reason"}


def test_require_hitl_is_returned_not_raised() -> None:
    engine = PolicyEngine(
        [IdentityPolicy("coder", "tool_call", "tools:delete_file", PolicyEffect.REQUIRE_HITL)]
    )
    interceptor, _ = _interceptor()
    decision = interceptor.check_identity_policy(
        identity=_coder(), action="tool_call", resource="tools:delete_file", policy_engine=engine
    )
    assert decision.effect is PolicyEffect.REQUIRE_HITL
