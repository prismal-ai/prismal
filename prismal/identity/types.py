"""Immutable value objects for agent identity & access governance (SPEC-IDN-TYP-001).

A :class:`Scope` is a least-privilege grant (``"tools:write_file"``, ``"rag:*"``).
An :class:`AgentIdentity` binds a runtime agent to a verifiable DID, a tenant, and
its scope ceiling — but never to a secret (only an opaque ``credential_ref``
travels). A :class:`Credential` is resolved at the action boundary only and keeps
its secret inside a ``SecretStr``. An :class:`OnBehalfToken` carries a user's
scoped delegation along the chain. A :class:`PolicyDecision` is the verdict of a
``PolicyEngine`` evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import SecretStr

# A Decentralised Identifier, e.g. "did:key:z6Mk…" or "did:web:org.example:agents:coder".
DID = str


@dataclass(frozen=True)
class Scope:
    """A least-privilege grant, e.g. ``'tools:write_file'`` or ``'rag:read'``.

    ``resource`` may be a glob (``'rag:*'``); :meth:`matches` tests whether this
    scope covers a requested ``(action, resource)``. Coverage is decided by the
    resource pattern — ``action`` is part of the signature for richer future
    rules but a resource-shaped scope ignores it.
    """

    resource: str

    def matches(self, action: str, resource: str) -> bool:  # noqa: ARG002 — `action` reserved for richer future rules (SPEC-IDN-TYP-001 signature)
        """True when this scope's pattern covers ``resource``."""
        return fnmatchcase(resource, self.resource)


@dataclass(frozen=True)
class AgentIdentity:
    """A verifiable per-agent/tenant identity. Never carries the secret itself."""

    did: DID
    agent_name: str
    org_id: str | None = None
    scopes: tuple[Scope, ...] = ()
    credential_ref: str | None = None  # opaque handle; NEVER the secret
    expires_at: float | None = None  # epoch seconds; None = no TTL


@dataclass(frozen=True)
class Credential:
    """A secret resolved at the action boundary only; never serialized into state."""

    ref: str
    value: SecretStr
    scopes: tuple[Scope, ...] = ()


@dataclass(frozen=True)
class OnBehalfToken:
    """A user's scoped, short-TTL delegation propagated along the agent chain."""

    token_id: str
    subject: str  # the user the agent acts for
    scopes: tuple[Scope, ...]
    expires_at: float
    chain: tuple[DID, ...] = ()  # delegation path (audit)


class PolicyEffect(StrEnum):
    """The effect a policy rule produces for an authorization decision."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HITL = "require_hitl"


@dataclass(frozen=True)
class PolicyDecision:
    """The verdict of a :meth:`PolicyEngine.allow` evaluation."""

    effect: PolicyEffect
    rule: str
    reason: str = ""


__all__ = [
    "DID",
    "AgentIdentity",
    "Credential",
    "OnBehalfToken",
    "PolicyDecision",
    "PolicyEffect",
    "Scope",
]
