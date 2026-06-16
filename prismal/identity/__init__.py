"""Agent Identity & Access Governance (Phase IDN).

Each agent/tenant gets a verifiable :class:`AgentIdentity` (W3C DID), scoped
credentials from a pluggable vault, an optional OAuth-on-behalf token threaded
along the delegation chain, and an identity-aware ``PolicyEngine`` evaluated
pre-action through the existing ``ActionInterceptor`` seam. Opt-in via
``settings.identity_enabled`` (default ``False``): with the flag off, behaviour
is byte-for-byte unchanged.

See ``docs/identity.md`` and ``specs/agent-identity-governance/``.
"""

from __future__ import annotations

from prismal.identity.types import (
    DID,
    AgentIdentity,
    Credential,
    OnBehalfToken,
    PolicyDecision,
    PolicyEffect,
    Scope,
)

__all__ = [
    "DID",
    "AgentIdentity",
    "Credential",
    "OnBehalfToken",
    "PolicyDecision",
    "PolicyEffect",
    "Scope",
]
