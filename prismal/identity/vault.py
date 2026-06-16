"""Credential vault (SPEC-IDN-VLT-001).

A vault resolves an opaque ``credential_ref`` to a :class:`Credential` whose
secret lives inside a ``SecretStr`` — so it cannot leak into state, logs, or
audit (only the ref travels). Resolution happens at the action boundary.

* :class:`EnvVault` — resolves through the injected ``ConfigSourcePort`` (Phase
  W); it never reads ``os.environ`` directly. Env-sourced secrets are
  host-trusted (unscoped); an in-memory overlay can add scoped entries.
* :class:`FileVault` — encrypted at rest (Fernet); the key is persisted in a
  sidecar file the host protects with filesystem permissions.
* :class:`FakeVault` — deterministic, in-memory test double.

Scope enforcement: ``resolve(ref, scopes=requested)`` returns the credential
only when every requested :class:`Scope` is covered by the entry's granted
scopes; otherwise it raises :class:`ScopeError`. With no requested scopes the
check is skipped (the common boundary case).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cryptography.fernet import Fernet
from pydantic import SecretStr

from prismal.core.config_source import EnvConfigSource, get_config_source
from prismal.core.exceptions import CredentialResolutionError, ScopeError
from prismal.identity.types import Credential, Scope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.core.config import Settings
    from prismal.core.config_source import ConfigSourcePort

# A vault entry pairs the secret with the scopes it may be used for.
_Entry = tuple[SecretStr, tuple[Scope, ...]]
_WILDCARD: tuple[Scope, ...] = (Scope("*"),)


def _check_scope_coverage(granted: tuple[Scope, ...], requested: Sequence[Scope]) -> None:
    """Raise :class:`ScopeError` if any requested scope is not covered by ``granted``."""
    for want in requested:
        if not any(g.matches("", want.resource) for g in granted):
            raise ScopeError(
                f"Credential not granted for scope {want.resource!r} "
                f"(granted: {[g.resource for g in granted]})"
            )


class FakeVault:
    """Deterministic, in-memory test double."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._entries: dict[str, _Entry] = {}

    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None:
        self._entries[ref] = (value, tuple(scopes))

    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential:
        entry = self._entries.get(credential_ref)
        if entry is None:
            raise CredentialResolutionError(f"Unknown credential ref: {credential_ref!r}")
        value, granted = entry
        _check_scope_coverage(granted, scopes)
        return Credential(ref=credential_ref, value=value, scopes=granted)


class EnvVault:
    """Resolves secrets through the injected ``ConfigSourcePort`` (never os.environ)."""

    def __init__(
        self, settings: Settings | None = None, *, source: ConfigSourcePort | None = None
    ) -> None:
        self._settings = settings
        self._source = source
        self._overlay: dict[str, _Entry] = {}

    def _config_source(self) -> ConfigSourcePort:
        return self._source or get_config_source() or EnvConfigSource()

    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None:
        """Add a scoped in-memory entry (env config remains read-only)."""
        self._overlay[ref] = (value, tuple(scopes))

    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential:
        overlay = self._overlay.get(credential_ref)
        if overlay is not None:
            value, granted = overlay
            _check_scope_coverage(granted, scopes)
            return Credential(ref=credential_ref, value=value, scopes=granted)

        raw = self._config_source().load().get(credential_ref)
        if raw is None:
            raise CredentialResolutionError(f"Unknown credential ref: {credential_ref!r}")
        secret = raw if isinstance(raw, SecretStr) else SecretStr(raw)
        # Env-sourced secrets are host-trusted ⇒ granted the wildcard scope.
        _check_scope_coverage(_WILDCARD, scopes)
        return Credential(ref=credential_ref, value=secret, scopes=_WILDCARD)


class FileVault:
    """Encrypted-at-rest credential store (Fernet)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        path: str | Path | None = None,
        key: bytes | None = None,
    ) -> None:
        self._settings = settings
        self._path = Path(path) if path is not None else Path("identity_vault.enc")
        self._fernet = Fernet(key or self._load_or_create_key())

    @property
    def _key_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".key")

    @staticmethod
    def _write_owner_only(path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` with owner-only (0600) permissions, atomically.

        Uses ``os.open`` with the mode set at creation so there is no window where
        the secret file is world/group-readable (unlike write-then-chmod).
        """
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = Fernet.generate_key()
        self._write_owner_only(self._key_path, key)
        return key

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {}
        decrypted = self._fernet.decrypt(self._path.read_bytes())
        data: dict[str, dict[str, object]] = json.loads(decrypted)
        return data

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        self._write_owner_only(self._path, self._fernet.encrypt(json.dumps(data).encode()))

    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None:
        data = self._read()
        data[ref] = {
            "value": value.get_secret_value(),
            "scopes": [s.resource for s in scopes],
        }
        self._write(data)

    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential:
        entry = self._read().get(credential_ref)
        if entry is None:
            raise CredentialResolutionError(f"Unknown credential ref: {credential_ref!r}")
        granted = tuple(Scope(r) for r in cast("list[str]", entry["scopes"]))
        _check_scope_coverage(granted, scopes)
        return Credential(
            ref=credential_ref,
            value=SecretStr(cast("str", entry["value"])),
            scopes=granted,
        )


__all__ = [
    "EnvVault",
    "FakeVault",
    "FileVault",
]
