# Prismal Configuration Source Inversion — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-07 |
| **PLAN** | `specs/config-source-injection/PLAN.md` |
| **Architecture** | `specs/config-source-injection/ARCHITECTURE.md` |
| **TASKS** | `specs/config-source-injection/TASKS.md` |

---

## Conventions

- `from __future__ import annotations` in all modules.
- The `Settings` **schema** (field names, types, defaults, validation) does not change. Only the **source** of values changes.
- `ConfigSourcePort.load()` is **sync** and **must not raise**; on failure it returns what it can (an empty mapping at minimum). The host connects to remote secret stores *before* wrapping them in a source.
- Secrets stay `SecretStr` inside `Settings`. Sources never log raw values.
- The legacy `LIGHTAGENT_*` mirror lives **inside** `EnvConfigSource`, never as an import-time `os.environ` mutation.
- Only `EnvConfigSource` (and the single LiteLLM `os.environ.setdefault` write-bridge in `providers/registry.py`) may touch `os.environ`.

---

## Module Summary

| Module | Status | Content |
|---|---|---|
| `prismal/core/config_source.py` | NEW | `ConfigSourcePort`, `EnvConfigSource`, `MappingConfigSource`, `ChainedConfigSource`, `FakeConfigSource`, `set_config_source`, `get_config_source` |
| `prismal/core/config.py` | MODIFIED | drop `env_file`; `settings_customise_sources`; `build_settings`, `reload_settings`; `get_settings` delegates; `+ tavily_api_key`, `+ config_source_strict` |
| `prismal/core/env_compat.py` | MODIFIED | mirror logic relocated to `EnvConfigSource`; deprecated thin shim |
| `prismal/core/exceptions.py` | MODIFIED | `+ ConfigSourceError` |
| `prismal/agents/tools.py` | MODIFIED | `TAVILY_API_KEY` → `settings.tavily_api_key` |
| `prismal/mcp/connection.py` | MODIFIED | `token_env` resolved via injected secret resolver / config source |
| `prismal/providers/registry.py` | MODIFIED | LiteLLM bridge reads only injected `Settings` (single `setdefault` write retained) |
| `prismal/sandbox/manager.py`, `prismal/sandbox/isolation.py` | MODIFIED | config reads via `Settings` fields |
| `prismal/composition/config_sources.py` | MODIFIED (Phase R) | `apply_org_overrides` accepts a `ConfigSourcePort` |

---

## SPEC-CSI-001: `ConfigSourcePort` (in `config_source.py`)

```python
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

ConfigValue = str | "SecretStr"

@runtime_checkable
class ConfigSourcePort(Protocol):
    """A source of raw configuration values consumed by Settings.

    Conforming shapes: EnvConfigSource, MappingConfigSource, ChainedConfigSource,
    FakeConfigSource, and any host source (Vault, AWS Secrets Manager, DB row).
    The core only invokes load(); it never constructs concrete sources.
    """

    def load(self) -> Mapping[str, ConfigValue]: ...
```

Rules:
- `load()` is **sync** and **must not raise**; a backing store that is unreachable yields what it can (≥ empty mapping).
- Keys use the canonical `PRISMAL_<FIELD>` form **or** the bare field name (`default_model`); `Settings(populate_by_name=True)` already accepts both. Well-known unprefixed provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `TAVILY_API_KEY`) are honoured by `EnvConfigSource` for parity.
- Values are strings (Pydantic coerces) or `SecretStr` for secrets; the source decides which to wrap.

---

## SPEC-CSI-002: `EnvConfigSource` (in `config_source.py`)

```python
class EnvConfigSource:
    """Default source — reproduces today's behaviour exactly.

    Reads, in this precedence (highest first):
      1. os.environ (PRISMAL_* and accepted unprefixed provider keys)
      2. the dotenv file at `dotenv_path` (default ".env"), if present
    and applies the legacy LIGHTAGENT_* → PRISMAL_* mirror (only where the
    PRISMAL_ name is unset), emitting a single DeprecationWarning the first
    time a legacy key is mirrored.
    """

    def __init__(
        self,
        *,
        dotenv_path: "str | Path | None" = ".env",
        env: "Mapping[str, str] | None" = None,   # defaults to os.environ
        include_legacy_aliases: bool = True,
    ) -> None: ...

    def load(self) -> Mapping[str, ConfigValue]: ...
```

- This is the **only** core source that reads `os.environ` / the `.env` file.
- The legacy mirror is computed **into the returned mapping**, not by mutating `os.environ` (behaviour preserved, global side effect removed).
- If `dotenv_path` does not exist, it is silently skipped (parity with `pydantic-settings`).

