# Prismal Configuration Source Inversion — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-07 |
| **PLAN** | `specs/config-source-injection/PLAN.md` |
| **Architecture** | `specs/config-source-injection/ARCHITECTURE.md` |
| **SPEC** | `specs/config-source-injection/SPEC.md` |
| **Depends on** | — (additive; reference playbook: Phase Y `tool-provider-injection`) |
| **Unblocks** | `composition-root/` per-tenant sources; `prismal-server` / `prismal-dashboard` config ownership |

---

## 1. Implementation Summary

Phase W inverts configuration into a hexagonal port (`ConfigSourcePort`). The core stops reading `.env`/`os.environ` and instead consumes an injected source; producing the values moves to the host. **Additive and opt-in**: a default `EnvConfigSource` reproduces today's behaviour byte-for-byte, so an unchanged deployment is unaffected.

Guiding principle: **invert the source, not the schema**. `Settings` (fields/defaults/validation) and the ~151 `get_settings()` call sites stay; only where the raw values originate changes.

---

## 2. Prerequisites

- Existing: `Settings`/`get_settings()` in `core/config.py`; `env_compat.py`; `pydantic-settings` with `settings_customise_sources`.
- Inventory of direct config `os.getenv` reads in core (confirmed): `agents/tools.py` (`TAVILY_API_KEY`), `mcp/connection.py` (`token_env` ×2), `providers/registry.py` (LiteLLM bridge), `sandbox/manager.py`, `sandbox/isolation.py`. (Built-in skills under `skills/available/*` are out of scope.)
- Reference: Phase Y registry + Protocol + Fake + AST-guard pattern.

---

## 3. Implementation Phases

### PHASE W1 — Port + sources
#### W1-01 — Protocol + concrete sources
- [ ] Create `prismal/core/config_source.py` with `ConfigSourcePort`, `EnvConfigSource`, `MappingConfigSource`, `ChainedConfigSource`, `FakeConfigSource`.
- [ ] `EnvConfigSource` reads `os.environ` + optional `.env` + folds in the `LIGHTAGENT_*` mirror (no global mutation); honours unprefixed provider keys.
- **Done:** each source's `load()` is sync, never raises; `EnvConfigSource` parity fixture matches today.
#### W1-02 — Injection registry
- [ ] `set_config_source` (invalidates cache via `reload_settings`), `get_config_source`, `_default_source`.
- **Done:** injecting then `get_config_source()` returns the source.

### PHASE W2 — Settings wiring
#### W2-01 — `settings_customise_sources`
- [ ] Adapt the injected port into a `PydanticBaseSettingsSource`; precedence init kwargs > injected source; drop `env_file` from `model_config`.
- **Done:** `Settings(field=...)` still wins; env/.env read only via the source.
#### W2-02 — Constructors
- [ ] `build_settings(source=None)`; `get_settings()` delegates (`@lru_cache`); `reload_settings()`.
- **Done:** `build_settings(FakeConfigSource({...}))` builds without touching env.

### PHASE W3 — New fields + exception
#### W3-01 — Fields
- [ ] `tavily_api_key: SecretStr`; `config_source_strict: bool`.
#### W3-02 — Exception
- [ ] `ConfigSourceError` in `core/exceptions.py`.
- **Done:** strict + no source → `ConfigSourceError`.

### PHASE W4 — Relocate raw reads
#### W4-01 — tavily
- [ ] `agents/tools.py`: `os.environ["TAVILY_API_KEY"]` → `get_settings().tavily_api_key`.
#### W4-02 — mcp token_env
- [ ] `mcp/connection.py`: resolve `token_env` via a `resolve_secret(name)` helper backed by the injected source (deferred default = os.environ lookup for parity).
#### W4-03 — providers/registry LiteLLM bridge
- [ ] Confirm the bridge reads only `self._settings`; keep the single `os.environ.setdefault` write; remove any direct `getenv`.
#### W4-04 — sandbox
- [ ] `sandbox/manager.py`, `sandbox/isolation.py`: config reads via `Settings` fields (e.g. `sandbox_env_allowlist`, `is_production`).
- **Done:** only `os.environ` *read* of config left in core is inside `EnvConfigSource`; only *write* is the LiteLLM bridge.

### PHASE W5 — Legacy shim relocation
#### W5-01 — env_compat
- [ ] Remove the import-time `apply_legacy_env_aliases()` call; turn it into a deprecated no-op shim; emit the `DeprecationWarning` from `EnvConfigSource` on first mirror.
- **Done:** importing `prismal.core` performs zero `os.environ` writes (snapshot test).

### PHASE W6 — Composition-root integration (Phase R consumer)
#### W6-01 — apply_org_overrides
- [ ] `composition/config_sources.py`: `apply_org_overrides(..., *, source=None)` builds from `build_settings(source)` then applies overrides; no global mutation.
- **Done:** two tenants via `build_runtime(build_settings(MappingConfigSource(...)), mode="context")` share no config state.

