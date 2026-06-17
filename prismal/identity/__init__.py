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

from prismal.identity.delegation import (
    get_on_behalf,
    mint_on_behalf,
    propagate,
    put_on_behalf,
    revoke,
    validate,
)
from prismal.identity.did import (
    did_document,
    issue_did_key,
    issue_did_web,
    resolve_did,
    verify_did,
)
from prismal.identity.policy import (
    IdentityPolicy,
    PolicyEngine,
    load_identity_policies,
)
from prismal.identity.provider import (
    FakeIdentityProvider,
    LocalIdentityProvider,
    OidcIdentityProvider,
)
from prismal.identity.types import (
    DID,
    AgentIdentity,
    Credential,
    OnBehalfToken,
    PolicyDecision,
    PolicyEffect,
    Scope,
)
from prismal.identity.vault import EnvVault, FakeVault, FileVault

__all__ = [
    "DID",
    "AgentIdentity",
    "Credential",
    "EnvVault",
    "FakeIdentityProvider",
    "FakeVault",
    "FileVault",
    "IdentityPolicy",
    "LocalIdentityProvider",
    "OidcIdentityProvider",
    "OnBehalfToken",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEngine",
    "Scope",
    "did_document",
    "get_on_behalf",
    "issue_did_key",
    "issue_did_web",
    "load_identity_policies",
    "mint_on_behalf",
    "propagate",
    "put_on_behalf",
    "resolve_did",
    "revoke",
    "validate",
    "verify_did",
]
