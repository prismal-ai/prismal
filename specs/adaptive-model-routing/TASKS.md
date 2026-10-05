# Prismal Adaptive Model Routing — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | AMR |
| **Target package version** | `3.16.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/adaptive-model-routing/PLAN.md` |
| **SPEC** | `specs/adaptive-model-routing/SPEC.md` |
| **Architecture** | `specs/adaptive-model-routing/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Seven phases (AMR0–AMR6). AMR0 verifies every **[verify]** fact before code depends on it. AMR1 lands the port and static/budget policies with a parity tier map (zero behavior change even when enabled in `shadow`). AMR2–AMR5 are independently testable and each gated (`routing_policy`, `plan_cache_enabled`, `response_cache_enabled`, `batch_api_enabled`). AMR6 wires Skynet / MoA / eval / supervisor-routed agents and proves the disabled path unchanged. TDD throughout; fakes only (no live LLM) except `live_api`-marked batch smoke tests.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Shipped: TKE1 (`Usage` cache/reasoning fields, cost precedence), GW1 (`GatewayRouter`, `ResolvedModel`, no-double-fallback), DEC1 (`DecisionPort`, fake), C (`BudgetGuard`), V (eval harness reports).
- Reuse, do not fork: `budget/resolve.py` per-run registry shape, `providers/cost.py::compute_cost_usd`, `agents/intent_router.py::match_intent`, `security/` `ActionInterceptor` tool classification, `SecurePromptBuilder`.

| ID | Task (verify before build) | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR0-01 | Verify OmniRouter figures (+6.30 % acc, ≥ 10.15 % cost, 2,700 Qs / 10 models, 81.3 % predictor acc, baselines list) against arXiv 2502.20576 latest + KDD camera-ready; fix PLAN/ARCH text if different | 0.2 d | — | BAT-001 | TODO |
| AMR0-02 | Verify RouteLLM (~2×), FrugalGPT (≤ 98 %), APC (−50.31 % / −27.28 %) and arXiv 2602.18922 (0–12 % hit rate) figures | 0.2 d | — | PLC-001 | TODO |
| AMR0-03 | Verify batch APIs: Anthropic Message Batches + OpenAI Batch discount, windows, limits; OpenRouter `:batch` variant semantics (sync vs async) | 0.2 d | — | BEX-001 | TODO |
| AMR0-04 | Verify LiteLLM batch support per provider at the pinned version; decide adapter path (LiteLLM vs `httpx`) | 0.2 d | AMR0-03 | BEX-001 | TODO |

## 3. Implementation Phases

### PHASE AMR1 — Types, port, static + budget-aware + composite policies

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR1-01 | `core/config.py`: master flag `adaptive_routing_enabled` (default `False`; amended 2026-10-05, M0/X0-04) + `routing_*`, `plan_cache_*`, `response_cache_enabled`, `batch_*` (incl. `batch_price_ratio`, ADR-WAVE-001 §2.3) settings + `_validate_routing` | 0.4 d | — | CFG-001 | TODO |
| AMR1-02 | `core/exceptions.py`: `RoutingError` hierarchy | 0.1 d | — | ERR-001 | TODO |
| AMR1-03 | `routing/types.py` + `routing/ports.py` (`ModelTier`, `TIER_ORDER`, `TaskFeatures`, `ModelChoice`, `RoutingOutcome`, ports) | 0.4 d | AMR1-01 | TYP-001/002 | TODO |
| AMR1-04 | `StaticTierPolicy` + `load_tier_models` + shipped `config/routing_tiers.yaml` parity map (26 agents) | 0.6 d | AMR1-03 | POL-001 | TODO |
| AMR1-05 | `BudgetAwarePolicy` (one-tier downgrade on soft cap, floor clamp, hard cap untouched) | 0.3 d | AMR1-04 | POL-002 | TODO |
| AMR1-06 | `CompositePolicy` (chain order, first-escalation-wins) | 0.2 d | AMR1-05 | POL-003 | TODO |
| AMR1-07 | `routing/features.py::extract_features` (intent precedence, token heuristic, risk map, taint flag) | 0.4 d | AMR1-03 | INT-001 | TODO |
| AMR1-08 | `routing/select.py`: seeding trio, `select_model`, `record_outcome`, `routing_mode=shadow` | 0.5 d | AMR1-06, AMR1-07 | INT-001 | TODO |
| AMR1-09 | `ProviderRegistry.get_llm(..., features=, state=)` per ADR-WAVE-001 (keeps `agent=`/`session_id=`; extends the shared registry contract test); explicit `model=` precedence; fail-open to static | 0.4 d | AMR1-08 | INT-001, TYP-002 | TODO |

**Done when:** settings parse from `PRISMAL_*`; parity map yields identical `ChatLiteLLM.model` for all agents; soft-cap downgrade observable in a unit test; any policy exception returns a `fallback:` static choice.

### PHASE AMR2 — Cascade, adequacy, cache affinity, gateway interplay, floors

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR2-01 | `CascadePolicy.select/on_outcome`: start tier, capability filter, 3 triggers, escalation cap | 0.8 d | AMR1 | CAS-001 | TODO |
| AMR2-02 | `AdequacyChecker` over `DecisionPort` Noul via `SecurePromptBuilder`; risk-aware abstention; tainted ⇒ abstain | 0.6 d | AMR2-01, DEC1 | CAS-001 | TODO |
| AMR2-03 | Cache affinity (`(session_id, agent)` registry + `metadata["routing"]["affinity"]`), one-way escalation | 0.5 d | AMR2-01 | CAS-002 | TODO |
| AMR2-04 | Gateway interplay: omit gateway `models` list + `fallback_model` under cascade; `AUTO` terminal; data-policy eligibility mask | 0.6 d | AMR2-01, GW1 | GW-001 | TODO |
| AMR2-05 | Security floors: clamp, `AUTO` banned for critical, tainted never below static, startup refusal on invalid floor | 0.5 d | AMR1-04 | SEC-001 | TODO |

**Done when:** a fake structured-output failure escalates small → medium; a third failure is accepted at cap; affinity starts the next turn at the escalated tier; a `gw:` call under cascade carries no `models` list.

### PHASE AMR3 — Success predictor + batch assignment

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR3-01 | `TableSuccessPredictor` (Beta posterior, tier-row prior, Score blend, served-model attribution, output-length table) | 0.6 d | AMR1 | PRD-001 | TODO |
| AMR3-02 | `export_predictor_table` from eval reports + versioned `config/routing_predictor/<ver>.json` | 0.6 d | AMR3-01, V | PRD-001 | TODO |
| AMR3-03 | Matrix builder: normalized cost, eligibility masks (floor, capabilities, gateway policy, `AUTO`), waves | 0.5 d | AMR3-01, AMR2-05 | BAT-001 | TODO |
| AMR3-04 | `solve_dual`: per-item argmin, projected subgradient on λ/μ, adaptive step, primal recovery, gap stop | 1.0 d | AMR3-03 | BAT-001 | TODO |
| AMR3-05 | `solve_greedy` (regret-ordered start, Δa/Δc heap upgrades) + max-accuracy infeasible path | 0.6 d | AMR3-03 | BAT-001 | TODO |
| AMR3-06 | Brute-force fixtures (N ≤ 6), hypothesis property tests, determinism tests | 0.7 d | AMR3-04, AMR3-05 | BAT-001/002 | TODO |
| AMR3-07 | Benchmark N=200, K=8 (p95 ≤ 50 ms) | 0.4 d | AMR3-04 | BAT-002 | TODO |

**Done when:** dual solution equals brute-force optimum on all fixtures with feasible instances; infeasible instances return `feasible=False` without raising; repeated runs are identical.

### PHASE AMR4 — Plan cache + response cache

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR4-01 | `PlanCacheKey` canonicalization (slots NFKD/sorted, sha256 overflow) + `tool_scope_hash` | 0.5 d | AMR1 | PLC-001 | TODO |
| AMR4-02 | Backends: in-memory LRU + SQLite (`plan_cache_path`) | 0.6 d | AMR4-01 | PLC-001 | TODO |
| AMR4-03 | Eligibility: intent allowlist, `ActionInterceptor` write/exec classification, taint | 0.4 d | AMR4-01 | PLC-001 | TODO |
| AMR4-04 | Lookup adaptation via small tier + `SecurePromptBuilder`; Kahn DAG revalidation; failure ⇒ miss | 0.6 d | AMR4-02 | PLC-001 | TODO |
| AMR4-05 | TTL, version-keyed structural miss, `invalidate()` sweep, tenant isolation | 0.4 d | AMR4-02 | PLC-002 | TODO |
| AMR4-06 | Wire into `llm_compiler` / plan-and-execute planner nodes (gated) | 0.5 d | AMR4-04 | PLC-001 | TODO |
| AMR4-07 | `ResponseCache` (exact key; bypass on tools, taint, temperature > 0) | 0.4 d | AMR1 | RSC-001 | TODO |

**Done when:** a repeated read-only intent skips the planner call; a tool-catalog change forces a miss; an `org_id` mismatch never hits.

### PHASE AMR5 — BatchExecutor

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR5-01 | `providers/batch_adapters.py`: `BatchApiPort` + Anthropic adapter | 0.7 d | AMR0-04 | BEX-001 | TODO |
| AMR5-02 | OpenAI Batch adapter | 0.5 d | AMR5-01 | BEX-001 | TODO |
| AMR5-03 | OpenRouter `:batch` adapter (scope per AMR0-03 outcome; may reduce to sync passthrough) | 0.4 d | AMR5-01 | BEX-001 | TODO |
| AMR5-04 | `BatchExecutor.run`: family grouping, backoff with jitter (fake clock), deadline, sync fallback | 0.8 d | AMR5-01 | BEX-001 | TODO |
| AMR5-05 | Batch-price metering (`compute_cost_usd(..., batch=True)`) into `CostMeter` | 0.3 d | AMR5-04, TKE1 | BEX-001 | TODO |
| AMR5-06 | Exclude tainted / HITL-gated requests; data-policy check before upload | 0.3 d | AMR5-04 | BEX-001 | TODO |

**Done when:** a fake adapter job completes with results metered at batch price; a failing job falls back to sync; tainted requests are rejected before submission.

### PHASE AMR6 — Integration, observability, docs

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| AMR6-01 | `monitoring/otel.py`: 5 instruments + `prismal.routing.select` span | 0.3 d | AMR1 | OTEL-001 | TODO |
| AMR6-02 | Skynet: per-round `assign()` → `SwarmWorker` model choice | 0.6 d | AMR3 | INT-002 | TODO |
| AMR6-03 | MoA: proposers via `assign()`, aggregator static | 0.4 d | AMR3 | INT-002 | TODO |
| AMR6-04 | Eval harness `--routing`: cost/quality/savings report + −40 % / ≤ 1 pt gate | 0.6 d | AMR3-02 | INT-002 | TODO |
| AMR6-05 | Supervisor-routed agents adopt `get_llm(features=…)` (one line each, gated) | 0.6 d | AMR2 | INT-001 | TODO |
| AMR6-06 | Snapshot: all flags off ⇒ graph + recorded `ChatLiteLLM` args identical | 0.4 d | AMR6-05 | SNP-001 | TODO |
| AMR6-07 | Integration (fakes): cascade escalation, Skynet batch round, gateway no-`models` under cascade, plan-cache hit | 0.6 d | AMR6-02..05 | CAS-001, BAT-001, GW-001, PLC-001 | TODO |
| AMR6-08 | `docs/routing.md`, `examples/adaptive_routing.py`, notebook | 0.6 d | AMR6-07 | — | TODO |
| AMR6-09 | AST guard (no provider SDK in `routing/`), README/CHANGELOG (planned) | 0.4 d | AMR6-08 | INT-001 | TODO |

**Done when:** `uv run pytest -m unit` green; ruff / mypy --strict / bandit clean; coverage ≥ 80 % on `prismal/routing/`; snapshot passes; eval gate met in shadow comparison.

## 4. Test Inventory

| Test module | Covers |
|---|---|
| `tests/unit/core/test_config_routing.py` · `tests/unit/routing/test_types_ports.py` | AMR1-01, AMR2-05 · AMR1-02, AMR1-03 |
| `tests/unit/routing/test_static_budget_composite.py` | AMR1-04..06 |
| `tests/unit/routing/test_features_select.py` | AMR1-07..09 |
| `tests/unit/routing/test_cascade.py` · `test_gateway_interplay.py` · `test_security_floors.py` | AMR2-01..03 · AMR2-04 · AMR2-05 |
| `tests/unit/routing/test_predictor.py` | AMR3-01, AMR3-02 |
| `tests/unit/routing/test_batch_assignment.py` · `test_batch_assignment_bench.py` (`slow`) | AMR3-03..06 · AMR3-07 |
| `tests/unit/routing/test_plan_cache.py` / `test_response_cache.py` | AMR4-01..07 |
| `tests/unit/routing/test_batch_executor.py`, `tests/unit/providers/test_batch_adapters.py` | AMR5-01..06 |
| `tests/unit/agents/test_graph_snapshot_routing.py` · `tests/integration/routing/test_routing_end_to_end.py` | AMR6-06 · AMR6-02..04, AMR6-07 |
| `tests/unit/routing/test_no_provider_imports.py` | AMR6-09 |
| `tests/live/test_batch_live.py` (`live_api`) | AMR5-01..03 smoke |

## 5. Risk Register

| Risk | Mitigation |
|---|---|
| Quality loss hidden by aggregate metrics | Per-intent quality columns in eval report; shadow before enforce |
| Cache-hit ratio drops after cascade | Affinity (AMR2-03); TKE1 cache metric watched in shadow |
| Solver non-convergence on adversarial instances | Primal recovery + greedy + max-accuracy fallbacks; never raises |
| OpenRouter `:batch` not a true async batch | AMR0-03 decides; AMR5-03 may become sync passthrough |
| Stale predictor table | Versioned file, online `update()`, re-export each eval run |

## 6. Definition of Done

- [ ] All MUST requirements implemented and traced to tasks + tests (§8).
- [ ] All AMR0 verification tasks closed; **[verify]** markers resolved or retained with dated notes.
- [ ] Flags off ⇒ snapshot identical; shadow mode ⇒ outputs identical, savings counter populated.
- [ ] Solver matches brute force on fixtures; deterministic; benchmark met.
- [ ] Docs, example, notebook published; README/CHANGELOG marked planned until ship.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| AMR0 | Verify volatile facts | 0.8 d |
| AMR1 | Types, port, static/budget/composite, wiring | 3.3 d |
| AMR2 | Cascade, adequacy, affinity, gateway interplay, floors | 3.0 d |
| AMR3 | Predictor + batch assignment | 4.4 d |
| AMR4 | Plan cache + response cache | 3.4 d |
| AMR5 | BatchExecutor | 3.0 d |
| AMR6 | Integration, observability, docs | 4.5 d |
| **Total** | | **22.4 d** |

## 8. Traceability Matrix

| SPEC id | Tasks | Tests |
|---|---|---|
| SPEC-AMR-TYP-001 / TYP-002 | AMR1-03, AMR1-09 | `test_types_ports.py`, `test_features_select.py` |
| SPEC-AMR-POL-001 / 002 / 003 | AMR1-04, AMR1-05, AMR1-06 | `test_static_budget_composite.py` (incl. 26-agent parity) |
| SPEC-AMR-CAS-001 / CAS-002 | AMR2-01..03, AMR6-07 | `test_cascade.py`, `test_routing_end_to_end.py` |
| SPEC-AMR-PRD-001 | AMR3-01, AMR3-02 | `test_predictor.py` |
| SPEC-AMR-BAT-001 | AMR3-03..06, AMR6-02, AMR6-03 | `test_batch_assignment.py`, `test_routing_end_to_end.py` |
| SPEC-AMR-BAT-002 | AMR3-06, AMR3-07 | `test_batch_assignment.py`, `test_batch_assignment_bench.py` |
| SPEC-AMR-PLC-001 / PLC-002 | AMR4-01..06 | `test_plan_cache.py` |
| SPEC-AMR-RSC-001 (SHOULD) | AMR4-07 | `test_response_cache.py` |
| SPEC-AMR-BEX-001 | AMR0-03/04, AMR5-01..06 | `test_batch_executor.py`, `test_batch_adapters.py` |
| SPEC-AMR-INT-001 | AMR1-07..09, AMR6-05, AMR6-09 | `test_features_select.py`, `test_no_provider_imports.py` |
| SPEC-AMR-INT-002 | AMR6-02..04 | `test_routing_end_to_end.py` |
| SPEC-AMR-GW-001 | AMR2-04, AMR6-07 | `test_gateway_interplay.py` |
| SPEC-AMR-SEC-001 | AMR2-05 | `test_security_floors.py`, `test_config_routing.py` |
| SPEC-AMR-CFG-001 / ERR-001 | AMR1-01, AMR1-02 | `test_config_routing.py`, `test_types_ports.py` |
| SPEC-AMR-OTEL-001 | AMR6-01 | `test_routing_end_to_end.py` (counter assertions) |
| SPEC-AMR-SNP-001 | AMR6-06 | `test_graph_snapshot_routing.py` |
