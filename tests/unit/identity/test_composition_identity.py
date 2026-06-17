"""Identity composition in the runtime root (Phase IDN — ID6-04 / SPEC-IDN-CMP-001).

``build_identity_ports`` maps settings → (IdentityProvider, CredentialVault,
PolicyEngine); ``build_runtime`` attaches them to the RuntimeContext only when
``identity_enabled``. The fields default to ``None`` so existing contexts are
unaffected.
"""

from __future__ import annotations

from prismal.agents.extension import CredentialVaultPort, IdentityPort, PolicyPort
from prismal.composition.runtime import build_identity_ports, build_test_runtime
from prismal.core.config import Settings
from prismal.identity.policy import PolicyEngine
from prismal.identity.provider import LocalIdentityProvider, OidcIdentityProvider
from prismal.identity.vault import EnvVault, FileVault


def test_build_identity_ports_local_env_default() -> None:
    provider, vault, policy = build_identity_ports(Settings(identity_enabled=True))
    assert isinstance(provider, LocalIdentityProvider)
    assert isinstance(vault, EnvVault)
    assert isinstance(policy, PolicyEngine)
    assert isinstance(provider, IdentityPort)
    assert isinstance(vault, CredentialVaultPort)
    assert isinstance(policy, PolicyPort)


def test_build_identity_ports_oidc_and_file() -> None:
    settings = Settings(
        identity_enabled=True,
        identity_provider="oidc",
        oidc_issuer="https://login.example.com",
        identity_vault="file",
    )
    provider, vault, _ = build_identity_ports(settings)
    assert isinstance(provider, OidcIdentityProvider)
    assert isinstance(vault, FileVault)


def test_build_identity_ports_wires_phase_h_delegate_when_hardening_on() -> None:
    settings = Settings(identity_enabled=True, hardening_enabled=True)
    _, _, policy = build_identity_ports(settings)
    assert policy._tool_policy is not None  # Phase H ToolPolicyEngine delegate wired


def test_build_identity_ports_no_delegate_when_hardening_off() -> None:
    _, _, policy = build_identity_ports(Settings(identity_enabled=True))
    assert policy._tool_policy is None


def test_runtime_context_identity_fields_default_none() -> None:
    ctx = build_test_runtime()
    assert ctx.identity_provider is None
    assert ctx.credential_vault is None
    assert ctx.policy_engine is None
