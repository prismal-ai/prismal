# Configuration source injection (Phase W)

`prismal`'s core no longer **reads** its own environment. It declares the
configuration *schema* (`Settings` — fields, defaults, validation) and *consumes*
an injected **`ConfigSourcePort`** that *supplies* the raw values. Producing those
values — from `os.environ`, a `.env` file, Vault, AWS Secrets Manager, a database
row, or an in-memory dict — is the job of the **host** that embeds the core
(`prismal-server`, `prismal-dashboard`, a notebook, a secrets operator).

This mirrors the Phase Y (`ToolProviderPort`) and Phase Z (`VectorStorePort`)
hexagonal-port inversions, applied to configuration.

> **Additive and opt-in.** With no source injected, the default
> `EnvConfigSource` reproduces the historical behaviour byte-for-byte
> (`PRISMAL_*` env + `.env` + the legacy `LIGHTAGENT_` mirror). An existing
> deployment that does nothing keeps working.

## The port

```python
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

@runtime_checkable
class ConfigSourcePort(Protocol):
    def load(self) -> Mapping[str, str | SecretStr]: ...   # sync; must not raise
```

`load()` returns a mapping keyed by the canonical `PRISMAL_<FIELD>` name (or the
bare field name — `Settings` accepts both). The well-known unprefixed provider
keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `TAVILY_API_KEY`,
`LANGFUSE_*`) are honoured for parity. Values may be `str` or `SecretStr`; the
source decides which to wrap.

## Built-in sources

| Source | Use |
|---|---|
| `EnvConfigSource(*, dotenv_path=".env", env=None, include_legacy_aliases=True)` | Default. The **only** core source that reads `os.environ` / `.env`; folds in the legacy `LIGHTAGENT_` mirror (no global `os.environ` mutation). |
| `MappingConfigSource(values)` | In-memory dict / tenant row / API payload (defensive copy). |
| `ChainedConfigSource([s1, s2, ...])` | Ordered precedence — **first source wins**; a sub-source whose `load()` raises is logged and skipped. |
| `FakeConfigSource(values=None)` | Deterministic test source — no environment access, no `.env` discovery. |

## Wiring it up

### Global injection (variant A — single-tenant host)

Inject once at startup; every `get_settings()` across the core then resolves
through the source.

```python
from prismal.core.config_source import EnvConfigSource, set_config_source

def on_startup() -> None:
    set_config_source(EnvConfigSource(dotenv_path=".env"))   # or a Vault/AWS source
```

`set_config_source()` invalidates the `get_settings()` cache, so the next read
rebuilds `Settings` from the new source. Pass `None` to clear the injection.

### Secrets manager (chained)

Place high-trust/secret sources **first** so they override file/env defaults:

```python
from prismal.core.config_source import ChainedConfigSource, EnvConfigSource, set_config_source

class VaultConfigSource:
    def load(self):
        return {"PRISMAL_ANTHROPIC_API_KEY": vault.read("prismal/anthropic")}

set_config_source(ChainedConfigSource([VaultConfigSource(), EnvConfigSource()]))
```

### Per-tenant (variant B — context, via the composition root)

`build_settings(source)` is a **pure** constructor with no global state — the
multi-tenant path. Two tenants in parallel never share a `Settings` or a source.

```python
from prismal.core.config import build_settings
from prismal.core.config_source import MappingConfigSource
from prismal.composition import build_runtime

settings = build_settings(MappingConfigSource(tenant_config_row(org_id)))
ctx = await build_runtime(settings, org_id=org_id, mode="context")
```

The composition root's `apply_org_overrides(settings, org_id, overrides, *, source=None)`
accepts a per-tenant `ConfigSourcePort`: when given, it builds from
`build_settings(source)` then layers `overrides` — without mutating any global.

## Strict mode

For a hardened host that must **never** read the ambient environment:

```python
# PRISMAL_CONFIG_SOURCE_STRICT=true
```

With `config_source_strict=True` and no injected source, `build_settings()` /
`get_settings()` raise `ConfigSourceError` instead of falling back to
`EnvConfigSource`.

## Constructor / reload reference

| Function | Behaviour |
|---|---|
| `build_settings(source=None)` | Build `Settings` from `source`; `None` → injected global source or default `EnvConfigSource`. Raises `ConfigSourceError` under strict mode with no source. |
| `get_settings()` | Cached global settings (`@lru_cache`); delegates to `build_settings()`. Signature unchanged — the ~151 existing call sites are untouched. |
| `reload_settings()` | Clear the `get_settings()` cache (called automatically by `set_config_source`). |

## Dashboard schema

A config UI edits the values the source supplies, through the stable `Settings`
schema (not by hand-editing a `.env`):

| UI section | `Settings` fields |
|---|---|
| Providers & keys | `llm_provider`, `default_model`, `*_api_key`, `tavily_api_key` |
| Security | `security_mode`, `risk_threshold`, `shell_enabled` |
| Storage | `db_url`, `chroma_path`, `vector_store_backend`, `mongodb_url` |
| Runtime | `config_source_strict`, `runtime_mode` (Phase R) |

The dashboard persists values to whatever store backs the host's
`ConfigSourcePort`; the server re-injects on restart (or a future hot-reload).

## What changed in the core

- `Settings.model_config` no longer sets `env_file` — all env/file reading lives
  in `EnvConfigSource`. `settings_customise_sources` adapts the injected port into
  a Pydantic source (preserving prefix/alias/JSON-list handling); init kwargs
  still win (`Settings(field=...)`).
- `prismal/core/env_compat.py` no longer mutates `os.environ` at import;
  `apply_legacy_env_aliases()` is a deprecated no-op shim (the mirror moved into
  `EnvConfigSource`).
- The direct `os.getenv` config reads relocated onto `Settings` / the port:
  `agents/tools.py` (`tavily_api_key`), `mcp/connection.py` (`resolve_secret`).
  The single LiteLLM `os.environ.setdefault` write-bridge in
  `providers/registry.py` stays, fed only from injected `Settings`.
- An architecture test (`tests/unit/core/test_no_env_reads.py`) forbids new
  direct config `os.getenv` reads in the core.

## See also

- `examples/config_source_env.py` — default env injection.
- `examples/config_source_custom.py` — Vault-style source + per-tenant settings.
- `docs/composition-root.md` — how `build_runtime` threads per-tenant sources.
