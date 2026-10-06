# Prismal LLM Gateway Providers — Implementation Plan (TASKS)

## Metadata
| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | GW |
| **Target package version** | `3.14.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/llm-gateway-providers/PLAN.md` |
| **SPEC** | `specs/llm-gateway-providers/SPEC.md` |
| **Architecture** | `specs/llm-gateway-providers/ARCHITECTURE.md` |

## 1. Implementation Summary

GW is planned as one verification phase (GW0) and six build phases (GW1–GW6), each independently testable and gated behind `llm_gateways_enabled=False` plus a per-gateway `*_enabled` flag, so `main` stays green and the 26 agents are unaffected until an operator opts in. It reuses LiteLLM as the only transport (a `ChatLiteLLM` subclass, no fork), `httpx` for gateway REST endpoints, `CostMeter` / `BudgetGuard` / `Degradation` (C) with the TKE1 cost precedence, `CredentialVault` (IDN) + `ConfigSourcePort` (W) + `build_runtime` (R) for per-tenant secrets, and `OTelManager`. No new runtime dependency.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. Implemented 2026-10-05 for v3.14.0 (GW0 facts in `VERIFY.md`); GW6-07 lives in another repository.

## 2. Prerequisites

- TKE1 merged (`Usage` cache/reasoning fields, `compute_cost_usd(..., reported_cost_usd=)`, source `"gateway"`). Reuse, do not fork: `providers/{registry,cost}.py`, `budget/{meter,usage}.py`, `composition/runtime.py`, `identity/` vault, `monitoring/otel.py`.
- **Verify before build (GW0).** Every **[verify]** fact in SPEC/ARCHITECTURE is re-checked against live docs or APIs. The outcome is recorded as a short note in `specs/llm-gateway-providers/VERIFY.md`, and the spec is amended if any fact changed. A dependent task does not start until its GW0 task is `DONE`.

### PHASE GW0 — Verify before build
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW0-01 | OpenRouter — **mostly verified 2026-10-04 against openrouter.ai/docs** (headers incl. `X-OpenRouter-Categories`, `provider` fields, always-on usage fields, variants, sticky `session_id`, plugins, 402 `limit_source`). Remaining: `:free`/`:batch` availability under ZDR; re-check docs at build time | 0.1 d | — | OR-001 | DONE |
| GW0-02 | OpenRouter `/api/v1/key`, `/api/v1/generation`, 402 `limit_source` and `:free` limits — **verified 2026-10-04**; only re-run the contract fixtures at build time | 0.1 d | — | OR-001, QUOTA-001 | DONE |
| GW0-03 | LiteLLM: `_hidden_params` billed cost for OpenRouter; `langchain_litellm.ChatLiteLLM` raw-response hook name (`_create_chat_result`) for sync, async and streaming (final-chunk usage) | 0.3 d | — | REG-001, COST-001 | DONE |
| GW0-04 | LiteLLM: `openai/responses/<id>` and `anthropic/<id>` with a custom `api_base` | 0.2 d | — | OC-001 | DONE |
| GW0-05 | OpenCode: Zen and Go base URLs; `/v1/models` shape (does it expose protocol?); model id format `opencode/<id>` / `opencode-go/<id>`; 5 h (20 %) / weekly (50 %) windows; quota API absence; "coding agents" ToS scope and validated clients | 0.2 d | — | OC-001, QUOTA-001 | DONE |
| GW0-06 | OpenCode retention exceptions: Grok 30 days, Muse Spark training, DeepSeek ZDR "through October 2026" (expires during this release window) | 0.1 d | — | OC-001 | DONE |
| GW0-07 | OmniRoute: health/version endpoint or header; 3.8.49 contains the CVE-2026-88062 fix (GHSA-hf57-cqmx-p4gr); official npm/Docker names; combo naming for the deny-list | 0.2 d | — | OMNI-001 | DONE |
| GW0-08 | IDN `CredentialVault` accessor signature and scoped-key naming (`<ref>@<org>`); OpenRouter `/api/v1/systemone` base URL (DEC forward ref) | 0.1 d | — | COMP-001, CFG-001 | DONE |

**Done when:** `VERIFY.md` lists each fact with source URL, date and outcome (confirmed / changed); SPEC amended for every changed fact.

## 3. Implementation Phases

