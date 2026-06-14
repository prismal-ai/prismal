"""Declarative tool policy engine (Phase H — SPEC-HRD-POL-001).

Capability routing and the 120-tool cap already exist, but they cannot express
"agent ``coder`` may call ``write_file`` only under the workspace, max 25x/run,
and ``delete_file`` needs a human". ``ToolPolicyEngine`` adds that least-privilege
layer as a declarative ``(agent, tool, args)`` policy — identity-agnostic, so the
richer identity-aware ``PolicyEngine`` (``agent-identity-governance``) can later
subsume it.

Evaluation is first-match / most-specific-wins; ``REQUIRE_HITL`` is surfaced to
the caller, which routes it through ``subgraphs/gates.py::hitl_gate()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from prismal.core.exceptions import HardeningConfigError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings

logger = get_logger("prismal.security.tool_policy")


class PolicyEffect(StrEnum):
    """The decision a policy yields for a tool call."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HITL = "require_hitl"


@dataclass(frozen=True)
class ToolPolicy:
    """One declarative rule over ``(agent, tool, args)``."""

    agent: str = "*"  # glob; "*" = any agent
    tool: str = "*"  # glob; "*" = any tool
    effect: PolicyEffect = PolicyEffect.ALLOW
    arg_constraints: dict[str, str] = field(default_factory=dict)  # arg -> regex
    rate_limit_per_run: int = 0  # 0 = unlimited
    reason: str = ""

    def matches(self, agent: str, tool: str) -> bool:
        """Return True if this rule's agent/tool globs match."""
        return fnmatchcase(agent, self.agent) and fnmatchcase(tool, self.tool)

    @property
    def specificity(self) -> int:
        """Higher = more specific (exact agent/tool + arg constraints win)."""
        score = 0
        if self.agent != "*":
            score += 2
        if self.tool != "*":
            score += 2
        score += len(self.arg_constraints)
        return score


@dataclass(frozen=True)
class PolicyDecision:
    """The engine's verdict for one tool call."""

    effect: PolicyEffect
    rule: str
    reason: str = ""


class ToolPolicyEngine:
    """Evaluates tool calls against a set of :class:`ToolPolicy` rules."""

    def __init__(self, policies: list[ToolPolicy], *, settings: Settings | None = None) -> None:
        """Initialize with *policies* and an optional settings snapshot."""
        self._policies = list(policies)
        self._settings = settings

    def evaluate(
        self,
        *,
        agent: str,
        tool: str,
        args: Mapping[str, object],
        call_count: int,
    ) -> PolicyDecision:
        """Return a :class:`PolicyDecision` for ``(agent, tool, args)``.

        First-match / most-specific-wins. With no matching rule the effect is the
        configured default (``settings.hardening_tool_policy_default``). Arg
        constraint violations and rate-limit overflow both yield ``DENY``.
        """
        matching = [p for p in self._policies if p.matches(agent, tool)]
        if not matching:
            return self._default_decision()

        # Most specific wins; ties resolved by declaration order (stable sort).
        chosen = max(matching, key=lambda p: p.specificity)
        rule = f"{chosen.agent}:{chosen.tool}"

        violated = self._first_violated_constraint(chosen, args)
        if violated is not None:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                rule=rule,
                reason=f"arg constraint violated: {violated}",
            )

        if chosen.rate_limit_per_run > 0 and call_count >= chosen.rate_limit_per_run:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                rule=rule,
                reason=f"rate limit exceeded ({chosen.rate_limit_per_run}/run)",
            )

        return PolicyDecision(effect=chosen.effect, rule=rule, reason=chosen.reason)

    # ── internals ──────────────────────────────────────────────────────────────

    def _default_decision(self) -> PolicyDecision:
        default = "allow"
        if self._settings is not None:
            default = getattr(self._settings, "hardening_tool_policy_default", "allow")
        effect = PolicyEffect.DENY if default == "deny" else PolicyEffect.ALLOW
        return PolicyDecision(effect=effect, rule="<default>", reason=f"default={default}")

    @staticmethod
    def _first_violated_constraint(policy: ToolPolicy, args: Mapping[str, object]) -> str | None:
        for arg_name, pattern in policy.arg_constraints.items():
            value = args.get(arg_name)
            if value is None:
                return arg_name  # required-by-constraint arg missing
            try:
                if re.search(pattern, str(value)) is None:
                    return arg_name
            except re.error:
                # A broken regex must not silently allow — treat as a violation.
                return arg_name
        return None


