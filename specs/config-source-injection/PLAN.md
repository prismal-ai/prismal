# Prismal — Configuration Source Inversion (decouple env from core)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-07 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Phase** | W — Configuration Source Inversion |
| **Depends on** | — (additive; mirrors Phase Y `tool-provider-injection` and Phase Z `vector-store-port`) |
| **Unblocks** | `composition-root/` (Phase R), `prismal-server`, `prismal-dashboard`, secrets-manager hosts |

---

## 1. Executive Summary

Today the `prismal` core **reads its own environment**. `prismal/core/config.py` declares a Pydantic `Settings(BaseSettings)` whose `model_config` hard-codes `env_prefix="PRISMAL_"` and `env_file=".env"`, and `get_settings()` instantiates it directly from the process environment behind an `@lru_cache`. On top of that, `prismal/core/env_compat.py` **mutates `os.environ`** on import (mirroring the legacy `LIGHTAGENT_*` prefix), and a handful of modules bypass `Settings` entirely with direct `os.getenv()` reads (`agents/tools.py` → `TAVILY_API_KEY`, `mcp/connection.py` → per-server `token_env`, `providers/registry.py` → LiteLLM credential bridge, `sandbox/*`).

The consequence: **the core is coupled to the `.env` file and the OS environment as its configuration source**. Any other component that embeds the core (`prismal-server`, `prismal-dashboard`, a notebook, a secrets-manager-backed deployment, a multi-tenant host) inherits that coupling — it cannot supply configuration from Vault, AWS Secrets Manager, a database, a request context, or an in-memory dict without first marshalling everything back through `os.environ`.

This phase **inverts the configuration dependency into a hexagonal port**, exactly as Phase Y did for tools and Phase Z did for the vector store. The core stops *reading* the environment and instead *declares a schema* (`Settings` — fields, defaults, validation) and *consumes* an injected `ConfigSourcePort` that yields the raw values. The job of *producing* those values — from `.env`, `os.environ`, the legacy alias shim, Vault, a dict, a tenant row — moves **out of the core into the host component** that owns it.

The change is **additive and opt-in**: the core ships a default `EnvConfigSource` that reproduces today's exact behaviour (`PRISMAL_*` env + `.env` + `LIGHTAGENT_*` legacy mirror), so an existing deployment that does nothing keeps working byte-for-byte. A host that *wants* to own its configuration calls `set_config_source(...)` (global) or threads a source through `build_settings(...)` / the composition root (per tenant), and the `.env` file is never touched by the core.

---

## 2. Context and Problem

### 2.1 Current Situation

- **Core owns env reading.** `Settings.model_config` sets `env_file=".env"`, `env_prefix="PRISMAL_"`. `get_settings()` = `@lru_cache → Settings()` reads the OS environment at first call. ~151 call sites across 82 files depend on this singleton.
- **Import-time global mutation.** `core/env_compat.py::apply_legacy_env_aliases()` runs on first import of `prismal.core` and writes into `os.environ`. It is a side effect on global process state that every consumer triggers transitively.
- **Direct env reads bypass Settings.** `agents/tools.py` (`TAVILY_API_KEY`), `mcp/connection.py` (`token_env`, x2), `providers/registry.py` (LiteLLM credential bridge into `os.environ`), `sandbox/manager.py`, `sandbox/isolation.py`, plus the built-in skills under `skills/available/*`. These read `os.getenv` directly and are invisible to any injected configuration.
- **Half-built injection seam already exists.** Many components already accept `settings: Settings | None = None` and fall back to `get_settings()` (`rag/engine.py`, `providers/registry.py`, `souls/*`, `security/media_validator.py`, all RAG engines). The pattern is proven — but the *source* of the `Settings` object is still hard-wired to env.

### 2.2 Problem

1. **Without a config port**, a host cannot supply configuration from anywhere other than `os.environ`/`.env`; secrets-manager and per-request/per-tenant configuration require ugly env round-tripping.
2. **Import-time `os.environ` mutation** makes the core non-pure and order-sensitive, and is incompatible with hosts that manage their own environment isolation.
3. **Scattered `os.getenv` reads** mean configuration is not centralized: the same deployment can be configured two contradictory ways (Settings vs raw env) and neither the dashboard nor a secrets source sees the raw reads.
4. **No clean per-tenant / per-request configuration** is possible while the source is a process-global singleton bound to `.env`.

### 2.3 Opportunity

The schema already exists (`Settings`, fully typed and validated). Pydantic Settings natively supports custom sources via `settings_customise_sources()`. What is missing is **a port that the core consumes instead of the environment**, plus relocating the few raw `os.getenv` reads onto that port. Low effort (one new module + a `model_config` change + ~6 read-site refactors), high impact: the core becomes source-agnostic and every downstream component can own its configuration. It also completes the `composition-root` story — `build_runtime` already wants to apply per-tenant overrides; with this phase it can do so without leaking into global env.