### PHASE W7 — Host/dashboard contract
#### W7-01 — Docs
- [ ] `docs/configuration.md`: global injection, secrets-manager chain, per-tenant context, dashboard schema table.

### PHASE W8 — Tests + Docs + Examples
#### W8-01 — Tests
- [ ] Parity (default source == today), sources (mapping/chained precedence/sub-error skip/fake purity), injection (cache invalidation), strict, relocation (tavily/token_env/sandbox/LiteLLM), no-import-time-mutation, isolation, backward-compat.
#### W8-02 — AST guard
- [ ] `tests/unit/core/test_no_env_reads.py`: forbid new direct config `os.getenv` in `prismal/**` (exempt `EnvConfigSource`, LiteLLM bridge).
#### W8-03 — Examples
- [ ] `examples/config_source_env.py`, `examples/config_source_custom.py` (Vault-style).

### HARDENING
- [ ] Coverage ≥ 85% on `config_source*`; `ruff` / `mypy --strict` / `bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CLAUDE.md` (Provider isolation / core one-liners) + `README.md` + `specs/roadmap.md` updated.

---

## 4. Inter-Task Dependencies

```
W1 (port + sources + registry)
   └─▶ W2 (Settings wiring)
         ├─▶ W3 (fields + exception)
         ├─▶ W4 (relocate raw reads)
         │     └─▶ W5 (legacy shim relocation)
         ├─▶ W6 (composition-root integration)
         └─▶ W8 (tests/AST guard/examples)
   W7 (contract docs) ──▶ W8
```

Critical path: **W1 → W2 → W4 → W5 → W8**.

---

## 5. Tasks ↔ Requirements Matrix

| Task | RF covered |
|---|---|
| W1 | RF-CSI-001, RF-CSI-002, RF-CSI-003, RF-CSI-006 |
| W2 | RF-CSI-004, RF-CSI-005 |
| W3 | RF-CSI-009 (tavily field), RF-CSI-010 |
| W4 | RF-CSI-008 |
| W5 | RF-CSI-009 |
| W6 | RF-CSI-007 |
| W7 | RF-CSI-013 |
| W8 | RF-CSI-011, RF-CSI-012, RF-CSI-013 |

Coverage: RF-CSI-001..013 mapped.

---

## 6. Risk Matrix

| Risk | Mitigation | Task |
|---|---|---|
| Behavioural drift vs env reading | Default `EnvConfigSource` + parity test | W1, W8 |
| Missed direct `os.getenv` read | Explicit inventory + AST guard | W4, W8 |
| `filterwarnings=error` on legacy warning | Emit from `EnvConfigSource`, assert exactly one | W5, W8 |
| LiteLLM needs `os.environ` | Keep single `setdefault`, fed from Settings | W4 |
| Per-tenant secret leakage | Context via `build_settings(source)`, no global; isolation test | W6, W8 |
| Over-engineering secrets framework | Core defines only the port; managers as examples | W1, W7 |

---

## 7. Definition of Done (Global for Phase W)

- [ ] `ConfigSourcePort` + `EnvConfigSource`/`MappingConfigSource`/`ChainedConfigSource`/`FakeConfigSource` + registry implemented.
- [ ] `Settings` consumes the source; `env_file` removed; `build_settings`/`reload_settings`; `get_settings()` signature unchanged.
- [ ] All in-scope raw `os.getenv` config reads relocated; legacy mirror in `EnvConfigSource`; no import-time `os.environ` write.
- [ ] `ConfigSourceError` + `config_source_strict` + `tavily_api_key`; composition-root `apply_org_overrides(*, source=)`.
- [ ] Parity, isolation, AST guard, and backward-compat tests green; default deployment byte-for-byte identical.
- [ ] `docs/configuration.md` + two examples; host/dashboard contract documented.
- [ ] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff` / `mypy --strict` / `bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + `specs/roadmap.md` updated.
- [ ] PR merged with review.

---

## 8. Effort Estimate

| Sub-phase | Effort |
|---|---|
| W1 Port + sources + registry | 0.6 wk |
| W2 Settings wiring | 0.5 wk |
| W3 Fields + exception | 0.1 wk |
| W4 Relocate raw reads | 0.6 wk |
| W5 Legacy shim relocation | 0.2 wk |
| W6 Composition-root integration | 0.2 wk |
| W7 Contract docs | 0.3 wk |
| W8 Tests + AST guard + examples | 0.6 wk |
| Hardening | 0.3 wk |
| **Total** | **~3.4 wk** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-07 | Ernesto Crespo | Initial implementation plan — configuration source inversion |
