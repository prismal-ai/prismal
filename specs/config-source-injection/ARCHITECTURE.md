# Prismal Configuration Source Inversion — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-07 |
| **Related PLAN** | `specs/config-source-injection/PLAN.md` |
| **Related SPEC** | `specs/config-source-injection/SPEC.md` |
| **TASKS** | `specs/config-source-injection/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Context

`prismal` is the publishable agent-framework core. Phase X exposed a public extension surface; Phase Y inverted tool resolution into `ToolProviderPort`; Phase Z (planned) inverts the vector store into `VectorStorePort`; Phase R (`composition-root`) composes those ports for a host. One dependency remains **un-inverted: configuration itself**. The core reads its own environment — `Settings.model_config` binds `env_file=".env"`, `get_settings()` instantiates from `os.environ`, `env_compat.py` mutates `os.environ` at import, and ~6 modules call `os.getenv` directly.

This document describes **Phase W — Configuration Source Inversion**: a hexagonal port (`ConfigSourcePort`) that *supplies* configuration values, so the core stops *reading* the environment and instead *consumes an injected source*. Producing those values (env, `.env`, Vault, a dict, a tenant row) moves into the host component. It mirrors the Phase Y/Z playbook (Protocol + concrete providers + registry + Fake) applied to `pydantic-settings`.

---

## 2. Technical Objectives

- **OT-1:** Define `ConfigSourcePort` and consume it from `Settings` via `settings_customise_sources()`.
- **OT-2:** Remove `env_file=".env"` from the core; all env/file reading lives in `EnvConfigSource`.
- **OT-3:** Remove import-time `os.environ` mutation (`env_compat`); relocate the legacy mirror into `EnvConfigSource`.
- **OT-4:** Relocate the ~6 direct `os.getenv` config reads onto `Settings` / the port (keep only the LiteLLM `setdefault` write).
- **OT-5:** Two modes — global (`set_config_source`) and context (`build_settings(source)` threaded by the composition root) — with no shared per-tenant state.
- **OT-6:** Byte-for-byte backward compatibility via the default `EnvConfigSource`; `get_settings()` signature unchanged.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram

```
   HOST (prismal-server / dashboard / notebook / secrets operator)
        │  builds a source it owns:
        │     EnvConfigSource | VaultConfigSource | MappingConfigSource | Chained
        ▼
   set_config_source(src)                build_settings(src)   ← per-tenant (context)
        │ (global)                              │
        ▼                                        ▼
   ┌──────────────────  prismal/core/config.py  ─────────────────┐
   │  Settings.settings_customise_sources():                     │
   │     init kwargs  >  injected ConfigSourcePort.load()        │
   │  build_settings(source) / get_settings() (cached)           │
   └───────────────┬─────────────────────────────────────────────┘
                   │  Settings (schema only: fields/defaults/validation)
                   ▼
        prismal core (agents, rag, memory, providers, sandbox, mcp)
            consume Settings via `settings: Settings | None = None`
            (no module reads os.environ for config anymore)
```

### 3.2 Layer Diagram (ecosystem)

```
┌───────────────────────────────────────────────────────────┐
│ prismal-dashboard  → EDITS config values (provider keys,   │
│                      security, storage, runtime)           │
└───────────────┬───────────────────────────────────────────┘
                │ persists to the store backing the source
┌───────────────▼───────────────────────────────────────────┐
│ prismal-server  → OWNS config: builds ConfigSourcePort     │
│   (Env / Vault / DB) and injects it:                       │
│     set_config_source(src)   OR   build_runtime(build_settings(src))│
└───────────────┬───────────────────────────────────────────┘
                │ injected source
┌───────────────▼───────────────────────────────────────────┐
│ prismal core                                               │
│   config_source.py (W) ── port ──► EnvConfigSource (default)│
│   config.py: Settings (schema) consumes the port           │
│   consumers take Settings; no direct env reads             │
└───────────────────────────────────────────────────────────┘
        prismal-sdk = API client (does not configure the core)
