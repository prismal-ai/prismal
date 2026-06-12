"""Custom config sources: a Vault-style source + per-tenant settings (Fase W).

Two host patterns:

1. **Secrets manager** — a plain class conforming ``ConfigSourcePort`` (only a
   sync ``load()`` returning a mapping) supplies secrets from a vault, chained
   ahead of :class:`EnvConfigSource` so vault values win over file/env defaults.
   Inject it globally with :func:`set_config_source`.

2. **Per-tenant (context)** — build isolated ``Settings`` from a
   :class:`MappingConfigSource` (a tenant row) via :func:`build_settings`, with
   no global state, so parallel tenants never share configuration.

Run::

    python examples/config_source_custom.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Mapping

from prismal.core.config import build_settings
from prismal.core.config_source import (
    ChainedConfigSource,
    ConfigValue,
    EnvConfigSource,
    MappingConfigSource,
    set_config_source,
)


class VaultConfigSource:
    """Toy secrets-manager source — replace ``_read`` with a real vault client.

    Conforms ``ConfigSourcePort`` structurally: a sync ``load()`` that never
    raises and returns a mapping. Secrets are wrapped in ``SecretStr`` so they
    never leak into logs or ``repr``.
    """

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def load(self) -> Mapping[str, ConfigValue]:
        return {key: SecretStr(value) for key, value in self._secrets.items()}


def secrets_manager_startup() -> None:
    """Vault secrets override env/file defaults (vault first in the chain)."""
    set_config_source(
        ChainedConfigSource(
            [
                VaultConfigSource({"PRISMAL_ANTHROPIC_API_KEY": "sk-from-vault"}),
                EnvConfigSource(dotenv_path=".env"),
            ]
        )
    )


def tenant_settings(org_id: str):
    """Build isolated Settings for one tenant from its config row (no globals)."""
    tenant_row = {
        "acme": {"PRISMAL_DEFAULT_MODEL": "claude-opus-4-8"},
        "globex": {"PRISMAL_DEFAULT_MODEL": "gpt-4o"},
    }[org_id]
    return build_settings(MappingConfigSource(tenant_row))


def main() -> None:
    secrets_manager_startup()
    from prismal.core.config import get_settings

    key = get_settings().anthropic_api_key.get_secret_value()
    print("anthropic key (from vault):", key)

    for org in ("acme", "globex"):
        print(f"{org} default_model:", tenant_settings(org).default_model)


if __name__ == "__main__":
    main()