### PHASE GW1 — Port, generic adapter, router, registry wiring
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW1-01 | `core/config.py`: `llm_gateways_enabled`, `llm_gateway_agent_map`, `llm_gateway_openai_compatible`, `llm_gateway_tenant_key_fallback`, reconcile/poll settings + `_validate_llm_gateways` | 0.4 d | — | CFG-001 | DONE |
| GW1-02 | `core/exceptions.py`: `GatewayError` hierarchy | 0.2 d | — | ERR-001 | DONE |
| GW1-03 | `gateways/base.py`: `WireProtocol`, `GatewayCapabilities`, `GatewayProfile`, `ResolvedModel` (secret `repr=False`), telemetry value objects, `GatewayPort` | 0.6 d | GW1-02 | BASE-001..003 | DONE |
| GW1-04 | `gateways/policy.py`: `GatewayDataPolicy`, `ModelRef`, `parse_model_ref` | 0.4 d | GW1-03 | POL-001 | DONE |
| GW1-05 | `gateways/openai_compatible.py`: generic adapter + `SecretResolver` | 0.6 d | GW1-04 | GEN-001 | DONE |
| GW1-06 | `gateways/router.py`: `GatewayRouter` (precedence, no I/O), `build_gateway_router`, ContextVar/global accessors | 0.7 d | GW1-05 | RTR-001 | DONE |
| GW1-07 | `gateways/chat_model.py`: `GatewayChatLiteLLM` metadata capture into `response_metadata["prismal_gateway"]` (sync / async / stream) | 0.8 d | GW0-03, GW1-03 | REG-001 | DONE |
| GW1-08 | `providers/registry.py` per ADR-WAVE-001 (pipeline steps 2–4, 7; extend the shared registry contract test incl. TKE2-wrapper combination): `get_llm(..., agent=, session_id=)`, `ProviderRegistry(gateway_router=)`, no-double-fallback in `get_llm_with_fallback` (`max_retries=0`, `exceptions_to_handle`) | 0.7 d | GW1-06, GW1-07 | REG-001 | DONE |
| GW1-09 | `budget/usage.py`: `extract_gateway_meta()` (pure dict read, never raises) | 0.2 d | GW1-03 | COST-001 | DONE |
| GW1-10 | Snapshot test (flag off ⇒ graph identical), `get_llm()` kwargs contract test, no-`os.environ`-write test | 0.6 d | GW1-08 | SNAP-001, REG-001 | DONE |

**Done when:** a `gw:` ref to a generic gateway produces the expected `ChatLiteLLM` kwargs; non-`gw:` refs and the flag-off path are byte-for-byte unchanged; `resolve()` performs zero requests.

