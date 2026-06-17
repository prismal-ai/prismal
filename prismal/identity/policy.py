"""Identity-aware policy engine (SPEC-IDN-POL-001).

``PolicyEngine.allow(identity, action, resource, context)`` is the richer
successor of the Phase H identity-agnostic ``ToolPolicyEngine``. When a tool
policy is wired, tool-call decisions are **delegated** to it first (no rule
duplication); the engine then layers identity scopes + identity rules on top.

Evaluation:
  1. If a ``ToolPolicyEngine`` is wired and ``action == "tool_call"``, delegate
     the ``(agent, tool, args)`` decision. A delegated ``DENY`` short-circuits;
     a delegated ``REQUIRE_HITL`` raises the floor to HITL.
  2. Pick the most-specific matching :class:`IdentityPolicy` (identity/action/
     resource globs; ties → declaration order).
  3. ``DENY`` denies; ``REQUIRE_HITL`` (or a delegated HITL) routes to the HITL
     gate; ``ALLOW`` additionally enforces ``require_scope`` against
     ``identity.scopes`` (least privilege) — a missing scope denies.

With no matching policy the verdict is ``DENY`` (least privilege). Evaluation is
sync, O(number-of-policies), and never raises on the hot path.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from prismal.core.exceptions import IdentityConfigError
from prismal.identity.types import PolicyDecision, PolicyEffect

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings
    from prismal.identity.types import AgentIdentity
    from prismal.security.tool_policy import ToolPolicyEngine

_DEFAULT_POLICY_PATH = "config/identity_policies.yaml"


@dataclass(frozen=True)
class IdentityPolicy:
    """One declarative rule over ``(identity, action, resource)``."""

    identity: str = "*"  # glob over the DID or the agent_name
    action: str = "*"  # "tool_call" | "file_write" | "a2a_delegate" | "*"
    resource: str = "*"  # glob: "tools:write_file", "rag:*", "https://api.example/*"
    effect: PolicyEffect = PolicyEffect.ALLOW
    require_scope: str | None = None  # scope the identity must hold for an ALLOW

    def matches(self, *, agent_name: str, did: str, action: str, resource: str) -> bool:
        """Return True if this rule's identity/action/resource globs all match."""
        identity_match = fnmatchcase(agent_name, self.identity) or fnmatchcase(did, self.identity)
        return (
            identity_match
            and fnmatchcase(action, self.action)
            and fnmatchcase(resource, self.resource)
        )

    @property
    def specificity(self) -> tuple[int, int, int]:
        """Higher tuple = more specific (exact identity/action + longer literal resource)."""
        return (
            0 if self.identity == "*" else 1,
            0 if self.action == "*" else 1,
            len(self.resource.replace("*", "")),
        )


class PolicyEngine:
    """Identity-aware authorization; delegates tool-call rules to Phase H."""

    def __init__(
        self,
        policies: list[IdentityPolicy],
        *,
        tool_policy: ToolPolicyEngine | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialize with identity *policies* and an optional Phase H delegate."""
        self._policies = list(policies)
        self._tool_policy = tool_policy
        self._settings = settings

    def allow(
        self,
        *,
        identity: AgentIdentity,
        action: str,
        resource: str,
        context: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        """Decide whether ``identity`` may perform ``action`` on ``resource``."""
        delegated_hitl = False
        if self._tool_policy is not None and action == "tool_call":
            tool = resource.split(":", 1)[1] if ":" in resource else resource
            args = dict(context.get("args", {})) if context else {}
            decision = self._tool_policy.evaluate(
                agent=identity.agent_name, tool=tool, args=args, call_count=0
            )
            if decision.effect.value == PolicyEffect.DENY.value:
                return PolicyDecision(
                    effect=PolicyEffect.DENY,
                    rule=f"tool_policy:{decision.rule}",
                    reason=decision.reason,
                )
            delegated_hitl = decision.effect.value == PolicyEffect.REQUIRE_HITL.value

        matching = [
            p
            for p in self._policies
            if p.matches(
                agent_name=identity.agent_name, did=identity.did, action=action, resource=resource
            )
        ]
        if not matching:
            effect = PolicyEffect.REQUIRE_HITL if delegated_hitl else PolicyEffect.DENY
            return PolicyDecision(effect=effect, rule="<no-match>", reason="no matching policy")

        chosen = max(matching, key=lambda p: p.specificity)
        rule = f"{chosen.identity}:{chosen.action}:{chosen.resource}"

        if chosen.effect is PolicyEffect.DENY:
            return PolicyDecision(effect=PolicyEffect.DENY, rule=rule, reason="denied by policy")

        if chosen.effect is PolicyEffect.REQUIRE_HITL or delegated_hitl:
            return PolicyDecision(effect=PolicyEffect.REQUIRE_HITL, rule=rule)

        if chosen.require_scope is not None and not any(
            s.matches(action, chosen.require_scope) for s in identity.scopes
        ):
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                rule=rule,
                reason=f"identity lacks required scope {chosen.require_scope!r}",
            )
        return PolicyDecision(effect=PolicyEffect.ALLOW, rule=rule)


def load_identity_policies(path: str | None = None) -> list[IdentityPolicy]:
    """Load + validate ``config/identity_policies.yaml`` (see the shipped example).

    The top-level ``default`` (``allow``/``deny``) becomes a lowest-specificity
    catch-all appended last, so any explicit rule wins. A missing file yields an
    empty list (the engine then denies by least privilege). A malformed file or
    an invalid effect raises :class:`IdentityConfigError`.
    """
    resolved = Path(path) if path else Path(_DEFAULT_POLICY_PATH)
    if not resolved.exists():
        return []

    try:
        with resolved.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise IdentityConfigError(f"identity policy YAML is malformed: {exc}") from exc

    raw_policies = data.get("policies") or []
    if not isinstance(raw_policies, list):
        raise IdentityConfigError("identity policy 'policies' must be a list")

    policies: list[IdentityPolicy] = []
    for i, raw in enumerate(raw_policies):
        if not isinstance(raw, dict):
            raise IdentityConfigError(f"policy #{i} must be a mapping")
        effect_raw = str(raw.get("effect", "allow"))
        try:
            effect = PolicyEffect(effect_raw)
        except ValueError as exc:
            raise IdentityConfigError(
                f"policy #{i} has invalid effect {effect_raw!r}; "
                f"expected one of {[e.value for e in PolicyEffect]}"
            ) from exc
        require_scope = raw.get("require_scope")
        policies.append(
            IdentityPolicy(
                identity=str(raw.get("identity", "*")),
                action=str(raw.get("action", "*")),
                resource=str(raw.get("resource", "*")),
                effect=effect,
                require_scope=str(require_scope) if require_scope is not None else None,
            )
        )

    default_raw = str(data.get("default", "deny"))
    try:
        default_effect = PolicyEffect(default_raw)
    except ValueError as exc:
        raise IdentityConfigError(
            f"invalid default effect {default_raw!r}; "
            f"expected one of {[e.value for e in PolicyEffect]}"
        ) from exc
    policies.append(IdentityPolicy("*", "*", "*", default_effect))
    return policies


__all__ = [
    "IdentityPolicy",
    "PolicyEngine",
    "load_identity_policies",
]