---

## 3. Target Users

### Persona 1: `prismal-server` (Platform Host)
- **Need:** Load configuration from its own source (env, file, Vault) and inject it once at startup so the embedded core never reads `.env` itself.
- **Frequency:** 1 per startup (global) or 1 per tenant (context).

### Persona 2: Secrets-Manager / Cloud Operator
- **Need:** Supply API keys and DSNs from AWS Secrets Manager / Vault / GCP Secret Manager instead of plaintext `.env`, without forking the core.
- **Frequency:** Per startup / per credential rotation.

### Persona 3: Multi-Tenant Operator
- **Need:** Resolve configuration per `org_id` (different models, keys, limits) from a database row, with no shared global env state between tenants.
- **Frequency:** Per request/tenant.

### Persona 4: `prismal-dashboard` (Config UI)
- **Need:** Read and edit the configuration the core consumes through a stable, source-agnostic schema (not by hand-editing a `.env` on disk).
- **Frequency:** Admin interaction.

### Persona 5: Library User / Test Author
- **Need:** Construct deterministic `Settings` from an in-memory mapping with zero environment dependency and no `.env` discovery.
- **Frequency:** Daily.

---

## 4. Objectives and Success Metrics

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Core no longer reads env directly | `os.getenv`/`os.environ` reads of *config* in `prismal/**` outside the default source | 0 (excl. `EnvConfigSource` + LiteLLM write-bridge) | Phase W |
| No import-time global mutation | `os.environ` writes at import time | 0 | Phase W |
| Injectable source | `set_config_source()` / `build_settings(source=...)` | Yes | Phase W |
| Backward compatibility | Existing `.env`/env deployment behaviour | Byte-for-byte identical via default `EnvConfigSource` | Global |
| Per-tenant config | Resolve `Settings` per `org_id` without global env | Yes | Phase W |
| AST guard | Test forbids new direct `os.getenv` of config keys in core | Enforced | Phase W |
| Coverage | Branch coverage of the new module | ≥ 85% | Global |

---

## 5. Scope

### 5.1 In Scope (Phase W)

**W1 — `ConfigSourcePort` (`prismal/core/config_source.py`):**
- [ ] `ConfigSourcePort` Protocol: `load() -> Mapping[str, str | SecretStr]` (sync, must not raise; returns what it can).
- [ ] Concrete sources: `EnvConfigSource` (default — env + optional `.env` + legacy `LIGHTAGENT_` mirror), `MappingConfigSource` (dict / in-memory), `ChainedConfigSource` (ordered precedence), `FakeConfigSource` (tests).

**W2 — Settings consumes the port (`prismal/core/config.py`):**
- [ ] Remove `env_file=".env"` from `model_config`; the file/env reading lives only in `EnvConfigSource`.
- [ ] `settings_customise_sources()` plugs the injected `ConfigSourcePort` as highest-priority source.
- [ ] `build_settings(source: ConfigSourcePort | None = None) -> Settings` — pure constructor over a source.
- [ ] `get_settings()` delegates to the injected/default source; `reload_settings()` clears the cache.

**W3 — Injection registry (`prismal/core/config_source.py`):**
- [ ] `set_config_source(source)` / `get_config_source()` (global, variant A); `config_source_strict` behaviour.
- [ ] Context variant (B): `build_settings(source=...)` threaded by the composition root per tenant.

**W4 — Relocate direct `os.getenv` reads onto Settings/port:**
- [ ] `agents/tools.py` `TAVILY_API_KEY` → `settings.tavily_api_key` (new field).
- [ ] `mcp/connection.py` `token_env` → resolved via a host-injected secret resolver / config source.
- [ ] `providers/registry.py` LiteLLM bridge reads only from injected `Settings` (keeps the single `os.environ.setdefault` write LiteLLM requires).
- [ ] `sandbox/manager.py`, `sandbox/isolation.py` config reads → `Settings` fields (already declared, e.g. `sandbox_env_allowlist`).

**W5 — Legacy shim relocation (`prismal/core/env_compat.py`):**
- [ ] Move `LIGHTAGENT_*` → `PRISMAL_*` aliasing **into** `EnvConfigSource` (no global `os.environ` mutation); keep a deprecated, no-op-on-double-call shim that emits the same `DeprecationWarning`.

**W6 — Exception + settings flags (`core/exceptions.py`, `core/config.py`):**
- [ ] `ConfigSourceError` (carries the failing source name).
- [ ] `config_source_strict: bool = False` (no source + strict → raise instead of default-env fallback).