---

## SPEC-CSI-003: `MappingConfigSource` (in `config_source.py`)

```python
class MappingConfigSource:
    """In-memory source from an explicit mapping (dict / tenant row / API payload)."""

    def __init__(self, values: Mapping[str, ConfigValue]) -> None: ...

    def load(self) -> Mapping[str, ConfigValue]:
        """Returns a defensive copy of `values`. No I/O, never raises."""
```

---

## SPEC-CSI-004: `ChainedConfigSource` (in `config_source.py`)

```python
class ChainedConfigSource:
    """Ordered composite: earlier sources win over later ones (first = highest priority)."""

    def __init__(self, sources: list[ConfigSourcePort]) -> None: ...

    def load(self) -> Mapping[str, ConfigValue]:
        """Merges load() of each source; for duplicate keys the earliest source wins.
        A source whose load() raises is caught, logged (config_source.subsource_error),
        and skipped (parity with Phase Y CompositeToolProvider)."""
```

Convention: place high-trust/secret sources first (e.g. `[VaultConfigSource(), EnvConfigSource()]`) so secrets override file/env defaults.

---

## SPEC-CSI-005: `FakeConfigSource` (in `config_source.py`, for tests)

```python
class FakeConfigSource:
    """Deterministic test source. No environment access, no .env discovery."""

    def __init__(self, values: Mapping[str, ConfigValue] | None = None) -> None: ...

    def load(self) -> Mapping[str, ConfigValue]:
        """Returns `values` (or {}). Pure."""
```

---

## SPEC-CSI-006: Injection registry (in `config_source.py`)

```python
_source: ConfigSourcePort | None = None

def set_config_source(source: ConfigSourcePort) -> None:
    """Inject the global config source. Idempotent; the host calls it once at startup.
    Invalidates the get_settings() cache (calls reload_settings())."""

def get_config_source() -> ConfigSourcePort | None:
    """Return the injected global source, or None."""

def _default_source() -> ConfigSourcePort:
    """EnvConfigSource() — used when no source is injected and strict mode is off."""
```

---

## SPEC-CSI-007: Settings consumes the source (in `core/config.py`)

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRISMAL_",
        # env_file=".env"  ← REMOVED: the .env reading now lives in EnvConfigSource
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        """Plug the injected ConfigSourcePort as the highest-priority source.

        Precedence: init kwargs > injected ConfigSourcePort > (nothing else).
        When no source is injected, falls back to EnvConfigSource via _default_source(),
        preserving today's env+.env resolution. The native env_settings/dotenv_settings
        are NOT used directly anymore — all env/file reading is funnelled through the source.
        """