### PHASE GW2 — OpenRouter
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW2-01 | `openrouter_*` settings (incl. `openrouter_systemone_base_url`) + validation | 0.2 d | GW1-01, GW0-08 | CFG-001 | DONE |
| GW2-02 | `OpenRouterGateway.resolve`: `openrouter/<vendor>/<model>[:variant]`, attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`, optional `X-OpenRouter-Categories`), hashed sticky `session_id` | 0.6 d | GW1-05, GW0-01 | OR-001 | DONE |
| GW2-03 | `build_provider_object` / `build_extra_body`: deny + ZDR defaults, `require_parameters` when tools/schema bound, `sort`/`max_price`/`only`/`ignore` | 0.6 d | GW2-02 | OR-001 | DONE |
| GW2-04 | `models` fallback list ⇒ `owns_model_fallback`; served-model logging; `gateway_fallbacks_total` | 0.5 d | GW2-03, GW1-08 | OR-001, REG-001 | DONE |
| GW2-05 | Variant allow-list + `openrouter/auto` gate (fail-closed) | 0.3 d | GW2-02 | OR-001 | DONE |
| GW2-06 | `reported_cost()` from `usage.cost` / `_hidden_params`; BYOK `upstream_cost_usd` | 0.4 d | GW1-07 | OR-001, COST-001 | DONE |
| GW2-07 | `quota()` via `GET /api/v1/key`; `fetch_generation()` via `GET /api/v1/generation` | 0.4 d | GW0-02 | OR-001, REC-001 | DONE |
| GW2-08 | 402 → `GatewayPaymentRequired(reason)` from `error.metadata.limit_source` (non-retryable); 429 → `gateway_errors_total{kind}` | 0.4 d | GW0-02 | OR-001, ERR-001 | DONE |
| GW2-09 | Request-capture tests (`httpx.MockTransport` / LiteLLM mock) for headers and `extra_body` | 0.3 d | GW2-03 | OR-001 | DONE |
| GW2-10 | `openrouter_plugins` allow-list (`response-healing`, `context-compression`, `pdf`; default `[]`) and `openrouter/auto` wildcards + cost tier | 0.3 d | GW2-03, GW2-05 | OR-001, CFG-001 | DONE |

**Done when:** every captured OpenRouter request carries `provider.data_collection="deny"` and `provider.zdr=true` by default; `models[]` disables prismal fallback; a 402 is typed and issued exactly once.

### PHASE GW3 — OpenCode Zen/Go + QuotaLedger
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW3-01 | `gateways/quota.py`: `QuotaWindow`, `QuotaVerdict`, `QuotaLedger` (rolling 5 h / week / month / day, fake clock, per `(org, gateway)`) | 0.8 d | GW1-03 | QUOTA-001 | DONE |
| GW3-02 | `opencode_*` settings + `load_opencode_model_protocols` + `config/opencode_models.yaml` | 0.4 d | GW0-05 | CFG-001, OC-001 | DONE |
| GW3-03 | `OpenCodeGateway.resolve`: Zen/Go base, protocol → `openai/` / `anthropic/` / `openai/responses/` | 0.6 d | GW3-02, GW0-04 | OC-001 | DONE |
| GW3-04 | Headers: `User-Agent: prismal/<ver>`, `x-opencode-session` = hashed `(org, session)` | 0.2 d | GW3-03 | OC-001 | DONE |
| GW3-05 | ToS guard (`opencode_go_allowed_agents`, `agent=None` denied) + retention deny-list | 0.4 d | GW3-03, GW0-06 | OC-001 | DONE |
| GW3-06 | `refresh_protocols()` from `/v1/models` (fail-open) | 0.3 d | GW3-03 | OC-001 | DONE |
| GW3-07 | `GatewayChatLiteLLM` preflight: refuse (no network) / downgrade via `opencode_go_downgrade_model` | 0.5 d | GW3-01, GW1-07 | QUOTA-001 | DONE |
| GW3-08 | OpenRouter `:free` per-day request window via the ledger | 0.3 d | GW3-01, GW2-05 | OR-001, QUOTA-001 | DONE |
| GW3-09 | Tests: protocol table, guard zero-request, ledger window arithmetic, preflight | 0.3 d | GW3-07 | OC-001, QUOTA-001 | DONE |

**Done when:** a non-coding agent calling `gw:opencode-go/*` raises `GatewayPolicyError` with zero requests; an exhausted 5 h window refuses before network; an unknown model id fails at resolve.

### PHASE GW4 — OmniRoute + compliance guard
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW4-01 | `omniroute_*` settings + validation (semver floor, attestation enum) | 0.2 d | GW1-01 | CFG-001 | DONE |
| GW4-02 | `OmniRouteGateway.resolve` + mandatory API key | 0.3 d | GW1-05 | OMNI-001 | DONE |
| GW4-03 | `detect_version()` + `astartup_check()` version floor; unknown version refused | 0.5 d | GW0-07, GW4-02 | OMNI-001 | DONE |
| GW4-04 | Attestation matrix (`api_keys_only` / `subscription_oauth` × allow flag) + combo deny-list | 0.4 d | GW4-03 | OMNI-001 | DONE |
| GW4-05 | Public plain-HTTP base URL warning + counter | 0.2 d | GW4-02 | OMNI-001 | DONE |
| GW4-06 | Tests: `3.8.50` refused, `3.8.51` accepted (GW0-07), unknown refused, attestation matrix | 0.3 d | GW4-04 | OMNI-001 | DONE |
| GW4-07 | Compliance section draft for `docs/gateways.md` (ToS, CVE, official repo only, never public) | 0.2 d | GW4-04 | OMNI-001 | DONE |

**Done when:** OmniRoute cannot be enabled below the version floor or without an attestation, and every refusal is counted.

### PHASE GW5 — Cost reconciliation, quota → BudgetGuard, OTel
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW5-01 | `providers/cost.py`: `cost_mode`, `"subscription"`, `equivalent_usd` on top of TKE1 precedence | 0.4 d | TKE1, GW1-09 | COST-001 | DONE |
| GW5-02 | `CostMeter.record_response`: pass gateway meta, tag `gateway` / `served_model` | 0.4 d | GW5-01 | COST-001 | DONE |
| GW5-03 | Ledger `record()` from the meter for subscription calls | 0.3 d | GW5-02, GW3-01 | QUOTA-001 | DONE |
| GW5-04 | `gateways/reconcile.py`: `CostReconciler` (bounded queue, delay, drift histogram, fail-open) | 0.6 d | GW2-07 | REC-001 | DONE |
| GW5-05 | Quota poller → `gateway_quota_remaining` gauge; `Degradation(reduce=True)` on downgrade | 0.4 d | GW3-07 | QUOTA-001 | DONE |
| GW5-06 | `monitoring/otel.py`: register the six `prismal.gateway_*` metrics; model-label cardinality bucketing | 0.4 d | — | OTEL-001 | DONE |
| GW5-07 | `RunSummary` gains `gateway`, `served_model` | 0.3 d | GW5-02 | COST-001 | DONE |
| GW5-08 | Tests: cost precedence matrix, drift, gauge, metric-once | 0.4 d | GW5-06 | COST-001, OTEL-001 | DONE |

**Done when:** an OpenRouter call meters with `source="gateway"`; a Go call meters `cost_usd=0` with `equivalent_usd>0` and decrements the ledger; drift is recorded.

### PHASE GW6 — Integration, live tests, docs, release
| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| GW6-01 | `composition/runtime.py`: `build_gateway_router` in `build_runtime(org_id)`, `RuntimeContext.gateways`, `astartup`, `aclose` drains reconciler | 0.6 d | GW1-06, GW4-03 | COMP-001 | DONE |
| GW6-02 | Per-tenant key tests (`FakeVault`, two tenants, missing key ⇒ disabled, no global fallback) | 0.5 d | GW6-01 | COMP-001 | DONE |
| GW6-03 | Secret hygiene: sentinel key never in logs, exceptions, `repr`, URLs | 0.3 d | GW6-01 | BASE-001, COMP-001 | DONE |
| GW6-04 | `@pytest.mark.live_api` tests: OpenRouter `:free` call (usage + cost + served model); optional Zen | 0.4 d | GW2-06 | OR-001, OC-001 | DONE |
| GW6-05 | `docs/gateways.md`: routing-policy split, no double fallback, data retention, ToS, quotas, per-tenant keys | 0.6 d | GW4-07 | all | DONE |
| GW6-06 | `examples/gateways_openrouter.py`, `gateways_opencode.py`, `gateways_omniroute.py` | 0.4 d | GW6-01 | — | DONE |
| GW6-07 | Notebook in `prisma-notebooks` (OpenRouter `:free` + cost/quota readout) | 0.3 d | GW6-06 | — | BLOCKED (separate repository; not part of this change) |
| GW6-08 | `README.md`, `CHANGELOG.md`, `specs/roadmap.md` entries (planned → shipped on release) | 0.2 d | GW6-05 | — | DONE |
| GW6-09 | Eval harness: one agent through a fake gateway; no solve-rate regression; tokens/cost reported | 0.4 d | GW5-02 | SNAP-001 | DONE |

**Done when:** `uv run pytest -m unit` is green; `ruff`, `mypy --strict` and `bandit` are clean; coverage ≥ 80 % on `providers/gateways/`; snapshot and contract tests pass with the flag off.

## 4. Test Inventory (planned)
| Test module | Covers |
|---|---|
| `tests/unit/core/test_{config,exceptions}_gateways.py` | GW1-01, GW1-02, GW2-01, GW3-02, GW4-01 |
| `tests/unit/providers/gateways/test_{base,policy,openai_compatible,router}.py` | GW1-03..06 |
| `tests/unit/providers/gateways/test_chat_model.py` | GW1-07, GW3-07 |
| `tests/unit/providers/test_registry_gateways.py` + `gateways/test_no_environ_writes.py` | GW1-08, GW1-10, GW2-04 |
| `tests/unit/agents/test_graph_snapshot_gateways.py` | GW1-10 |
| `tests/unit/providers/gateways/test_{openrouter,opencode,omniroute}.py` | GW2-02..09, GW3-02..06/09, GW4-02..06 |
| `tests/unit/providers/gateways/test_{quota_ledger,reconcile}.py` | GW3-01, GW3-08, GW5-03, GW5-04 |
| `tests/unit/budget/test_{usage_gateway_meta,meter_gateway}.py` + `providers/test_cost_gateway.py` | GW1-09, GW5-01, GW5-02, GW5-07 |
| `tests/unit/monitoring/test_otel_gateways.py` | GW5-05, GW5-06, GW5-08 |
| `tests/integration/composition/test_runtime_gateways.py` + `gateways/test_secret_hygiene.py` | GW6-01..03 |
| `tests/live/test_gateways_live.py` (`live_api`) · `tests/eval/test_gateway_eval.py` | GW6-04, GW6-09 |

## 5. Risk Register (implementation)
| Risk | Mitigation |
|---|---|
| A **[verify]** fact is wrong at build time | GW0 gates the dependent tasks; volatile values live in config |
| `langchain_litellm` hook changes between versions | GW0-03 pins the hook; GW1-07 test fails loudly; fall back to `usage.cost` from `response_metadata` |
| Double fallback re-introduced by a later caller | DD-GW-003 contract test (GW1-08) on `get_llm_with_fallback` |
| ZDR default makes popular models unroutable | Clear `GatewayPolicyError` hint; docs list the relax setting; shadow rollout first |
| Ledger drift vs OpenCode's real windows | Ratios are config; warn-mode rollout; open question on persistence |
| Metric label cardinality from served models | Bucketing to `other` after 200 values (GW5-06) |

## 6. Definition of Done (feature)

- [ ] All MUST requirements implemented and tested; SHOULD (REC-001, OTEL-001) implemented; every GW0 task `DONE` and `VERIFY.md` committed.
- [ ] Flag off ⇒ graph snapshot and `get_llm()` kwargs unchanged; no double fallback; privacy defaults on every OpenRouter request; ToS/compliance guards fail closed with zero requests.
- [ ] No secret in `os.environ`, logs, exceptions or URLs; provider-import AST guard and `test_no_env_reads.py` pass.
- [ ] `ruff`, `mypy --strict` and `bandit` clean; unit suite green; `docs/gateways.md` published.

## 7. Effort Summary
| Phase | Focus | Est. |
|---|---|---|
| GW0 | Verify before build | ~1.3 d |
| GW1 | Port, generic adapter, router, registry | ~5.2 d |
| GW2 | OpenRouter | ~4.0 d |
| GW3 | OpenCode Zen/Go + QuotaLedger | ~3.8 d |
| GW4 | OmniRoute + compliance | ~2.1 d |
| GW5 | Cost reconciliation, quota → Budget, OTel | ~3.2 d |
| GW6 | Integration, live tests, docs | ~3.7 d |
| **Total** | | **~23.3 d** |

## 8. Traceability Matrix
| SPEC id | Tasks | Tests |
|---|---|---|
| SPEC-GW-BASE-001..003, POL-001, GEN-001 | GW1-03, GW1-04, GW1-05, GW6-03 | `test_base.py`, `test_policy.py`, `test_openai_compatible.py`, `test_secret_hygiene.py` |
| SPEC-GW-OR-001 | GW0-01/02, GW2-02..09, GW3-08, GW6-04 | `test_openrouter.py`, `test_quota_ledger.py`, `test_gateways_live.py` |
| SPEC-GW-OC-001 | GW0-04..06, GW3-02..06, GW3-09 | `test_opencode.py` |
| SPEC-GW-OMNI-001 | GW0-07, GW4-02..07 | `test_omniroute.py` |
| SPEC-GW-RTR-001 | GW1-06 | `test_router.py` |
| SPEC-GW-REG-001 | GW0-03, GW1-07, GW1-08, GW1-10, GW2-04 | `test_chat_model.py`, `test_registry_gateways.py`, `test_no_environ_writes.py` |
| SPEC-GW-QUOTA-001 | GW3-01, GW3-07, GW3-08, GW5-03, GW5-05 | `test_quota_ledger.py`, `test_chat_model.py`, `test_otel_gateways.py` |
| SPEC-GW-COST-001 | GW1-09, GW2-06, GW5-01, GW5-02, GW5-07 | `test_usage_gateway_meta.py`, `test_cost_gateway.py`, `test_meter_gateway.py` |
| SPEC-GW-REC-001 | GW2-07, GW5-04 | `test_reconcile.py` |
| SPEC-GW-COMP-001 | GW0-08, GW6-01..03 | `test_runtime_gateways.py`, `test_secret_hygiene.py` |
| SPEC-GW-CFG-001, ERR-001 | GW1-01, GW1-02, GW2-01, GW2-08, GW3-02, GW4-01 | `test_config_gateways.py`, `test_exceptions_gateways.py`, `test_openrouter.py` |
| SPEC-GW-OTEL-001 | GW5-05, GW5-06, GW5-08 | `test_otel_gateways.py` |
| SPEC-GW-SNAP-001 | GW1-10, GW6-09 | `test_graph_snapshot_gateways.py`, `test_gateway_eval.py` |
