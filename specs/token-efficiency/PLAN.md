# Prismal — Token Efficiency (Cache-Aware Accounting, Prompt Caching, Cache-Safe Context Engineering)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | TKE (Token Efficiency) |
| **Target package version** | `3.13.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead, FinOps |
| **Priority** | P1 (cost) — TKE1 is a hard prerequisite of GW, AMR and MEM; TKE10 is P3 (optional stretch) |
| **Depends on** | LH (`loop-hardening/`, shipped 3.7.0), C (`cost-budget-governance/`), OBS (`ObservabilityPort`), V (`agent-eval-harness/`), H (`runtime-hardening/`) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2, §3.3, §3.4, §5 row 1), `specs/loop-hardening/` (TKE3/TKE4 are **deltas** to LH2/LH1), `specs/llm-gateway-providers/` (GW fills `reported_cost_usd`), `specs/adaptive-model-routing/` (consumes TKE1 metrics), `specs/agentic-memory/` (reuses `ArtifactStore`, budgets, TOON) |

---

## 1. Executive Summary

The 2026-10 gap analysis (§3.3) ranks provider prompt caching, cache-safe tool exposure, observation masking and sub-agent result contracts as the highest-ROI token reductions available to an agent framework — and found that prismal does **none** of them today. Worse, prismal cannot even *measure* the problem: the cache-hit ratio, "the single most important production metric of context engineering" (Manus), is invisible because `budget/usage.py::extract_token_usage` returns only prompt/completion counts.

**Token Efficiency (Phase TKE)** closes this in ten phases, **measure first**: TKE1 adds cache/reasoning-aware accounting and eval metrics so every later phase's savings are provable against a committed baseline (`tests/eval/baselines/tokens.json`); TKE2–TKE8 add the optimizations (prompt caching + cache-stable layout, cache-safe tool gating + deferred tools, observation masking with an artifact store, tool-output budgets + TOON, per-role effort, handoff envelopes, RAG context budgets); TKE9 integrates and adds a CI token-regression gate. TKE10 (code-mode for MCP chains) is an **optional P3 stretch**, separately flagged.

Everything is opt-in behind `token_efficiency_enabled=False` plus per-phase flags. With all flags off, the compiled supervisor graph, tool resolution, usage/cost numbers and prompts are **byte-for-byte unchanged** (snapshot + contract tests). TKE3 and TKE4 are explicit **deltas to the shipped Loop Hardening spec** whose defaults (`tool_gating_mode="remove"`, `context_compaction_strategy="truncate"`, hysteresis disabled) preserve LH behavior exactly.

---

## 2. Context and Problem (code facts, verified 2026-10-04)

| # | Current state (file) | Consequence |
|---|---|---|
| 1 | `budget/types.py::TokenCounts`/`Usage` carry only `prompt_tokens`, `completion_tokens`, `cost_usd`, `calls`, `wall_clock_s`, `estimated` | No place to store cache-read / cache-write / reasoning tokens |
| 2 | `budget/usage.py::extract_token_usage` reads `usage_metadata.input_tokens/output_tokens` → `response_metadata["token_usage"].prompt_tokens/completion_tokens` → zeros | `input_token_details`, `prompt_tokens_details.cached_tokens`, Anthropic `cache_read_input_tokens` / `cache_creation_input_tokens`, `reasoning_tokens` are all discarded |
| 3 | `providers/cost.py::compute_cost_usd(model, prompt_tokens, completion_tokens)` → `litellm.cost_per_token` → `settings.budget_pricing[model]` (`input`/`output` per 1k) → `CostEstimate(0.0, "none")` | Cached reads (≈0.1× input price) are billed at full input price; cache writes (1.25×/2×) are under-priced; gateway-reported cost has no slot |
| 4 | `budget/meter.py::CostMeter._emit_otel` emits `budget_tokens` = `usage.total_tokens` only | No `kind` split, no hit-ratio metric |
| 5 | `providers/registry.py::get_llm` builds `ChatLiteLLM(model, streaming, temperature, max_tokens=settings.max_tokens, …)` with one global `max_tokens`, no `reasoning_effort`, no cache params; `grep cache_control` → 0 hits | Every call pays full input price for the identical system prompt + tool schemas of 26 agents; effort is not tuned per role |
| 6 | `SecurePromptBuilder` puts per-request canary tokens in the system prompt (gap analysis §2; `CLAUDE.md` "isolates user input with canary tokens") | A per-request value in the prefix defeats every provider prefix cache |
| 7 | `agents/extension/providers.py::CompositeToolProvider.get_tools` (LH2) narrows `capabilities` per `(agent, phase)` and **returns a different tool list** per phase; merge order = provider priority, not name | Anthropic cache order is tools → system → messages: each phase change invalidates the whole prefix |
| 8 | `agents/context_compaction.py`: `CompactionStrategy` = `TRUNCATE` \| `SUMMARIZE`; `maybe_compact_context` fires on `len(messages) > context_compaction_max_messages` and drops everything but `keep_recent`; the summary lands *after* the tail (module docstring) | No observation masking (the cheapest, best-performing strategy per JetBrains 2025); every compaction rewrites the cached prefix; no hysteresis |
| 9 | Tool results are appended raw to `messages`; RAG `search(k=5)` with rerank only in `federated.py::_merge_and_rerank` | Large MCP/RAG payloads dominate prompt size; no token budget for retrieved context |
| 10 | Skynet `WorkerResult`, MoA, Kokoro, BRP, `parallel_research` return free-form text | The orchestrator re-reads raw worker transcripts ("15× tokens" multi-agent tax) |
| 11 | `ObservabilityPort.record_node(..., usage: Usage \| None)` and `get_run_summary() -> RunSummary` (`agents/extension/ports.py`) | Usage flows to OBS, but `RunSummary` has no cache fields |

---

## 3. Target Users

- **FinOps / Operator:** wants provable input-cost reduction on cached workloads and a cache-hit ratio dashboard (consumed later by `prismal-dashboard` DSH2).
- **AI Engineer / Flow Author:** wants long Skynet/ReAct/BRP runs to cost less without rewriting agents — wiring lives in `ProviderRegistry`, `react_loop` and `@prismal_node`.
- **Security Lead:** needs the canary relocation, masked-tool enforcement and artifact store to keep (or strengthen) today's guarantees — fail-closed on security.
- **Platform Host (`prismal-server`):** needs per-call usage with `cache_read`/`cache_write`/`reasoning` for the v0.2 `done` event and `/v1/usage`.
- **Downstream specs (GW, AMR, MEM):** consume TKE1 fields, `ArtifactStore`, handoff envelopes and budgets.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Input-cost reduction on cached workloads | USD input cost per successful task, eval set "cached-workload" A/B (TKE2+TKE3 on vs. off) | **≥ 50 %** reduction |
| Fewer tokens on long loops | `tokens_per_successful_task` on the long-loop eval set vs. `baselines/tokens.json` | **≥ 30 %** reduction |
| Cache hits on supervisor turns | `prismal.llm_cache_hit_ratio{agent="supervisor"}` p50 on multi-turn eval sessions | **≥ 60 %** |
| No quality loss | Eval solve rate vs. baseline | **0** regression (non-inferiority, ±1 pt noise band) |
| Measurement exists | cache_read / cache_write / reasoning populated for Anthropic, OpenAI, OpenRouter, DeepSeek, Gemini fixtures | 100 % of fixtures |
| CI protection | Token-regression gate fails PR when tokens/success regresses > 10 % or hit ratio drops > 10 pts | Enforced in CI |
| Opt-in safety | All flags off ⇒ graph snapshot, tool lists, prompts, `Usage`/`cost_usd` byte-for-byte unchanged | 100 % (snapshot + contract tests) |
| Security parity | Canary leak detection recall on the H red-team corpus with `cache_stable` layout | ≥ legacy layout (no regression) |

---

## 5. Scope

### In Scope
- **TKE1** Cache-aware accounting: `Usage` cache/reasoning fields, extraction, cache-aware pricing precedence (reported > litellm > table > none; source `"gateway"`), OTel metrics, `RunSummary` cache fields, eval metrics + baseline file.
- **TKE2** Prompt caching (`providers/prompt_cache.py`: `CacheFamily`, `CachePolicy`, `apply_cache_policy`) wired inside `ProviderRegistry`; cache-stable layout in `SecurePromptBuilder` (dynamic values incl. canaries → tail turn-context block); deterministic JSON; stable tool ordering.
- **TKE3** *(delta to LH2)* `tool_gating_mode="mask"`; deferred tool loading with `search_tools` (BM25) and native `defer_loading` behind a capability flag.
- **TKE4** *(delta to LH1)* `CompactionStrategy.MASK_OBSERVATIONS`, `ArtifactStore` port + adapters, `read_artifact` tool, high/low-water hysteresis, optional relevance hook.
- **TKE5** `ToolOutputBudget` (head+tail excerpt + artifact ref) and `utils/toon.py` for uniform tabular data.
- **TKE6** Effort / max-tokens per agent, constant per run.
- **TKE7** `HandoffEnvelope` for Skynet, MoA, Kokoro, BRP, `parallel_research`, spawner.
- **TKE8** RAG context budget, optional cross-encoder rerank, optional LLMLingua-2 passage compression.
- **TKE9** Integration, snapshot/contract tests, eval token-regression gate, docs, notebook, example.
- **TKE10 (OPTIONAL, P3)** Code-mode for MCP chains in `codeact_agent` sandbox, flag `codeact_mcp_code_mode_enabled`.

### Out of Scope
- Gateway adapters and gateway-reported cost retrieval (GW fills `reported_cost_usd`; TKE only adds the slot and precedence).
- Model routing / cascades / batch APIs / plan caching (AMR).
- Typed decision models (DEC) — TKE4 exposes a relevance *hook* only.
- Embedding-based semantic response caches (gap analysis §3.3 #9: 0–12 % hit rate on agentic workloads).
- A tokenizer dependency — token estimates use a chars-based heuristic with documented ±15 % error.
- MCP 2026-07-28 `ttlMs`/`cacheScope` handling (forward reference to PRT; TKE3 leaves the seam).

---

## 6. Phases Overview

| Phase | Name | LH relation | Flag(s) (all default off) | Est. |
|---|---|---|---|---|
| TKE0 | Verify before build | — | — | 1.5 d |
| TKE1 | Cache-aware accounting (measure first) | — | `token_efficiency_enabled` | 3.8 d |
| TKE2 | Prompt caching + cache-stable layout | — | `prompt_cache_enabled` | 5.4 d |
| TKE3 | Cache-safe tool exposure + deferred tools | **DELTA to LH2** | `tool_gating_mode`, `tool_defer_enabled` | 3.9 d |
| TKE4 | Observation masking + artifact store | **DELTA to LH1** | `context_compaction_strategy="mask_observations"`, `context_compaction_high_water` | 3.4 d |
| TKE5 | Tool-output budgets + TOON | — | `tool_output_budget_enabled`, `tool_output_toon_enabled` | 2.2 d |
| TKE6 | Effort / reasoning budgets per role | — | `llm_effort_enabled` | 1.0 d |
| TKE7 | Handoff envelopes | — | `handoff_envelope_enabled` | 2.6 d |
| TKE8 | RAG context budget | — | `rag_context_budget_enabled`, `rag_rerank_enabled`, `rag_passage_compression_enabled` | 2.4 d |
| TKE9 | Integration, eval gate, docs | — | — | 3.3 d |
| TKE10 | Code-mode for MCP chains — **OPTIONAL P3** | — | `codeact_mcp_code_mode_enabled` | 2.8 d |

Sub-flags are effective only when `token_efficiency_enabled=True` (master). Order: TKE0 → TKE1 → (TKE2 ∥ TKE5 ∥ TKE6) → TKE3 → TKE4 → (TKE7 ∥ TKE8) → TKE9 → (TKE10).

---

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Canary relocation weakens prompt-injection / exfiltration detection | DD-TKE-004: per-request canary stays in context (tail turn-context block) and is still checked in output; a static, HMAC-derived, per-tenant rotating *prefix canary* stays in the system prompt to keep system-prompt-extraction detection; layout failure falls back to legacy (fail-closed) |
| Provider cache semantics drift (breakpoint caps, min prefix, param names) | Volatile values in a config table, never hard-coded; TKE0 verify tasks; `CacheFamily.NONE` no-op fallback |
| ChatLiteLLM drops `cache_control` content blocks or `prompt_cache_key` **[verify]** | TKE0-03 verifies; wrapper falls back to request-level kwargs or no-op and counts `prompt_cache_layout_fallbacks_total` |
| Masked tools invite calls to disallowed tools | Execution-time enforcement in `react_loop` + `ActionInterceptor`, synthetic `ToolMessage`, audit, counter; tail hint lists allowed tools |
| Observation masking hides data the model still needs | `keep_turns` window, `read_artifact` retrieval, stubs carry tool name + token estimate; eval gate on solve rate |
| Artifact store leaks across tenants / holds untrusted content | Tenant + run scoped ids (unguessable), TTL purge, content re-enters only through the same taint/sanitize path as the original tool output |
| TOON confuses models on nested data | Encoder refuses non-uniform/nested input (JSON kept); opt-in; never for output schemas |
| Effort tuning lowers quality | Per-agent map, `recommended` profile only, eval non-inferiority gate |
| Byte-for-byte leak when flags off | Every wiring point gated; graph snapshot + tool-list + `Usage`/cost contract tests (TKE9-01/02) |

---

## 8. Dependencies

- `prismal/budget/{types,usage,meter}.py`, `prismal/providers/{cost,registry}.py` (TKE1, TKE2, TKE6).
- `prismal/security/prompt_builder.py::SecurePromptBuilder` (TKE2 layout), `ActionInterceptor`, `TaintRegistry` (H) (TKE3/TKE4).
- `prismal/agents/context_compaction.py`, `agents/extension/{ports,providers}.py`, `agents/tool_registry.py`, `agents/loop_phase.py` (LH; TKE3/TKE4 deltas).
- `react_loop`, `agents/extension/decorators.py::@prismal_node` (TKE3/TKE5 seams).
- `prismal/monitoring/{otel.py,observability_types.py}` (metrics, `RunSummary`).
- `prismal/eval/` harness (V) for metrics, baselines, CI gate.
- `agents/skynet/types.py::WorkerResult`, MoA/Kokoro/BRP/`parallel_research`/spawner (TKE7); `rag/` engines + `federated.py` (TKE8); `sandbox/` + `codeact_agent.py` (TKE10).
- Optional extras: `[rerank]` (cross-encoder), `[compression]` (LLMLingua-2) — imports only under `prismal/providers/`.

---

## 9. References

- Anthropic prompt caching — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI prompt caching — https://developers.openai.com/api/docs/guides/prompt-caching
- Gemini context caching — https://ai.google.dev/gemini-api/docs/caching
- Manus, Context Engineering for AI Agents — https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- JetBrains, The Complexity Trap (observation masking) — https://arxiv.org/html/2508.21433v2
- Anthropic, context management — https://claude.com/blog/context-management
- Anthropic, advanced tool use (tool search) — https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic, code execution with MCP — https://www.anthropic.com/engineering/code-execution-with-mcp
- Anthropic, multi-agent research system — https://www.anthropic.com/engineering/multi-agent-research-system
- TOON format — https://github.com/toon-format/toon
- LLMLingua-2 — https://arxiv.org/abs/2403.12968
- Anthropic, contextual retrieval — https://www.anthropic.com/engineering/contextual-retrieval
- ACON (agent context optimization) — https://arxiv.org/abs/2510.00615

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial full SDD from `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` §3.3/§5 |