class RunToolPolicy:
    """Per-run policy enforcer that tracks call counts for rate limiting.

    A live engine (held in the per-run hardening registry, never in checkpointed
    state). ``check()`` evaluates one tool call, increments the per-(agent, tool)
    counter on ``ALLOW``, and hash-first audits any non-allow decision.
    """

    def __init__(self, engine: ToolPolicyEngine, *, audit: Any = None) -> None:
        """Initialize with a :class:`ToolPolicyEngine` and optional audit logger."""
        self._engine = engine
        self._audit: Any = audit
        self._counts: dict[tuple[str, str], int] = {}

    def check(self, *, agent: str, tool: str, args: Mapping[str, object]) -> PolicyDecision:
        """Evaluate one tool call and record it. Never raises."""
        key = (agent, tool)
        count = self._counts.get(key, 0)
        decision = self._engine.evaluate(agent=agent, tool=tool, args=args, call_count=count)
        if decision.effect is PolicyEffect.ALLOW:
            self._counts[key] = count + 1
        else:
            self._record(agent, tool, decision)
        return decision

    def _record(self, agent: str, tool: str, decision: PolicyDecision) -> None:
        from contextlib import suppress

        from prismal.monitoring.otel import OTelManager

        with suppress(Exception):
            OTelManager().increment_counter(
                "tool_policy_denied", attributes={"agent": agent, "tool": tool}
            )
        logger.warning(
            "tool_policy_decision",
            agent=agent,
            tool=tool,
            effect=decision.effect.value,
            rule=decision.rule,
            reason=decision.reason,
        )
        if self._audit is None:
            return
        with suppress(Exception):
            self._audit.log_event(
                "tool_policy",
                {
                    "agent": agent,
                    "tool": tool,
                    "effect": decision.effect.value,
                    "rule": decision.rule,
                    "reason": decision.reason,
                },
            )


def load_tool_policies(path: str | None = None) -> list[ToolPolicy]:
    """Load + validate the tool-policy YAML (see ``tool_policies.example.yaml``).

    A missing file yields an empty list (no policies → default effect governs).
    A malformed file or an invalid effect raises :class:`HardeningConfigError`.
    """
    resolved = Path(path) if path else Path("config/tool_policies.yaml")
    if not resolved.exists():
        logger.info("tool_policy.no_file", path=str(resolved))
        return []

    try:
        with resolved.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise HardeningConfigError(f"tool policy YAML is malformed: {exc}") from exc

    raw_policies = data.get("policies") or []
    if not isinstance(raw_policies, list):
        raise HardeningConfigError("tool policy 'policies' must be a list")

    policies: list[ToolPolicy] = []
    for i, raw in enumerate(raw_policies):
        if not isinstance(raw, dict):
            raise HardeningConfigError(f"policy #{i} must be a mapping")
        effect_raw = str(raw.get("effect", "allow"))
        try:
            effect = PolicyEffect(effect_raw)
        except ValueError as exc:
            raise HardeningConfigError(
                f"policy #{i} has invalid effect {effect_raw!r}; "
                f"expected one of {[e.value for e in PolicyEffect]}"
            ) from exc
        constraints = raw.get("arg_constraints") or {}
        if not isinstance(constraints, dict):
            raise HardeningConfigError(f"policy #{i} arg_constraints must be a mapping")
        policies.append(
            ToolPolicy(
                agent=str(raw.get("agent", "*")),
                tool=str(raw.get("tool", "*")),
                effect=effect,
                arg_constraints={str(k): str(v) for k, v in constraints.items()},
                rate_limit_per_run=int(raw.get("rate_limit_per_run", 0)),
                reason=str(raw.get("reason", "")),
            )
        )
    return policies


__all__ = [
    "PolicyDecision",
    "PolicyEffect",
    "RunToolPolicy",
    "ToolPolicy",
    "ToolPolicyEngine",
    "load_tool_policies",
]