**W7 — Host/dashboard contract:**
- [ ] Document how `prismal-server` builds a source (env/Vault) and injects it; how `composition-root` threads per-tenant sources; the stable schema `prismal-dashboard` edits.

**W8 — Tests + docs + example:**
- [ ] AST guard test (no new direct config `os.getenv` in core); parity test (default source == today's behaviour); per-tenant isolation test; `docs/configuration.md`; `examples/config_source_{env,custom}.py`.

### 5.2 Out of Scope

- Implementing `prismal-server` / `prismal-dashboard` themselves (only the contract they consume).
- Replacing the `Settings` schema or field names (they stay; only the *source* changes).
- A bespoke secrets-manager client (the host brings its own; the core only defines the port). Reference adapters may ship as examples.
- The built-in skills' own runtime secrets (`skills/available/*`) — they are plugins, not core config; noted as a future consideration (receive secrets via skill/tool context).
- The single `os.environ.setdefault` that `providers/registry.py` performs **for LiteLLM** (an external lib that reads `os.environ`); it stays but is fed exclusively from injected `Settings`.

### 5.3 Future Considerations

- `prismal.config_sources` entry-point group so plugins/hosts can contribute named sources discoverable like Phase X plugins.
- Hot-reload / watch of the underlying source (file change, secret rotation) with `reload_settings()`.
- Per-skill secret injection so `skills/available/*` no longer read `os.environ`.
- Encrypted-at-rest source and field-level audit of secret access.

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-CSI-001 | `ConfigSourcePort` Protocol: sync `load()` returning a mapping; never raises | `MUST` |
| RF-CSI-002 | `EnvConfigSource` reproduces today's behaviour (env + `.env` + legacy mirror) | `MUST` |
| RF-CSI-003 | `MappingConfigSource`, `ChainedConfigSource`, `FakeConfigSource` | `MUST` |
| RF-CSI-004 | `Settings` consumes the injected source via `settings_customise_sources`; `env_file` removed from core | `MUST` |
| RF-CSI-005 | `build_settings(source=None)` pure constructor; `get_settings()` delegates; `reload_settings()` | `MUST` |
| RF-CSI-006 | `set_config_source()` / `get_config_source()` global injection + strict mode | `MUST` |
| RF-CSI-007 | Per-tenant source threaded by the composition root (context variant) | `SHOULD` |
| RF-CSI-008 | Relocate `TAVILY_API_KEY`, `token_env`, LiteLLM bridge, sandbox reads onto Settings/port | `MUST` |
| RF-CSI-009 | Legacy `LIGHTAGENT_` mirror moved into `EnvConfigSource`; no import-time `os.environ` write | `MUST` |
| RF-CSI-010 | `ConfigSourceError`; `config_source_strict` setting | `SHOULD` |
| RF-CSI-011 | Backward-compat: zero-config deployment behaves byte-for-byte as today | `MUST` |
| RF-CSI-012 | AST guard: no new direct config `os.getenv` in `prismal/**` (excl. `EnvConfigSource`) | `SHOULD` |
| RF-CSI-013 | Documented host/dashboard contract + examples | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- `load()` is called once per `Settings` construction (cached behind `get_settings()`); no per-read I/O.
- Context mode: per-tenant `build_settings(source)` is O(fields); shareable sources may be reused.

### Security
- Secrets remain `SecretStr` in `Settings`; sources must not log raw secret values. `ConfigSourcePort.load()` returns secrets that the core wraps; no plaintext in logs or `RuntimeConfig`.
- Removing import-time `os.environ` mutation reduces global-state leakage between embedders/tenants.
- The L1–L5 security layers are unaffected — this phase only changes where config values originate.

### Compatibility
- `prismal/` PEP 420 namespace preserved. Additive: no change to node or RAG/memory signatures.
- `filterwarnings=error`: the legacy shim emits a single, suppressible `DeprecationWarning` from `EnvConfigSource`, not at import.
- Public API (`ConfigSourcePort`, sources, `build_settings`, `set_config_source`) follows SemVer.

### Maintainability
- Coverage ≥ 85% on `config_source*`; `ruff` / `mypy --strict` / `bandit` clean.
- AST guard keeps the inversion from regressing.

---

## 8. Constraints and Dependencies

| Dependency | Type | Use |
|---|---|---|
| `pydantic-settings` `BaseSettings` + `settings_customise_sources` | Existing | Plug the injected source as a Pydantic source |
| `prismal/core/config.py` `Settings` schema | Existing | Unchanged schema; new source wiring |
| `prismal/core/env_compat.py` | Existing | Legacy mirror relocated into `EnvConfigSource` |
| Phase Y / Z injection precedent | Reference | Same registry + Protocol + Fake playbook |
| `composition-root/` (Phase R) | Consumer | Threads per-tenant sources via `apply_org_overrides` |

No hard ordering dependency: Phase W is additive and can ship independently. It **strengthens** the composition root (R), which becomes the natural place to inject per-tenant sources.

---

## 9. User Stories

**US-CSI-001:** As `prismal-server`, I own configuration loading and inject it once.
```python
from prismal.core.config_source import EnvConfigSource, set_config_source
set_config_source(EnvConfigSource(dotenv_path=".env"))   # core never reads .env itself
```

**US-CSI-002:** As a Cloud Operator, I load secrets from Vault, not `.env`.
```python
class VaultConfigSource:
    def load(self) -> dict[str, str]:
        return {"PRISMAL_ANTHROPIC_API_KEY": vault.read("prismal/anthropic")}
set_config_source(ChainedConfigSource([VaultConfigSource(), EnvConfigSource()]))
```

**US-CSI-003:** As a Multi-Tenant Operator, each tenant resolves its own config.
```python
ctx = await build_runtime(build_settings(MappingConfigSource(tenant_row(org_id))), org_id=org_id)
```

**US-CSI-004:** As a Test Author, I build deterministic settings with no environment.
```python
s = build_settings(FakeConfigSource({"PRISMAL_DEFAULT_MODEL": "claude-test"}))
```

**US-CSI-005:** As an existing user, I change nothing and everything still works (default `EnvConfigSource`).

---

## 10. Risks and Mitigations

| Risk | Prob. | Impact | Mitigation |
|---|---|---|---|
| Behavioural drift vs today's env reading | Medium | High | `EnvConfigSource` is the default; parity test asserts identical resolution incl. unprefixed `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` and legacy mirror |
| Missed direct `os.getenv` read in core | Medium | Medium | Inventory + AST guard test; the relocation list is explicit (W4) |
| `filterwarnings=error` trips on the legacy `DeprecationWarning` | Medium | Low | Warning emitted from `EnvConfigSource` (suppressible), not at import; tests assert exactly one |
| LiteLLM still needs `os.environ` | High | Low | Out of scope to remove; the single `setdefault` write stays, fed only from injected `Settings` |
| Per-tenant secret leakage via global cache | Low | Critical | Context variant uses `build_settings(source)` (no global); isolation test |
| Over-engineering a secrets framework | Medium | Medium | Core defines only the port; concrete managers ship as examples / host code |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| W1 — Port + sources | 0.5 wk | `ConfigSourcePort`, `EnvConfigSource`, `MappingConfigSource`, `ChainedConfigSource`, `FakeConfigSource` |
| W2 — Settings wiring | 0.5 wk | `settings_customise_sources`, `build_settings`, `get_settings`/`reload_settings` |
| W3 — Injection registry | 0.2 wk | `set_config_source`/`get_config_source` + strict |
| W4 — Relocate raw reads | 0.6 wk | tavily/token_env/registry/sandbox onto Settings/port |
| W5 — Legacy shim relocation | 0.2 wk | mirror inside `EnvConfigSource`; no import-time write |
| W6 — Exception + flags | 0.1 wk | `ConfigSourceError`, `config_source_strict` |
| W7 — Host/dashboard contract | 0.3 wk | contract docs |
| W8 — Tests + docs + examples | 0.6 wk | AST guard, parity, isolation, docs, examples |
| Hardening | 0.3 wk | coverage, mypy/bandit, validation |
| **Total** | **~3.1 wk** | core fully decoupled from env; values produced by host components |

---

## 12. Definition of Done (Global for Phase W)

- [ ] `ConfigSourcePort` + `EnvConfigSource`/`MappingConfigSource`/`ChainedConfigSource`/`FakeConfigSource` implemented.
- [ ] `Settings` consumes the injected source; `env_file` removed from core `model_config`; `build_settings`/`reload_settings` in place.
- [ ] `set_config_source`/`get_config_source` + `config_source_strict`; context variant threadable by the composition root.
- [ ] All in-scope direct `os.getenv` config reads relocated (W4); legacy mirror moved into `EnvConfigSource` (no import-time `os.environ` write).
- [ ] Default deployment behaves byte-for-byte as today (parity test green).
- [ ] AST guard forbids new direct config `os.getenv` in core; `ConfigSourceError` defined.
- [ ] Host contract (`prismal-server`, `composition-root`) and dashboard schema documented.
- [ ] `docs/configuration.md` + `examples/config_source_env.py` + `examples/config_source_custom.py`.
- [ ] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff` / `mypy --strict` / `bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + `specs/roadmap.md` updated.
- [ ] PR merged with review.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-07 | Ernesto Crespo | Initial version — configuration source inversion (decouple env from core) |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| Security Lead | — | | ☐ Pending |
