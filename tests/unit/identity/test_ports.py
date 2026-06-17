"""Tests for the identity hexagonal ports (Phase IDN — SPEC-IDN-PRV/VLT/POL).

The core depends on ``IdentityPort`` / ``CredentialVaultPort`` / ``PolicyPort``
Protocols; concrete providers conform structurally. They are re-exported from
``prismal.agents.extension`` alongside the other ports (Y/Z).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import SecretStr

from prismal.agents.extension import (
    CredentialVaultPort,
    IdentityPort,
    PolicyPort,
)
from prismal.identity.types import (
    DID,
    AgentIdentity,
    Credential,
    PolicyDecision,
    PolicyEffect,
    Scope,
)


class _Identity:
    def issue(
        self, *, agent_name: str, org_id: str | None = None, scopes: Sequence[Scope] = ()
    ) -> AgentIdentity:
        return AgentIdentity(did=f"did:key:{agent_name}", agent_name=agent_name)

    def resolve(self, did: DID) -> AgentIdentity | None:
        return None

    def verify(self, did: DID) -> bool:
        return True

    def revoke(self, did: DID) -> None:
        return None


class _Vault:
    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential:
        return Credential(ref=credential_ref, value=SecretStr("x"))

    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None:
        return None


class _Policy:
    def allow(
        self,
        *,
        identity: AgentIdentity,
        action: str,
        resource: str,
        context: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(effect=PolicyEffect.ALLOW, rule="*")


def test_identity_port_is_satisfied_structurally() -> None:
    assert isinstance(_Identity(), IdentityPort)


def test_credential_vault_port_is_satisfied_structurally() -> None:
    assert isinstance(_Vault(), CredentialVaultPort)


def test_policy_port_is_satisfied_structurally() -> None:
    assert isinstance(_Policy(), PolicyPort)


def test_non_conforming_object_is_not_an_identity_port() -> None:
    assert not isinstance(object(), IdentityPort)
