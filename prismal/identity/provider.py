"""Identity providers (SPEC-IDN-PRV-001).

* :class:`LocalIdentityProvider` — zero-config default: generates an Ed25519
  keypair per agent, issues a self-contained ``did:key``, and keeps an
  in-process registry (issued identities + revocations). It holds the private
  keys so an agent can sign (durable persistence via ``PermissionManager`` is
  wired in a later phase).
* :class:`OidcIdentityProvider` — maps a corporate OIDC subject (Entra/Okta)
  onto a prismal :class:`AgentIdentity`. The subject is produced by an injected
  ``subject_resolver`` seam (a production host wires an async token exchange
  here, keeping any IdP SDK isolated to this module); the default resolver
  derives a deterministic subject from the issuer with no network call.
* :class:`FakeIdentityProvider` — deterministic, I/O-free test double.

All providers are sync (the :class:`IdentityPort`); remote DID *signature*
verification (``did.verify_did``) is async and used separately for A2A.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from prismal.identity.did import issue_did_key
from prismal.identity.types import AgentIdentity, Scope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from prismal.core.config import Settings
    from prismal.identity.types import DID


class LocalIdentityProvider:
    """did:key issuance backed by an in-process registry. Zero-config default."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._identities: dict[DID, AgentIdentity] = {}
        self._keys: dict[DID, Ed25519PrivateKey] = {}
        self._revoked: set[DID] = set()

    def issue(
        self,
        *,
        agent_name: str,
        org_id: str | None = None,
        scopes: Sequence[Scope] = (),
        credential_ref: str | None = None,
    ) -> AgentIdentity:
        """Generate an Ed25519 keypair and issue a self-contained did:key identity."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        did = issue_did_key(public_key)
        identity = AgentIdentity(
            did=did,
            agent_name=agent_name,
            org_id=org_id,
            scopes=tuple(scopes),
            credential_ref=credential_ref,
        )
        self._identities[did] = identity
        self._keys[did] = private_key
        return identity

    def resolve(self, did: DID) -> AgentIdentity | None:
        """Return the identity for ``did`` (even if revoked) or ``None`` if unknown."""
        return self._identities.get(did)

    def verify(self, did: DID) -> bool:
        """True if ``did`` was issued here and has not been revoked."""
        return did in self._identities and did not in self._revoked

    def revoke(self, did: DID) -> None:
        """Mark ``did`` revoked so subsequent :meth:`verify` calls fail."""
        self._revoked.add(did)

    def sign(self, did: DID, payload: bytes) -> bytes:
        """Sign ``payload`` with the private key the provider holds for ``did``."""
        private_key = self._keys.get(did)
        if private_key is None:
            raise KeyError(f"No signing key for {did}")
        return private_key.sign(payload)


class OidcIdentityProvider(LocalIdentityProvider):
    """Adapter that links a corporate OIDC subject to a prismal identity."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        subject_resolver: Callable[[str, str | None], str] | None = None,
    ) -> None:
        super().__init__(settings)
        self._issuer = settings.oidc_issuer if settings is not None else ""
        self._resolver = subject_resolver or self._default_resolver

    def _default_resolver(self, agent_name: str, org_id: str | None) -> str:
        """Derive a deterministic OIDC subject from the issuer (no network).

        Production hosts inject an async token-exchange resolver instead; the IdP
        SDK stays isolated to this module (Rule #4).
        """
        return f"{self._issuer}#{org_id or 'default'}:{agent_name}"

    def issue(
        self,
        *,
        agent_name: str,
        org_id: str | None = None,
        scopes: Sequence[Scope] = (),
        credential_ref: str | None = None,
    ) -> AgentIdentity:
        """Issue a did:key identity whose ``credential_ref`` is the OIDC subject."""
        subject = credential_ref or self._resolver(agent_name, org_id)
        return super().issue(
            agent_name=agent_name, org_id=org_id, scopes=scopes, credential_ref=subject
        )


class FakeIdentityProvider:
    """Deterministic, I/O-free test double — no crypto, stable DIDs."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._identities: dict[DID, AgentIdentity] = {}
        self._revoked: set[DID] = set()

    def issue(
        self,
        *,
        agent_name: str,
        org_id: str | None = None,
        scopes: Sequence[Scope] = (),
    ) -> AgentIdentity:
        """Issue a deterministic ``did:fake:<org>:<agent>`` identity."""
        did = f"did:fake:{org_id or 'default'}:{agent_name}"
        identity = AgentIdentity(
            did=did, agent_name=agent_name, org_id=org_id, scopes=tuple(scopes)
        )
        self._identities[did] = identity
        return identity

    def resolve(self, did: DID) -> AgentIdentity | None:
        return self._identities.get(did)

    def verify(self, did: DID) -> bool:
        return did in self._identities and did not in self._revoked

    def revoke(self, did: DID) -> None:
        self._revoked.add(did)


__all__ = [
    "FakeIdentityProvider",
    "LocalIdentityProvider",
    "OidcIdentityProvider",
]
