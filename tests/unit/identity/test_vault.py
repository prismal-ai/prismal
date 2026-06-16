"""Tests for the credential vault (Phase IDN — SPEC-IDN-VLT-001).

ID3 "done when": ``resolve()`` returns a ``SecretStr``; the secret never appears
in ``repr``/text (so it cannot leak to logs/state/audit); an out-of-scope
resolution raises ``ScopeError``; ``EnvVault`` reads through the injected
``ConfigSourcePort`` (never ``os.environ``); ``FileVault`` is encrypted at rest.
"""

from __future__ import annotations

import os
import stat

import pytest
from pydantic import SecretStr

from prismal.agents.extension import CredentialVaultPort
from prismal.core.config_source import MappingConfigSource
from prismal.core.exceptions import CredentialResolutionError, ScopeError
from prismal.identity.types import Scope
from prismal.identity.vault import EnvVault, FakeVault, FileVault

# ── FakeVault ─────────────────────────────────────────────────────────────────


def test_fake_vault_conforms_to_port() -> None:
    assert isinstance(FakeVault(), CredentialVaultPort)


def test_fake_store_then_resolve_returns_secret() -> None:
    vault = FakeVault()
    vault.store("api-key", SecretStr("s3cr3t"))
    cred = vault.resolve("api-key")
    assert cred.ref == "api-key"
    assert cred.value.get_secret_value() == "s3cr3t"


def test_fake_resolve_unknown_ref_raises() -> None:
    with pytest.raises(CredentialResolutionError):
        FakeVault().resolve("nope")


def test_fake_resolve_within_scope_succeeds() -> None:
    vault = FakeVault()
    vault.store("api-key", SecretStr("s3cr3t"), scopes=(Scope("rag:*"),))
    cred = vault.resolve("api-key", scopes=(Scope("rag:read"),))
    assert cred.value.get_secret_value() == "s3cr3t"


def test_fake_resolve_out_of_scope_raises() -> None:
    vault = FakeVault()
    vault.store("api-key", SecretStr("s3cr3t"), scopes=(Scope("rag:read"),))
    with pytest.raises(ScopeError):
        vault.resolve("api-key", scopes=(Scope("tools:write_file"),))


def test_fake_resolve_without_requested_scopes_skips_check() -> None:
    """No requested scopes ⇒ no scope enforcement (the common boundary case)."""
    vault = FakeVault()
    vault.store("api-key", SecretStr("s3cr3t"), scopes=(Scope("rag:read"),))
    assert vault.resolve("api-key").value.get_secret_value() == "s3cr3t"


# ── Redaction (secret never leaks to text/logs) ───────────────────────────────


def test_resolved_secret_is_masked_in_text() -> None:
    vault = FakeVault()
    vault.store("api-key", SecretStr("s3cr3t"))
    cred = vault.resolve("api-key")
    assert "s3cr3t" not in repr(cred)
    assert "s3cr3t" not in str(cred.value)
    assert "s3cr3t" not in f"the credential is {cred.value}"
    assert cred.value.get_secret_value() == "s3cr3t"


# ── EnvVault (reads through ConfigSourcePort) ─────────────────────────────────


def test_env_vault_conforms_to_port() -> None:
    assert isinstance(EnvVault(), CredentialVaultPort)


def test_env_vault_resolves_from_injected_source() -> None:
    source = MappingConfigSource({"OPENAI_API_KEY": "sk-test"})
    cred = EnvVault(source=source).resolve("OPENAI_API_KEY")
    assert cred.value.get_secret_value() == "sk-test"


def test_env_vault_falls_back_to_global_source() -> None:
    """With no explicit source, EnvVault resolves via the global ConfigSourcePort."""
    with pytest.raises(CredentialResolutionError):
        EnvVault().resolve("DEFINITELY_NOT_SET_XYZ_123")


def test_env_vault_missing_ref_raises() -> None:
    cred_source = MappingConfigSource({"OTHER": "x"})
    with pytest.raises(CredentialResolutionError):
        EnvVault(source=cred_source).resolve("OPENAI_API_KEY")


def test_env_vault_store_overlay_is_scope_enforced() -> None:
    """A stored overlay entry carries scopes and is enforced; env entries don't."""
    vault = EnvVault(source=MappingConfigSource({"ENV_KEY": "env-val"}))
    vault.store("scoped", SecretStr("v"), scopes=(Scope("rag:read"),))
    # env-sourced entry is host-trusted ⇒ any scope is allowed
    assert vault.resolve("ENV_KEY", scopes=(Scope("tools:write_file"),)).value
    # overlay entry resolves within its granted scope
    assert vault.resolve("scoped", scopes=(Scope("rag:read"),)).value.get_secret_value() == "v"
    # overlay entry enforces its scopes
    with pytest.raises(ScopeError):
        vault.resolve("scoped", scopes=(Scope("tools:write_file"),))


# ── FileVault (encrypted at rest) ─────────────────────────────────────────────


def test_file_vault_conforms_to_port(tmp_path) -> None:
    assert isinstance(FileVault(path=tmp_path / "v.enc"), CredentialVaultPort)


def test_file_vault_round_trips_across_instances(tmp_path) -> None:
    path = tmp_path / "vault.enc"
    FileVault(path=path).store("api-key", SecretStr("s3cr3t"), scopes=(Scope("rag:read"),))
    cred = FileVault(path=path).resolve("api-key", scopes=(Scope("rag:read"),))
    assert cred.value.get_secret_value() == "s3cr3t"


def test_file_vault_encrypts_at_rest(tmp_path) -> None:
    path = tmp_path / "vault.enc"
    FileVault(path=path).store("api-key", SecretStr("s3cr3t"))
    assert b"s3cr3t" not in path.read_bytes()


def test_file_vault_out_of_scope_raises(tmp_path) -> None:
    path = tmp_path / "vault.enc"
    FileVault(path=path).store("api-key", SecretStr("s3cr3t"), scopes=(Scope("rag:read"),))
    with pytest.raises(ScopeError):
        FileVault(path=path).resolve("api-key", scopes=(Scope("tools:write_file"),))


def test_file_vault_unknown_ref_raises(tmp_path) -> None:
    with pytest.raises(CredentialResolutionError):
        FileVault(path=tmp_path / "vault.enc").resolve("missing")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_file_vault_writes_are_owner_only(tmp_path) -> None:
    """The key file and the encrypted vault must be 0600 (no world/group read)."""
    path = tmp_path / "sub" / "vault.enc"
    FileVault(path=path).store("api-key", SecretStr("s3cr3t"))
    key_path = path.with_suffix(path.suffix + ".key")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    # the parent directory the vault created is owner-only too
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
