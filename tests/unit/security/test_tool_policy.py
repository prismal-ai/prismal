"""Tests for the tool policy engine (Phase H — SPEC-HRD-POL-001)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import HardeningConfigError
from prismal.security.tool_policy import (
    PolicyDecision,
    PolicyEffect,
    ToolPolicy,
    ToolPolicyEngine,
    load_tool_policies,
)


def _engine(policies: list[ToolPolicy], default: str = "allow") -> ToolPolicyEngine:
    return ToolPolicyEngine(policies, settings=Settings(hardening_tool_policy_default=default))


# ── default effect ───────────────────────────────────────────────────────────


def test_default_allow_when_no_match() -> None:
    eng = _engine([], default="allow")
    decision = eng.evaluate(agent="coder", tool="write_file", args={}, call_count=0)
    assert isinstance(decision, PolicyDecision)
    assert decision.effect is PolicyEffect.ALLOW


def test_default_deny_when_no_match() -> None:
    eng = _engine([], default="deny")
    decision = eng.evaluate(agent="coder", tool="write_file", args={}, call_count=0)
    assert decision.effect is PolicyEffect.DENY


# ── allow / deny / require_hitl ──────────────────────────────────────────────


def test_require_hitl_for_destructive_tool() -> None:
    eng = _engine([ToolPolicy(agent="*", tool="delete_file", effect=PolicyEffect.REQUIRE_HITL)])
    decision = eng.evaluate(agent="coder", tool="delete_file", args={}, call_count=0)
    assert decision.effect is PolicyEffect.REQUIRE_HITL


def test_explicit_deny() -> None:
    eng = _engine([ToolPolicy(agent="*", tool="http_request", effect=PolicyEffect.DENY)])
    decision = eng.evaluate(agent="coder", tool="http_request", args={}, call_count=0)
    assert decision.effect is PolicyEffect.DENY


# ── most-specific-wins ───────────────────────────────────────────────────────


def test_most_specific_rule_wins() -> None:
    eng = _engine(
        [
            ToolPolicy(agent="*", tool="http_request", effect=PolicyEffect.DENY),
            ToolPolicy(agent="researcher", tool="http_request", effect=PolicyEffect.ALLOW),
        ]
    )
    # researcher gets the specific allow; everyone else gets the glob deny.
    assert (
        eng.evaluate(agent="researcher", tool="http_request", args={}, call_count=0).effect
        is PolicyEffect.ALLOW
    )
    assert (
        eng.evaluate(agent="coder", tool="http_request", args={}, call_count=0).effect
        is PolicyEffect.DENY
    )


# ── arg constraints ──────────────────────────────────────────────────────────


def test_arg_constraint_violation_denies() -> None:
    eng = _engine(
        [
            ToolPolicy(
                agent="coder",
                tool="write_file",
                effect=PolicyEffect.ALLOW,
                arg_constraints={"path": r"^workspace/.*"},
            )
        ]
    )
    ok = eng.evaluate(
        agent="coder", tool="write_file", args={"path": "workspace/a.txt"}, call_count=0
    )
    assert ok.effect is PolicyEffect.ALLOW
    bad = eng.evaluate(agent="coder", tool="write_file", args={"path": "/etc/passwd"}, call_count=0)
    assert bad.effect is PolicyEffect.DENY


# ── rate limiting ────────────────────────────────────────────────────────────


def test_rate_limit_denies_after_n_calls() -> None:
    eng = _engine(
        [
            ToolPolicy(
                agent="coder", tool="write_file", effect=PolicyEffect.ALLOW, rate_limit_per_run=20
            )
        ]
    )
    # 20th call (call_count=19) allowed; 21st (call_count=20) denied.
    assert (
        eng.evaluate(agent="coder", tool="write_file", args={}, call_count=19).effect
        is PolicyEffect.ALLOW
    )
    denied = eng.evaluate(agent="coder", tool="write_file", args={}, call_count=20)
    assert denied.effect is PolicyEffect.DENY
    assert "rate" in denied.reason.lower()


def test_rate_limit_zero_is_unlimited() -> None:
    eng = _engine(
        [
            ToolPolicy(
                agent="coder", tool="write_file", effect=PolicyEffect.ALLOW, rate_limit_per_run=0
            )
        ]
    )
    assert (
        eng.evaluate(agent="coder", tool="write_file", args={}, call_count=9999).effect
        is PolicyEffect.ALLOW
    )


# ── load_tool_policies ───────────────────────────────────────────────────────


def test_load_tool_policies_from_yaml(tmp_path) -> None:
    yaml_text = """
version: 1
default: allow
policies:
  - agent: "*"
    tool: "delete_file"
    effect: require_hitl
    reason: "Destructive"
  - agent: "coder"
    tool: "write_file"
    effect: allow
    arg_constraints:
      path: "^workspace/.*"
    rate_limit_per_run: 25
"""
    path = tmp_path / "tool_policies.yaml"
    path.write_text(yaml_text)
    policies = load_tool_policies(str(path))
    assert len(policies) == 2
    assert policies[0].tool == "delete_file"
    assert policies[0].effect is PolicyEffect.REQUIRE_HITL
    assert policies[1].rate_limit_per_run == 25
    assert policies[1].arg_constraints == {"path": "^workspace/.*"}


def test_load_tool_policies_missing_file_returns_empty() -> None:
    assert load_tool_policies("/nonexistent/path/policies.yaml") == []


def test_load_tool_policies_bad_effect_raises(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("policies:\n  - tool: x\n    effect: explode\n")
    with pytest.raises(HardeningConfigError):
        load_tool_policies(str(path))


def test_load_tool_policies_malformed_yaml_raises(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("policies: [unclosed")
    with pytest.raises(HardeningConfigError):
        load_tool_policies(str(path))
