# Prismal Token Efficiency — Implementation Plan (TASKS)
## Metadata
| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | TKE |
| **Target package version** | `3.13.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/token-efficiency/PLAN.md` |
| **SPEC** | `specs/token-efficiency/SPEC.md` |
| **Architecture** | `specs/token-efficiency/ARCHITECTURE.md` |

---

## 1. Implementation Summary
Ten phases (TKE0 verify → TKE9 integration) plus the **optional P3** TKE10, each independently testable and gated behind `token_efficiency_enabled=False` and its own sub-flag, so `main` stays green and the 26 agents are unaffected. **Measure first:** TKE1 lands and baselines are committed before any optimization. TKE3/TKE4 are deltas to Loop Hardening with LH-preserving defaults. Strict TDD; every MUST maps to ≥ 1 task and ≥ 1 test (§8). Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. All tasks `TODO`. Phase order (amended 2026-10-05, M0/X0-02 — the previous order was not executable because TKE5-02 needs TKE4-02 and TKE4-04 needs TKE5-01): TKE0 → TKE1 → **foundations** {TKE2-06 `stable_json`, TKE4-02 `ArtifactStore`, TKE5-01 `token_estimate`} → {rest of TKE2, rest of TKE5, TKE6} → TKE3 (needs TKE2-07 tail block) → rest of TKE4 → {TKE7, TKE8} → TKE9 → TKE10. `get_llm` / wrapper / cost-precedence work (TKE1-03, TKE2-05, TKE6-02) follows `docs/adr/ADR-WAVE-001-llm-construction-contract.md`.
## 2. Prerequisites

