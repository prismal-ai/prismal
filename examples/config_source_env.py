"""Inject the default environment config source (Fase W).

The standard single-tenant host path: build an :class:`EnvConfigSource` (env +
``.env`` + the legacy ``LIGHTAGENT_`` mirror) and inject it once at startup with
:func:`set_config_source`. After that, every ``get_settings()`` call across the
core resolves through the injected source — the core never reads ``.env`` itself.

This reproduces today's behaviour byte-for-byte; doing nothing at all (no
injection) falls back to the very same :class:`EnvConfigSource`, so existing
deployments are unaffected.

Run::

    PRISMAL_DEFAULT_MODEL=claude-opus-4-8 python examples/config_source_env.py
"""

from __future__ import annotations

from prismal.core.config import get_settings
from prismal.core.config_source import EnvConfigSource, set_config_source


def on_startup() -> None:
    """Own configuration loading: inject the env source once at startup."""
    set_config_source(EnvConfigSource(dotenv_path=".env"))


def main() -> None:
    on_startup()
    settings = get_settings()
    print("default_model:", settings.default_model)
    print("vector_store_backend:", settings.vector_store_backend)
    print("config_source_strict:", settings.config_source_strict)


if __name__ == "__main__":
    main()
