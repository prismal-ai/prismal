"""Tests for the identity policy engine (Phase IDN — SPEC-IDN-POL-001).

ID4 "done when": an out-of-scope action is denied; identity rules resolve
most-specific-wins; tool-call rules still flow through the delegated Phase H
``ToolPolicyEngine``; the YAML loads (default + catch-all).
"""

from __future__ import annotations

import pytest

from prismal.agents.extension import PolicyPort
from prismal.core.exceptions import IdentityConfigError
from prismal.identity.policy import IdentityPolicy, PolicyEngine, load_identity_policies
from prismal.identity.types import AgentIdentity, PolicyEffect, Scope
from prismal.security.tool_policy import PolicyEffect as ToolEffect
from prismal.security.tool_policy import ToolPolicy, ToolPolicyEngine


def _coder() -> AgentIdentity:
    return AgentIdentity(
        did="did:key:zCoder",
        agent_name="coder",
        scopes=(Scope("rag:read"), Scope("tools:write_file")),
    )


# ── Port conformance ──────────────────────────────────────────────────────────


def test_policy_engine_conforms_to_port() -> None:
    assert isinstance(PolicyEngine([]), PolicyPort)


# ── Identity rules: allow / deny / hitl / scope ───────────────────────────────


def test_allow_rule_with_held_scope_allows() -> None:
    engine = PolicyEngine(
        [
            IdentityPolicy(
                "coder", "tool_call", "tools:write_file", PolicyEffect.ALLOW, "tools:write_file"
            )
        ]
    )
    d = engine.allow(identity=_coder(), action="tool_call", resource="tools:write_file")
    assert d.effect is PolicyEffect.ALLOW


def test_allow_rule_missing_scope_denies() -> None:
    """An allow rule whose require_scope the identity lacks is a least-privilege deny."""
    engine = PolicyEngine(
        [IdentityPolicy("coder", "tool_call", "db:select", PolicyEffect.ALLOW, "db:read")]
    )
    d = engine.allow(identity=_coder(), action="tool_call", resource="db:select")
    assert d.effect is PolicyEffect.DENY


def test_deny_rule_denies() -> None:
    engine = PolicyEngine([IdentityPolicy("*", "tool_call", "secrets:*", PolicyEffect.DENY)])
    d = engine.allow(identity=_coder(), action="tool_call", resource="secrets:db_password")
    assert d.effect is PolicyEffect.DENY


def test_require_hitl_rule_surfaces_hitl() -> None:
    engine = PolicyEngine(
        [IdentityPolicy("coder", "tool_call", "tools:delete_file", PolicyEffect.REQUIRE_HITL)]
    )
    d = engine.allow(identity=_coder(), action="tool_call", resource="tools:delete_file")
    assert d.effect is PolicyEffect.REQUIRE_HITL


def test_no_matching_policy_denies_by_least_privilege() -> None:
    d = PolicyEngine([]).allow(identity=_coder(), action="tool_call", resource="rag:read")
    assert d.effect is PolicyEffect.DENY


# ── Most-specific-wins ────────────────────────────────────────────────────────


def test_specific_allow_beats_general_deny() -> None:
    """A specific resource allow wins over a broad deny (researcher egress case)."""
    researcher = AgentIdentity(
        did="did:key:zR", agent_name="researcher", scopes=(Scope("net:egress"),)
    )
    engine = PolicyEngine(
        [
            IdentityPolicy("*", "tool_call", "https://*", PolicyEffect.DENY),
            IdentityPolicy(
                "researcher",
                "tool_call",
                "https://api.search.example/*",
                PolicyEffect.ALLOW,
                "net:egress",
            ),
        ]
    )
    d = engine.allow(
        identity=researcher, action="tool_call", resource="https://api.search.example/q"
    )
    assert d.effect is PolicyEffect.ALLOW


def test_identity_glob_matches_did_or_agent_name() -> None:
    engine = PolicyEngine(
        [IdentityPolicy("did:key:zCoder", "*", "rag:read", PolicyEffect.ALLOW, "rag:read")]
    )
    d = engine.allow(identity=_coder(), action="tool_call", resource="rag:read")
    assert d.effect is PolicyEffect.ALLOW


# ── Phase H delegation ────────────────────────────────────────────────────────


def _identity_allow_all() -> list[IdentityPolicy]:
    return [IdentityPolicy("*", "*", "*", PolicyEffect.ALLOW)]


def test_delegates_tool_call_deny_to_phase_h() -> None:
    tool_engine = ToolPolicyEngine(
        [ToolPolicy(agent="coder", tool="delete_file", effect=ToolEffect.DENY)]
    )
    engine = PolicyEngine(_identity_allow_all(), tool_policy=tool_engine)
    d = engine.allow(
        identity=_coder(), action="tool_call", resource="tools:delete_file", context={"args": {}}
    )
    assert d.effect is PolicyEffect.DENY
    assert "tool_policy" in d.rule


def test_delegated_hitl_surfaces_even_when_identity_allows() -> None:
    tool_engine = ToolPolicyEngine(
        [ToolPolicy(agent="coder", tool="write_file", effect=ToolEffect.REQUIRE_HITL)]
    )
    engine = PolicyEngine(_identity_allow_all(), tool_policy=tool_engine)
    d = engine.allow(
        identity=_coder(), action="tool_call", resource="tools:write_file", context={"args": {}}
    )
    assert d.effect is PolicyEffect.REQUIRE_HITL


def test_delegated_allow_then_identity_allows() -> None:
    tool_engine = ToolPolicyEngine([ToolPolicy(agent="*", tool="*", effect=ToolEffect.ALLOW)])
    engine = PolicyEngine(
        [
            IdentityPolicy(
                "coder", "tool_call", "tools:write_file", PolicyEffect.ALLOW, "tools:write_file"
            )
        ],
        tool_policy=tool_engine,
    )
    d = engine.allow(
        identity=_coder(), action="tool_call", resource="tools:write_file", context={"args": {}}
    )
    assert d.effect is PolicyEffect.ALLOW


# ── load_identity_policies ────────────────────────────────────────────────────


def test_load_shipped_policy_file() -> None:
    policies = load_identity_policies("config/identity_policies.yaml")
    assert len(policies) > 1
    # the file's `default: deny` becomes a lowest-specificity catch-all
    catch_all = policies[-1]
    assert catch_all.identity == "*"
    assert catch_all.action == "*"
    assert catch_all.resource == "*"
    assert catch_all.effect is PolicyEffect.DENY


def test_load_missing_file_returns_empty() -> None:
    assert load_identity_policies("does/not/exist.yaml") == []


def test_load_invalid_effect_raises(tmp_path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("version: 1\npolicies:\n  - identity: '*'\n    effect: bogus\n")
    with pytest.raises(IdentityConfigError):
        load_identity_policies(str(bad))


def test_load_malformed_yaml_raises(tmp_path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("policies: [unclosed\n")
    with pytest.raises(IdentityConfigError):
        load_identity_policies(str(bad))


def test_load_policies_not_a_list_raises(tmp_path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("policies: not-a-list\n")
    with pytest.raises(IdentityConfigError):
        load_identity_policies(str(bad))


def test_load_policy_item_not_a_mapping_raises(tmp_path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("policies:\n  - just-a-string\n")
    with pytest.raises(IdentityConfigError):
        load_identity_policies(str(bad))


def test_load_invalid_default_raises(tmp_path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("default: bogus\npolicies: []\n")
    with pytest.raises(IdentityConfigError):
        load_identity_policies(str(bad))