- Reuse, do not fork: `budget/{types,usage,meter,resolve}.py`, `providers/{cost,registry}.py`, `agents/context_compaction.py` (`_RUN_ENGINES` pattern), `agents/extension/{ports,providers}.py`, `agents/tool_registry.py`, `security/prompt_builder.py`, `monitoring/otel.py`, `eval/`. LH shipped (3.7.0) and its tests green — TKE3/TKE4 modify LH code paths.
- **Verify before build** (every **[verify]** fact):

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE0-01 | Verify usage field names surfaced through `ChatLiteLLM` (`usage_metadata.input_token_details`, `token_usage.prompt_tokens_details.cached_tokens`, `cache_write_tokens`, Anthropic `cache_read_input_tokens`/`cache_creation_input_tokens`, `reasoning_tokens`) for Anthropic, OpenAI, OpenRouter, DeepSeek, Gemini; record fixtures | 0.3 d | — | ACC-002 | TODO |
| TKE0-02 | Verify LiteLLM cache pricing API (`completion_cost(response)` availability from an `AIMessage`; `cost_per_token(cache_read_input_tokens=, cache_creation_input_tokens=)`) and whether `prompt_tokens` includes cached tokens per provider | 0.2 d | — | ACC-003 | TODO |
| TKE0-03 | Verify `cache_control` content-block + tool passthrough via `ChatLiteLLM`, OpenAI `prompt_cache_key` / retention param name, Anthropic max 4 breakpoints, 20-block lookback, min cacheable prefix per model; write `config/prompt_cache.yaml` | 0.4 d | — | CCH-001/002/003 | TODO |
| TKE0-04 | Verify Anthropic native tool search (`defer_loading`) via LiteLLM/OpenRouter; record capability table | 0.2 d | — | DEF-001 | TODO |
| TKE0-05 | Verify `litellm.supports_reasoning` and `reasoning_effort` passthrough per model family | 0.1 d | — | EFF-001 | TODO |
| TKE0-06 | Confirm `SecurePromptBuilder` canary placement + existing leak-check API; `grep cache_control` still 0; inventory per-request values (timestamps, ids) in agent system prompts incl. supervisor `build_system_prompt` | 0.3 d | — | LAY-001 | TODO |
## 3. Implementation Phases
### PHASE TKE1 — Cache-aware accounting (measure first)
**Done when:** cache/reasoning fields populated for all TKE0-01 fixtures; defaults reproduce v3.12.0 `CostEstimate`/`TokenCounts`; baseline file committed.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE1-01 | `budget/types.py`: `TokenCounts`/`Usage` cache, reasoning, `cost_source` fields; `__add__`; `uncached_prompt_tokens`, `cache_hit_ratio` | 0.3 d | — | ACC-001 | TODO |
| TKE1-02 | `budget/usage.py`: new-field extraction precedence + Anthropic-raw normalization | 0.5 d | TKE0-01, TKE1-01 | ACC-002 | TODO |
| TKE1-03 | `providers/cost.py`: cache kwargs, `reported_cost_usd`, `response`, `"gateway"` source, table `cache_read`/`cache_write` | 0.6 d | TKE0-02 | ACC-003 | TODO |
| TKE1-04 | `budget/meter.py`: pass cache tokens (master-gated), `reported_cost_usd` kwarg, kind-split metrics | 0.4 d | TKE1-02/03 | ACC-004 | TODO |
| TKE1-05 | `core/config.py` `token_efficiency_enabled` + all TKE settings + `_validate_token_efficiency`; `core/exceptions.py` `TokenEfficiencyError` tree | 0.3 d | — | CFG-001, ERR-001 | TODO |
| TKE1-06 | `monitoring/otel.py`: register all TKE metrics | 0.2 d | — | OTEL-001 | TODO |
| TKE1-07 | `RunSummary` cache fields + aggregation in `DefaultObservabilityProvider` | 0.3 d | TKE1-01 | ACC-005 | TODO |
| TKE1-08 | `eval/metrics_tokens.py`: `TokenReport`, `build_token_report`, `load_baseline`, `check_token_regression`; capture `tests/eval/baselines/tokens.json` | 0.6 d | TKE1-04/07 | ACC-005 | TODO |
| TKE1-09 | Tests: extraction fixtures per provider, pricing precedence, v3.12.0 contract for defaults | 0.6 d | TKE1-01..04 | ACC-001..004 | TODO |
### PHASE TKE2 — Prompt caching + cache-stable layout
**Done when:** two consecutive supervisor turns produce byte-identical prefixes; fake-provider hit ratio > 0 with flag on; red-team parity green.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE2-01 | `providers/prompt_cache.py`: `CacheFamily`, `detect_cache_family` | 0.4 d | — | CCH-001 | TODO |
| TKE2-02 | `CachePolicy` + `load_cache_policy` from `config/prompt_cache.yaml` | 0.3 d | TKE0-03 | CCH-001 | TODO |
| TKE2-03 | `apply_cache_policy` Anthropic: tool/system/moving message breakpoints, cap, lookback, min prefix | 0.8 d | TKE2-02 | CCH-002 | TODO |
| TKE2-04 | `apply_cache_policy` OpenAI `prompt_cache_key` + retention; other families no-op; tenant scope in `prefix_hash` | 0.3 d | TKE2-02 | CCH-002 | TODO |
| TKE2-05 | `CacheAwareChatModel` (outer composition wrapper, ADR-WAVE-001 §2.2) + `ProviderRegistry.get_llm(agent=)` wiring (agent from `langgraph_node`); create the shared `tests/unit/providers/test_registry_contract.py` | 0.8 d | TKE2-03/04 | CCH-003 | TODO |
| TKE2-06 | `utils/stable_json.py`; name-sorted tool binding | 0.3 d | — | CCH-003, LAY-001 | TODO |
| TKE2-07 | `SecurePromptBuilder`: `PromptLayout`, `TurnContext`, prefix canary (HMAC, `SecretStr` via vault), `detect_leak`, legacy fallback | 0.8 d | TKE0-06 | LAY-001 | TODO |
| TKE2-08 | Move per-request values out of agent/supervisor system prompts into `TurnContext` (gated on layout) | 0.6 d | TKE2-07 | LAY-001 | TODO |
| TKE2-09 | Security tests: both canaries detected; red-team recall parity LEGACY vs CACHE_STABLE; fallback never drops canary | 0.5 d | TKE2-07 | LAY-001 | TODO |
| TKE2-10 | Tests: breakpoint placement, prefix byte-stability across two turns, disabled path returns bare `ChatLiteLLM` | 0.6 d | TKE2-05 | CCH-001..003 | TODO |
### PHASE TKE3 — Cache-safe tool exposure (DELTA to LH2)
**Done when:** mask mode keeps the tool list identical across phases; disallowed calls never execute; LH tests pass unmodified.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE3-01 | `tool_gating_mode` / `tool_defer_*` settings validation | 0.1 d | TKE1-05 | CFG-001 | TODO |
| TKE3-02 | `CompositeToolProvider.allowed_tool_names`; mask mode returns full name-sorted list (MODIFIES SPEC-LH-GAT-002/003) | 0.5 d | — | GAT-001 | TODO |
| TKE3-03 | `agents/tool_gate.py` + `react_loop` enforcement, synthetic `ToolMessage`, tail hint | 0.6 d | TKE3-02, TKE2-07 | GAT-001 | TODO |
| TKE3-04 | `ActionInterceptor` allow-set re-check + hash-first audit | 0.4 d | TKE3-03 | GAT-001 | TODO |
| TKE3-05 | `agents/tool_search.py`: BM25 `ToolSearchIndex`, `search_tools`, `split_core_and_deferred` | 0.7 d | — | DEF-001 | TODO |
| TKE3-06 | Append-only activation in per-run registry; `refresh(ttl_ms)` seam for PRT | 0.6 d | TKE3-05 | DEF-001 | TODO |
| TKE3-07 | Native `defer_loading` path behind `supports_native_tool_search` | 0.4 d | TKE0-04, TKE3-06 | DEF-001 | TODO |
| TKE3-08 | Tests incl. LH back-compat (`remove` mode = LH3-05/07 unchanged) | 0.6 d | TKE3-02..07 | GAT-001, DEF-001, COMPAT-001 | TODO |
### PHASE TKE4 — Observation masking + artifact store (DELTA to LH1)
**Done when:** masked history preserves order and tool-call pairing; artifacts retrievable only within tenant+run; hysteresis compacts once per band.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE4-01 | `CompactionStrategy.MASK_OBSERVATIONS`, `mask_observations`, same-id replacement, `CompactionResult.replaced` | 0.6 d | TKE4-02 | MSK-001 | TODO |
| TKE4-02 | `agents/artifacts.py`: port, `ArtifactRef`/`ArtifactSlice`, memory + SQLite adapters, TTL, tenant/run scope | 1.0 d | — | ART-001 | TODO |
| TKE4-03 | `read_artifact` tool (sanitize/taint path, audit on scope mismatch) | 0.4 d | TKE4-02 | ART-001 | TODO |
| TKE4-04 | High/low-water hysteresis in `maybe_compact_context` (MODIFIES RF-LH-004) | 0.5 d | TKE5-01 | MSK-002 | TODO |
| TKE4-05 | Optional `relevance_fn` injection (DEC hook, no hard dependency) | 0.3 d | TKE4-01 | MSK-001 | TODO |
| TKE4-06 | Tests: position preserved through `add_messages`; store-failure keeps payload; LH truncate/summarize unchanged | 0.6 d | TKE4-01..05 | MSK-001/002, ART-001 | TODO |
### PHASE TKE5 — Tool-output budgets + TOON
**Done when:** no tool output above its budget reaches `messages` with the flag on; TOON only on uniform tables.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE5-01 | `utils/token_estimate.py` (`estimate_tokens`, ±15 % documented) | 0.2 d | — | OUT-001 | TODO |
| TKE5-02 | `ToolOutputBudget` head+tail excerpt + artifact ref | 0.5 d | TKE4-02, TKE5-01 | OUT-001 | TODO |
| TKE5-03 | Wire into `react_loop` and `@prismal_node` middleware after sanitize/taint | 0.5 d | TKE5-02 | OUT-001 | TODO |
| TKE5-04 | `utils/toon.py`: `is_uniform_tabular`, `encode_toon`, `maybe_toon` | 0.6 d | TKE2-06 | TOON-001 | TODO |
| TKE5-05 | Tests: budgets per tool, excerpt bounds, TOON golden files, nested → JSON | 0.4 d | TKE5-02..04 | OUT-001, TOON-001 | TODO |
### PHASE TKE6 — Effort / reasoning budgets per role
**Done when:** effort passed only where supported; disabled path constructs v3.12.0 `ChatLiteLLM`.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE6-01 | Settings `llm_effort_*`, `recommended` profile resolution | 0.2 d | TKE1-05 | EFF-001 | TODO |
| TKE6-02 | `get_llm(agent=)`: `reasoning_effort` + per-agent `max_tokens` for supporting models only | 0.5 d | TKE0-05, TKE2-05 | EFF-001 | TODO |
| TKE6-03 | Tests: unsupported model omits kwarg; constant per run | 0.3 d | TKE6-02 | EFF-001 | TODO |
### PHASE TKE7 — Handoff envelopes
**Done when:** all six patterns emit envelopes with the flag on and legacy checkpoints still load.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE7-01 | `agents/handoff.py`: `HandoffEnvelope`, `build_envelope`, `envelope_to_message_text` | 0.5 d | TKE4-02 | HND-001 | TODO |
| TKE7-02 | Skynet `WorkerResult.envelope` + reducer/evaluator reads summaries | 0.5 d | TKE7-01 | HND-001 | TODO |
| TKE7-03 | MoA proposers, Kokoro souls, BRP roles, `parallel_research`, spawner | 1.2 d | TKE7-01 | HND-001 | TODO |
| TKE7-04 | Tests: legacy shapes round-trip; summary cap; artifact fetched on demand only | 0.4 d | TKE7-02/03 | HND-001 | TODO |
### PHASE TKE8 — RAG context budget
**Done when:** retrieved context ≤ budget; missing extras never raise.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE8-01 | `rag/context_budget.py`: `fit_to_budget`, `RerankerPort` | 0.4 d | TKE5-01 | RAG-001 | TODO |
| TKE8-02 | `providers/rerank.py` cross-encoder (`[rerank]`, deferred import) + `_merge_and_rerank` fallback | 0.6 d | TKE8-01 | RAG-001 | TODO |
| TKE8-03 | `providers/compression.py` LLMLingua-2 (`[compression]`); refuses code/JSON/cached prefix | 0.5 d | TKE8-01 | RAG-001 | TODO |
| TKE8-04 | Wire into RAG engines (retrieve 20 → budget) | 0.5 d | TKE8-02 | RAG-001 | TODO |
| TKE8-05 | Tests: budget fit, missing-extra fallback, compression exclusions | 0.4 d | TKE8-02..04 | RAG-001 | TODO |
### PHASE TKE9 — Integration, eval gate, docs
**Done when:** gate green; PLAN §4 metrics met (≥ 50 % input cost, ≥ 30 % tokens/success, ≥ 60 % supervisor hit ratio, 0 solve-rate regression).

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE9-01 | Graph snapshot test with all TKE flags off (**mandatory**) | 0.3 d | TKE1..8 | COMPAT-001 | TODO |
| TKE9-02 | Disabled contract test: tools, prompts, `ChatLiteLLM` kwargs, `Usage`/`cost_usd` identical to v3.12.0 | 0.3 d | TKE1..8 | COMPAT-001 | TODO |
| TKE9-03 | CI token-regression gate (report-only → blocking) | 0.6 d | TKE1-08 | ACC-005 | TODO |
| TKE9-04 | Eval A/B on fixed + long-loop sets; record success metrics in docs | 0.6 d | TKE9-03 | ACC-005 | TODO |
| TKE9-05 | AST guards re-run (provider imports, `mcp`/`skills`, env reads) | 0.2 d | TKE1..8 | COMPAT-001 | TODO |
| TKE9-06 | `docs/token-efficiency.md` (incl. canary DD, LH delta table) | 0.5 d | TKE9-04 | — | TODO |
| TKE9-07 | Notebook (`prisma-notebooks`) + `examples/token_efficiency.py` | 0.5 d | TKE9-06 | — | TODO |
| TKE9-08 | README/CHANGELOG/roadmap; cross-reference note in `specs/loop-hardening/` | 0.3 d | TKE9-06 | — | TODO |
### PHASE TKE10 — Code-mode for MCP chains (**OPTIONAL, P3**, flag `codeact_mcp_code_mode_enabled`)
**Done when:** bulk MCP chain eval shows token reduction with no policy bypass; may slip to a later minor without blocking 3.13.0.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| TKE10-01 | `render_mcp_module` typed stubs | 0.8 d | TKE9 | CMD-001 | TODO |
| TKE10-02 | `SandboxToolBridge` with per-call `ActionInterceptor` + `ToolPolicyEngine` | 1.0 d | TKE10-01 | CMD-001 | TODO |
| TKE10-03 | `codeact_agent` integration behind flag | 0.4 d | TKE10-02 | CMD-001 | TODO |
| TKE10-04 | Tests: policy denial inside sandbox; only stdout returned | 0.6 d | TKE10-03 | CMD-001 | TODO |
## 4. Test Inventory (planned)
| Test module(s) | Covers |
|---|---|
| `tests/unit/budget/test_{usage_cache_fields,extract_cache_usage,meter_tke_metrics}.py`, `tests/unit/providers/test_cost_cache_aware.py` | TKE1-01..04, TKE1-06, TKE1-09 |
| `tests/unit/core/test_config_token_efficiency.py`, `tests/unit/monitoring/test_run_summary_cache.py`, `tests/unit/eval/test_token_report.py` | TKE1-05/07/08, TKE3-01, TKE6-01 |
| `tests/unit/providers/test_{prompt_cache_policy,cache_aware_chat_model,effort_per_agent}.py` | TKE2-01..06/10, TKE6-02/03 |
| `tests/unit/security/test_{prompt_layout_cache_stable,canary_leak_cache_stable}.py` | TKE2-07/08/09 |
| `tests/unit/agents/test_{tool_gate_mask,tool_search,artifact_store,mask_observations,compaction_hysteresis}.py` | TKE3-02..08, TKE4-01..06 |
| `tests/unit/agents/test_{tool_output_budget,handoff_envelope}.py`, `tests/unit/utils/test_toon.py`, `tests/unit/rag/test_rag_context_budget.py` | TKE5-01..05, TKE7-01..04, TKE8-01..05 |
| `tests/unit/agents/test_{graph_snapshot_token_efficiency,tke_disabled_contract,codeact_mcp_mode}.py`, `tests/eval/test_token_regression_gate.py` | TKE9-01..05, TKE10-01..04 (optional) |
## 5. Risk Register (implementation)
| Risk | Mitigation |
|---|---|
| `ChatLiteLLM` strips cache params; canary relocation reduces detection | TKE0-03 first, fallback to direct `litellm.acompletion` inside `providers/`; DD-TKE-004 + TKE2-09 parity gate blocks release |
| LH regressions | `remove`/`truncate`/`high_water=0` defaults; LH3-05/06/07 re-run in TKE3-08/TKE4-06 |
| Masking loses data; envelopes break checkpoints | Mask only after successful artifact write (TKE4-06); optional fields + legacy round-trip (TKE7-04) |
| Eval noise trips the gate; TKE10 scope creep | Fixed seeds/fakes, report-only until two green runs; TKE10 separately flagged, P3, last |
## 6. Definition of Done (feature)
- [ ] All MUST requirements implemented and tested (§8); SHOULD implemented; TKE10 optional.
- [ ] All flags off ⇒ graph snapshot + disabled contract tests prove zero behavior change; LH suite passes unmodified; LH delta documented (MODIFIED/ADDED).
- [ ] Canary leak-detection parity proven on the H red-team corpus; eval gate green; PLAN §4 targets recorded in `docs/token-efficiency.md`.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; coverage ≥ 80 % on new modules; AST guards pass.
## 7. Effort Summary
| TKE0 | TKE1 | TKE2 | TKE3 | TKE4 | TKE5 | TKE6 | TKE7 | TKE8 | TKE9 | **Core total** | TKE10 (optional P3) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1.5 d | 3.8 d | 5.4 d | 3.9 d | 3.4 d | 2.2 d | 1.0 d | 2.6 d | 2.4 d | 3.3 d | **29.5 d** | 2.8 d |
## 8. Traceability Matrix
| SPEC | Tasks | Tests |
|---|---|---|
| SPEC-TKE-ACC-001 / 002 | TKE1-01, TKE1-09 / TKE0-01, TKE1-02 | `test_usage_cache_fields.py`, `test_extract_cache_usage.py` |
| SPEC-TKE-ACC-003 | TKE0-02, TKE1-03 | `test_cost_cache_aware.py` |
| SPEC-TKE-ACC-004 | TKE1-04, TKE1-06 | `test_meter_tke_metrics.py` |
| SPEC-TKE-ACC-005 | TKE1-07, TKE1-08, TKE9-03, TKE9-04 | `test_run_summary_cache.py`, `test_token_report.py`, `test_token_regression_gate.py` |
| SPEC-TKE-CCH-001 / 002 | TKE0-03, TKE2-01, TKE2-02 / TKE2-03, TKE2-04 | `test_prompt_cache_policy.py` |
| SPEC-TKE-CCH-003 | TKE2-05, TKE2-06 | `test_cache_aware_chat_model.py` |
| SPEC-TKE-LAY-001 | TKE0-06, TKE2-07, TKE2-08, TKE2-09 | `test_prompt_layout_cache_stable.py`, `test_canary_leak_cache_stable.py` |
| SPEC-TKE-GAT-001 / DEF-001 | TKE3-02..04, TKE3-08 / TKE0-04, TKE3-05..07 | `test_tool_gate_mask.py` / `test_tool_search.py` |
| SPEC-TKE-ART-001 | TKE4-02, TKE4-03 | `test_artifact_store.py` |
| SPEC-TKE-MSK-001 / 002 | TKE4-01, TKE4-05, TKE4-06 / TKE4-04 | `test_mask_observations.py` / `test_compaction_hysteresis.py` |
| SPEC-TKE-OUT-001 / TOON-001 (SHOULD) | TKE5-01..03 / TKE5-04 | `test_tool_output_budget.py` / `test_toon.py` |
| SPEC-TKE-EFF-001 | TKE0-05, TKE6-01..03 | `test_effort_per_agent.py` |
| SPEC-TKE-HND-001 | TKE7-01..04 | `test_handoff_envelope.py` |
| SPEC-TKE-RAG-001 | TKE8-01..05 | `test_rag_context_budget.py` |
| SPEC-TKE-CFG-001 / ERR-001 / OTEL-001 (SHOULD) | TKE1-05, TKE3-01 / TKE1-06 | `test_config_token_efficiency.py` / `test_meter_tke_metrics.py` |
| SPEC-TKE-COMPAT-001 | TKE9-01, TKE9-02, TKE9-05, TKE3-08 | `test_graph_snapshot_token_efficiency.py`, `test_tke_disabled_contract.py` |
| SPEC-TKE-CMD-001 (MAY, P3) | TKE10-01..04 | `test_codeact_mcp_mode.py` |
