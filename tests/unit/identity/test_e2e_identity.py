"""End-to-end identity governance with fakes (Phase IDN — ID7-06).

Composes the identity ports, issues a scoped identity, and drives a tool-call
through the ActionInterceptor seam: an in-scope action is allowed, an
out-of-scope action is denied, and a high-risk action routes to HITL — no LLM,
no services.
"""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import PolicyDenied
from prismal.identity.policy import PolicyEngine, load_identity_policies
from prismal.identity.provider import LocalIdentityProvider
from prismal.identity.types import PolicyEffect, Scope
from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.permissions import PermissionManager

_POLICY = """
default: deny
policies:
  - identity: "coder"
    action: "tool_call"
    resource: "tools:write_file"
    effect: allow
    require_scope: "tools:write_file"
  - identity: "coder"
    action: "tool_call"
    resource: "tools:delete_file"
    effect: require_hitl
"""


@pytest.fixture
def interceptor_and_engine(tmp_path):
    policy_file = tmp_path / "identity_policies.yaml"
    policy_file.write_text(_POLICY)
    engine = PolicyEngine(load_identity_policies(str(policy_file)), settings=Settings())
    interceptor = ActionInterceptor(permission_manager=PermissionManager())
    provider = LocalIdentityProvider()
    identity = provider.issue(agent_name="coder", scopes=(Scope("tools:write_file"),))
    return interceptor, engine, identity


def test_in_scope_action_is_allowed(interceptor_and_engine) -> None:
    interceptor, engine, identity = interceptor_and_engine
    decision = interceptor.check_identity_policy(
        identity=identity, action="tool_call", resource="tools:write_file", policy_engine=engine
    )
    assert decision.effect is PolicyEffect.ALLOW


def test_out_of_scope_action_is_denied(interceptor_and_engine) -> None:
    interceptor, engine, identity = interceptor_and_engine
    with pytest.raises(PolicyDenied):
        interceptor.check_identity_policy(
            identity=identity,
            action="tool_call",
            resource="secrets:db_password",
            policy_engine=engine,
        )


def test_high_risk_action_routes_to_hitl(interceptor_and_engine) -> None:
    interceptor, engine, identity = interceptor_and_engine
    decision = interceptor.check_identity_policy(
        identity=identity, action="tool_call", resource="tools:delete_file", policy_engine=engine
    )
    assert decision.effect is PolicyEffect.REQUIRE_HITL


def test_issued_identity_is_verifiable(interceptor_and_engine) -> None:
    _, _, identity = interceptor_and_engine
    assert identity.did.startswith("did:key:z")
    assert identity.scopes == (Scope("tools:write_file"),)