```

Note: the implementation wraps the port's `load()` result in a `pydantic_settings` `PydanticBaseSettingsSource` callable. Init kwargs still win (so `Settings(default_model=...)` keeps working).

---

## SPEC-CSI-008: `build_settings` / `get_settings` / `reload_settings` (in `core/config.py`)

```python
def build_settings(source: ConfigSourcePort | None = None) -> Settings:
    """Pure constructor over a source (no global state).

    - source given  → Settings built from that source only (per-tenant / tests).
    - source None   → uses get_config_source() or _default_source().
    Raises ConfigSourceError if config_source_strict and no source is available.
    """

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached global settings. Delegates to build_settings(get_config_source())."""

def reload_settings() -> None:
    """Clear the get_settings() lru_cache. Called by set_config_source()."""
```

- `get_settings()` keeps its signature and return type (stable API; the ~151 call sites are untouched).
- `build_settings(source)` is the context/per-tenant path — no singleton involved.

---

## SPEC-CSI-009: New Settings fields (in `core/config.py`)

```python
# Web search (relocated from agents/tools.py os.getenv)
tavily_api_key: SecretStr = Field(default=SecretStr(""), description="Tavily web-search API key")

# Config source inversion
config_source_strict: bool = Field(
    default=False,
    description="If True, a missing injected ConfigSourcePort raises ConfigSourceError "
                "instead of falling back to EnvConfigSource.",
)
```

- `tavily_api_key` accepts the bare `TAVILY_API_KEY` (via `EnvConfigSource` unprefixed allowlist) and `PRISMAL_TAVILY_API_KEY`.

---

## SPEC-CSI-010: Relocated reads (W4)

```python
# agents/tools.py  — BEFORE
tavily_key = os.environ.get("TAVILY_API_KEY", "")
# AFTER
tavily_key = get_settings().tavily_api_key.get_secret_value()

# mcp/connection.py — token_env stays a *name*, but resolution goes through the source:
#   token = resolve_secret(self._config.auth.token_env)
# where resolve_secret() reads the injected ConfigSourcePort (or a host SecretResolver),
# not os.environ directly. A deferred default reproduces os.environ lookup for parity.

# providers/registry.py — keeps the LiteLLM bridge:
#   for env_var, value in self._settings.<provider_keys>:
#       os.environ.setdefault(env_var, value)   # the ONE allowed write; source = injected Settings
```

Invariant after W4: the only `os.environ` *reads* of config in `prismal/**` are inside `EnvConfigSource`; the only `os.environ` *write* is the LiteLLM `setdefault` bridge.

---

## SPEC-CSI-011: Legacy shim relocation (in `core/env_compat.py`)

```python
def apply_legacy_env_aliases() -> list[str]:
    """DEPRECATED. The LIGHTAGENT_* → PRISMAL_* mirror now happens inside
    EnvConfigSource.load(). This function is retained as a no-op-returning shim that
    emits a DeprecationWarning and returns []. It no longer mutates os.environ and is
    NOT called at import time."""
```

- The module-level `apply_legacy_env_aliases()` call at import is **removed**.
- The same user-facing `DeprecationWarning` (listing mirrored legacy keys) is emitted by `EnvConfigSource` the first time it mirrors one.

---

## SPEC-CSI-012: Exception (in `core/exceptions.py`)

```python
class ConfigSourceError(PrismalError):
    """No ConfigSourcePort injected and settings.config_source_strict is True,
    or an injected source failed irrecoverably."""
    def __init__(self, source: str, cause: str = "") -> None:
        msg = f"Configuration source '{source}' unavailable"
        super().__init__(f"{msg}: {cause}" if cause else msg + ".")
```

---

## SPEC-CSI-013: Composition-root integration (Phase R consumer)

`specs/composition-root` `apply_org_overrides(settings, org_id, overrides)` gains an optional source:

```python
def apply_org_overrides(
    settings: Settings,
    org_id: str | None,
    overrides: dict[str, Any] | None,
    *,
    source: ConfigSourcePort | None = None,
) -> Settings:
    """Effective per-tenant settings. If `source` is given, builds from
    build_settings(source) then applies `overrides`; otherwise uses `settings`.
    Does not mutate any global."""
```

In `context` mode the composition root threads a per-tenant `MappingConfigSource` (or host source) — no global `set_config_source`, so tenants never share config state.

---

## Host Contract (prismal-server / prismal-dashboard)

### Standard startup (variant A — global)
```python
from prismal.core.config_source import EnvConfigSource, set_config_source

def on_startup() -> None:
    set_config_source(EnvConfigSource(dotenv_path=".env"))   # or a Vault/AWS source
    # get_settings() everywhere now resolves through the injected source
```

### Secrets manager
```python
from prismal.core.config_source import ChainedConfigSource, EnvConfigSource, set_config_source

class VaultConfigSource:
    def load(self):
        return {"PRISMAL_ANTHROPIC_API_KEY": vault.read("prismal/anthropic")}

set_config_source(ChainedConfigSource([VaultConfigSource(), EnvConfigSource()]))
```

### Per-tenant (variant B — context, via composition root)
```python
from prismal.core.config import build_settings
from prismal.core.config_source import MappingConfigSource
from prismal.composition import build_runtime

ctx = await build_runtime(
    build_settings(MappingConfigSource(tenant_config_row(org_id))),
    org_id=org_id, mode="context",
)
```

### Dashboard schema (edited, not a raw `.env`)
| UI Section | Settings field(s) the source supplies |
|---|---|
| Providers & keys | `llm_provider`, `default_model`, `*_api_key`, `tavily_api_key` |
| Security | `security_mode`, `risk_threshold`, `shell_enabled` |
| Storage | `db_url`, `chroma_path`, `vector_store_backend`, `mongodb_url` |
| Runtime | `config_source_strict`, `runtime_mode` (Phase R) |

The dashboard persists values to whatever store backs the host's `ConfigSourcePort`; the server re-injects (restart or future hot-reload).

---

## Compatibility and Versioning

- `ConfigSourcePort`, the sources, `build_settings`, `set_config_source`/`get_config_source` are **public API** (SemVer; breaking → minor + 1-release deprecation).
- `get_settings()` keeps its signature and resolution semantics (parity test); the ~151 call sites are untouched.
- `env_compat.apply_legacy_env_aliases()` becomes a deprecated no-op shim; removed no earlier than version `X+1`.
- **Opt-in:** with no injected source, `EnvConfigSource` is used and behaviour is byte-for-byte identical to today.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-07 | Ernesto Crespo | Initial interface specification — `ConfigSourcePort`, sources, Settings delegation, raw-read relocation |