```

### 3.3 Components

#### W1 — `ConfigSourcePort` + sources (`prismal/core/config_source.py`)
- `ConfigSourcePort` Protocol: sync `load() -> Mapping[str, str | SecretStr]`, never raises.
- `EnvConfigSource`: the **only** core source touching `os.environ`/`.env`; folds in the legacy `LIGHTAGENT_` mirror.
- `MappingConfigSource`: dict / tenant row.
- `ChainedConfigSource`: ordered precedence (first wins), sub-source errors caught.
- `FakeConfigSource`: deterministic tests.
- `set_config_source` / `get_config_source`: global injection (invalidates the `get_settings` cache).

#### W2 — `Settings` wiring (`prismal/core/config.py`)
- Drop `env_file`; add `settings_customise_sources()` that adapts the injected port into a `PydanticBaseSettingsSource`.
- `build_settings(source=None)`: pure constructor (per-tenant / tests).
- `get_settings()`: `@lru_cache` over `build_settings(get_config_source())`; `reload_settings()` clears it.

#### W3 — Relocated reads (`agents/tools.py`, `mcp/connection.py`, `providers/registry.py`, `sandbox/*`)
- Config values now come from `Settings`; only the LiteLLM `os.environ.setdefault` write remains, fed from injected `Settings`.

#### W4 — Legacy shim (`core/env_compat.py`)
- Mirror logic moved into `EnvConfigSource`; module no longer mutates `os.environ` at import; `apply_legacy_env_aliases()` becomes a deprecated no-op shim.

#### W5 — Exception + flags (`core/exceptions.py`, `core/config.py`)
- `ConfigSourceError`; `config_source_strict`, `tavily_api_key` fields.

### 3.4 Data Flows

#### Flow W-A: Global startup
```
1. host builds src = EnvConfigSource(".env")  (or Vault/Chained)
2. set_config_source(src) → reload_settings() (clears cache)
3. first get_settings() → build_settings(src) → Settings(_customise_sources injects src.load())
4. every consumer reads the same cached Settings; core never touches os.environ for config
```

#### Flow W-B: Per-tenant (context, via composition root)
```
1. request org=acme → src = MappingConfigSource(tenant_row("acme"))
2. settings = build_settings(src)                # no global, no cache
3. ctx = await build_runtime(settings, org_id="acme", mode="context")
4. tenant runs with its own Settings; another tenant in parallel shares no config state
```

#### Flow W-C: Backward-compat (zero config)
```
1. host does nothing
2. get_settings() → get_config_source() is None → _default_source() = EnvConfigSource()
3. resolution identical to today (PRISMAL_* env + .env + legacy mirror)
```

---

## 4. Design Decisions

### DD-CSI-001: Invert the source, not the schema
`Settings` stays the single typed/validated schema (field names, defaults, `SecretStr`, validators). Only *where the raw values come from* is inverted. This keeps the ~151 `get_settings()` call sites and every `settings.<field>` access unchanged.

### DD-CSI-002: Adapt the port through `settings_customise_sources`
Rather than bypass Pydantic, the injected port is wrapped as a `PydanticBaseSettingsSource`. We keep Pydantic's coercion/validation and alias handling (`populate_by_name`, unprefixed provider keys) and simply replace *the env/dotenv sources* with the injected one. Init kwargs still win, so `Settings(field=...)` keeps working (tests rely on it).

### DD-CSI-003: `EnvConfigSource` is the default — parity by construction
The default source *is* today's behaviour: `os.environ` + `.env` + legacy mirror, including the accepted unprefixed provider keys. A zero-config deployment is byte-for-byte identical. The only observable difference is *where* the legacy `DeprecationWarning` originates (the source, on first mirror) — no longer at import.

### DD-CSI-004: No import-time global mutation
`env_compat` mutating `os.environ` on import is a hidden global side effect that breaks host environment isolation and per-tenant separation. Folding the mirror into `EnvConfigSource.load()` makes it explicit, local, and only active when that source is used.

### DD-CSI-005: Keep the one LiteLLM `os.environ` write
LiteLLM reads provider credentials from `os.environ`; we cannot remove that without forking LiteLLM. `providers/registry.py` keeps a single `os.environ.setdefault(...)` bridge, but it reads exclusively from the injected `Settings` — so the *source of truth* is still the port, and `setdefault` never overrides a host-set value.

### DD-CSI-006: Global and context modes mirror Phase Y/Z
`set_config_source` (global, variant A) for single-tenant hosts; `build_settings(source)` (context, variant B) threaded by the composition root for multi-tenant. No new `*_mode` setting is required — the presence of a threaded source selects context; this avoids config-surface growth (`config_source_strict` is the only new flag).

### DD-CSI-007: The feature lives in the core; hosts produce values
`prismal/core/config_source.py` belongs to the publishable core (it defines the port + the default env source). Concrete remote sources (Vault, AWS, DB) live in the host or ship as `examples/`. The contract exists before the server.

### DD-CSI-008: Full backward-compat + AST guard
Opt-in: nothing changes for existing users. An AST test forbids new direct config `os.getenv` reads in `prismal/**` (exempting `EnvConfigSource` and the LiteLLM bridge), mirroring Phase Y's `test_no_mcp_skills_imports.py` guard so the inversion does not regress.

---

## 5. Code Structure

```
prismal/
├── core/
│   ├── config_source.py        # NEW: ConfigSourcePort, Env/Mapping/Chained/Fake, set/get_config_source
│   ├── config.py               # MOD: drop env_file; settings_customise_sources; build_settings; reload_settings; +fields
│   ├── env_compat.py           # MOD: mirror → EnvConfigSource; deprecated no-op shim; no import-time write
│   └── exceptions.py           # MOD: + ConfigSourceError
├── agents/
│   └── tools.py                # MOD: TAVILY_API_KEY → settings.tavily_api_key
├── mcp/
│   └── connection.py           # MOD: token_env resolved via source/secret resolver
├── providers/
│   └── registry.py             # MOD: LiteLLM bridge reads injected Settings only
├── sandbox/
│   ├── manager.py              # MOD: config via Settings fields
│   └── isolation.py            # MOD: config via Settings fields
└── composition/
    └── config_sources.py       # MOD (Phase R): apply_org_overrides(*, source=None)
docs/configuration.md           # NEW
examples/config_source_env.py   # NEW
examples/config_source_custom.py# NEW
tests/unit/core/config_source/  # tests
tests/unit/core/test_no_env_reads.py  # AST guard
```

### Applied Patterns
- **Hexagonal port / Dependency Inversion** (`ConfigSourcePort`).
- **Strategy** (interchangeable sources).
- **Composite** (`ChainedConfigSource`).
- **Adapter** (port → `PydanticBaseSettingsSource`).
- **Composition Root** (host owns source construction; Phase R threads per-tenant).

### Error Handling
- A source whose `load()` raises is caught and skipped (`ChainedConfigSource`) or, for a lone strict source, surfaced as `ConfigSourceError`.
- `config_source_strict=True` + no injected source → `ConfigSourceError` instead of the `EnvConfigSource` fallback (for hosts that must never read ambient env).

---

## 6. Security

- **Secret hygiene:** secrets stay `SecretStr` in `Settings`; sources must not log raw values; `repr` masking preserved. The adapter never echoes values into logs or spans.
- **Reduced global state:** removing import-time `os.environ` mutation removes a cross-tenant/cross-embedder leak vector.
- **Tenant isolation:** context mode uses `build_settings(source)` with no global cache, so two tenants never share a `Settings` or a source.
- **Least ambient authority:** `config_source_strict` lets a hardened host forbid the implicit `EnvConfigSource` fallback entirely.
- **No change to L1–L5:** this phase only changes value origin; guardrails, interceptor, audit, sanitizer are untouched.

---

## 7. Observability

- Span `prismal.config.build_settings` with `source` (class name), `mode` (global/context), `n_keys`, `org_id?` — **never** values.
- Log `config.source_injected{source}` on `set_config_source`; `config.legacy_alias_mirrored{keys}` (names only) once per process from `EnvConfigSource`.
- Metric `prismal_config_source_loads_total{source}`, `prismal_config_source_errors_total{source}`.

---

## 8. Testing Strategy

- **Parity:** `build_settings(EnvConfigSource())` == today's `Settings()` for a representative env+`.env` fixture (incl. unprefixed `ANTHROPIC_API_KEY`, legacy `LIGHTAGENT_*` mirror, precedence).
- **Sources:** `MappingConfigSource` round-trips; `ChainedConfigSource` precedence (first wins) and sub-source error skipping; `FakeConfigSource` purity.
- **Injection:** `set_config_source` invalidates `get_settings` cache; `get_config_source` returns the injected source; `reload_settings` clears.
- **Strict:** `config_source_strict=True` + no source → `ConfigSourceError`.
- **Relocation:** `tavily_api_key`, `token_env`, sandbox reads resolve from `Settings`; LiteLLM bridge fed from injected `Settings` (no ambient read).
- **No import-time mutation:** importing `prismal.core` does not write `os.environ` (assert env snapshot unchanged).
- **AST guard:** no direct config `os.getenv` in `prismal/**` except `EnvConfigSource`/LiteLLM bridge.
- **Isolation:** two `build_settings(MappingConfigSource(...))` in `asyncio.gather` (via `build_runtime` context) don't share state.
- **Backward-compat:** existing tests that construct `Settings(field=...)` or rely on `.env` still pass.

---

## 9. Rollout Plan

1. W1–W2 (port + sources + Settings wiring) — additive; default `EnvConfigSource` keeps parity.
2. W3 (relocate raw reads) — behaviour-preserving refactor behind the same `Settings`.
3. W4 (legacy shim relocation) — remove import-time mutation.
4. W5 (exception/flags) + W7/W8 (contract, tests, docs).

Backout: the feature is opt-in; not calling `set_config_source` leaves `EnvConfigSource` in effect (today's behaviour). The relocations (W3/W4) are pure refactors guarded by the parity test.

---

## 10. Open Questions

- **PA-1:** Should `token_env` resolution use a dedicated `SecretResolverPort` distinct from `ConfigSourcePort`, or reuse the config source? (Proposal: reuse the config source; add a thin `resolve_secret(name)` helper that reads it.)
- **PA-2:** Do we expose a `prismal.config_sources` entry-point group now (plugin-discoverable sources) or defer to a follow-up? (Proposal: defer; ship the port + examples first.)
- **PA-3:** Should `get_settings()` remain `@lru_cache` or move to an explicit module global for easier reload semantics? (Proposal: keep `@lru_cache` + `reload_settings()`; lowest churn.)
- **PA-4:** Should built-in skills' own `os.getenv` reads be in scope? (Proposal: out of scope here; future per-skill secret injection.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-07 | Ernesto Crespo | Initial technical design — configuration source inversion |
