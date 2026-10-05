# Prismal Token Efficiency — Technical Design Document

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
| **TASKS** | `specs/token-efficiency/TASKS.md` |

---

## 1. Context

The 2026-10 gap analysis (§2, §3.3) identified prompt caching, cache-safe tool exposure, observation masking, tool-output budgets and sub-agent result contracts as missing. Verified against the code:

- `prismal/budget/usage.py::extract_token_usage` reads only `input_tokens`/`output_tokens` (or `prompt_tokens`/`completion_tokens`) and `prismal/budget/types.py::TokenCounts` has no cache fields → hit ratio is unmeasurable.
- `prismal/providers/cost.py::compute_cost_usd` calls `litellm.cost_per_token(model, prompt_tokens, completion_tokens)` with no cache arguments; its table fallback knows only `input`/`output` per 1k.
- `prismal/providers/registry.py::get_llm` constructs a bare `ChatLiteLLM` with global `max_tokens`; there is no cache parameter anywhere (`grep cache_control` → 0 hits).
- `prismal/agents/extension/providers.py::CompositeToolProvider.get_tools` (LH2) returns a phase-narrowed list (`_effective_capabilities`) in provider-priority order.
- `prismal/agents/context_compaction.py` offers `TRUNCATE`/`SUMMARIZE` only; `maybe_compact_context` triggers on message count, drops all but `keep_recent`, and its docstring documents that the summary lands after the tail.
- `SecurePromptBuilder` (`prismal/security/prompt_builder.py`, used by `context_compaction.py::_default_summarizer_fn` as `build(system=..., user=...)`) places per-request canary tokens in the system prompt (gap analysis §2).

## 2. Feasibility with the existing core (confirmed)

- **Additive dataclass fields.** `TokenCounts`/`Usage` are frozen dataclasses with all-default fields; appending defaulted fields keeps every existing constructor call valid. `Usage.__add__` is the single place that must learn the new fields.
- **`ObservabilityPort.record_node(..., usage: Usage | None)`** already carries a `Usage`, so cache fields reach OBS with no port change; only `RunSummary` aggregation gains fields.
- **`CostMeter.record_response`** is the single extract→price→record choke point; one edit wires cache-aware pricing for every metered call (react_loop, patterns, Skynet, compaction summarizer).
- **`ProviderRegistry.get_llm`** is the single LLM factory → wrapping its return value in `CacheAwareChatModel` reaches all 26 agents without touching them. The wrapper reads the agent name from `RunnableConfig["metadata"]["langgraph_node"]` when `agent=None`.
- **`add_messages` replaces a message with an existing `id`** (LangGraph semantics, same reducer LH1 relies on for `RemoveMessage`) → observation masking can rewrite a `ToolMessage` in place, preserving position — unlike `SUMMARIZE`, which can only append.
- **LH per-run registry pattern** (`_RUN_ENGINES` in `context_compaction.py`, `budget/resolve.py`) hosts the artifact-store handle, the tool-search index and the activated-deferred-tool set.
- **`CompositeToolProvider._effective_capabilities`** already computes the phase-narrowed capability set; mask mode reuses it to derive an allow-set instead of shrinking the list.
- **Eval harness (V)** already produces per-case results; `TokenReport` is a pure aggregation over them.

No new mandatory dependency. Optional extras: `[rerank]`, `[compression]`.

## 3. Proposed Architecture

### 3.1 Modules

| Module | New/extend | Responsibility |
|---|---|---|
| `budget/types.py`, `budget/usage.py`, `budget/meter.py` | extend | Cache/reasoning fields, extraction, metrics |
| `providers/cost.py` | extend | Precedence gateway > litellm > table > none |
| `providers/prompt_cache.py` | new | `CacheFamily`, `CachePolicy`, `apply_cache_policy`, `CacheAwareChatModel` |
| `providers/registry.py` | extend | Wrapper wiring, `agent=` kwarg, effort/max-tokens |
| `security/prompt_builder.py` | extend | `PromptLayout.CACHE_STABLE`, `TurnContext`, prefix canary, `detect_leak` |
| `utils/stable_json.py`, `utils/token_estimate.py`, `utils/toon.py` | new | Deterministic JSON, heuristic estimate, TOON |
| `agents/tool_gate.py`, `agents/tool_search.py` | new | Mask enforcement; BM25 deferred tools |
| `agents/artifacts.py` | new | `ArtifactStorePort` + memory/SQLite adapters + `read_artifact` |
| `agents/context_compaction.py` | extend | `MASK_OBSERVATIONS`, hysteresis |
| `agents/tool_output_budget.py` | new | Per-tool output budgets |
| `agents/handoff.py` | new | `HandoffEnvelope` |
| `rag/context_budget.py`, `providers/rerank.py`, `providers/compression.py` | new | RAG budget, rerank, compression |
| `eval/metrics_tokens.py` | new | `TokenReport`, baseline, regression gate |
| `agents/codeact_mcp.py` | new, **optional P3** | MCP-as-module bridge |
| `core/config.py`, `core/exceptions.py`, `monitoring/otel.py` | extend | Settings, `TokenEfficiencyError`, metrics |
| `config/prompt_cache.yaml` | new | Min-prefix table, breakpoint caps, family overrides (volatile values) |

### 3.2 TKE1 + TKE2 request path

```
agent node ── get_llm(model, agent=?) ──► ProviderRegistry
                                            │ token_efficiency_enabled && prompt_cache_enabled?
                                            ├─ no ─► ChatLiteLLM (v3.12.0, byte-identical)
                                            └─ yes ► CacheAwareChatModel(inner=ChatLiteLLM)
   SecurePromptBuilder.build(layout=CACHE_STABLE, turn_context=TurnContext(canary, req_id, now))
   ┌──────────── cached prefix ────────────┐┌──────── per-request tail ─────────┐
   │ tools (sorted, stable_dumps) [BP1]    ││ ...history...  [BP3 moving ≤20]   │
   │ system: static instr + prefix canary  ││ latest user turn:                 │
   │                                 [BP2] ││   user text + <turn_context>      │
   └───────────────────────────────────────┘│   canary · request_id · now ·     │
                                            │   allowed-tools hint (TKE3)       │
                                            └───────────────────────────────────┘
        │ apply_cache_policy(family=detect_cache_family(model))
        │   anthropic_explicit → cache_control on BP1/BP2/BP3 (≤ max_breakpoints)
        │   openai_auto        → prompt_cache_key=prismal:{agent}:{prefix_hash}
        │   gemini/deepseek/none → no-op
        ▼
   LiteLLM call ──► response.usage{cached, cache_write, reasoning}
        ▼
   CostMeter.record_response → extract_token_usage → compute_cost_usd(cache_read=, cache_write=,
        reported_cost_usd= (GW), response=) → Usage(+cache fields) → OTel kind-split + hit ratio
        → ObservabilityPort.record_node(usage) → RunSummary(cache fields) → eval TokenReport
```

### 3.3 TKE3 mask-mode tool path (delta to LH2)

```
get_tools_for_agent(agent, caps, phase)
   tool_gating_mode="remove" ─► LH2 path unchanged (narrowed list)
   tool_gating_mode="mask"   ─► full list, sorted by name  (prefix stable across phases)
                              + allow_set = allowed_tool_names(agent, caps, phase)
                              + (tool_defer_enabled && len > threshold)
                                   → core tools + search_tools  |  native defer_loading [verify]
model emits tool_call(name)
   react_loop ─► check_tool_call(name, allow_set, phase)
       allowed  ─► ActionInterceptor.check() (also re-checks allow_set) ─► execute
       denied   ─► ToolMessage("tool 'x' not available in phase P") + audit + counter
   search_tools(q,k) ─► BM25 hits ─► activated set (append-only) ─► next call exposes them
```

### 3.4 TKE4/TKE5 observation path (delta to LH1)

```
tool executes ─► H sanitize/taint ─► ToolOutputBudget.apply (TKE5)
                   over budget → head+tail excerpt + ArtifactStore.put → "[truncated … artifact=id]"
                   uniform tabular & toon enabled → encode_toon
   ─► ToolMessage appended
supervisor turn ─► maybe_compact_context
   high_water=0 → LH trigger;  high_water>0 → est_tokens > high? compact to ≤ low
   strategy=mask_observations:
     for ToolMessage older than keep_turns (and relevance < keep, if hook injected):
        ArtifactStore.put(payload) → ToolMessage(id=same, tool_call_id=same,
                                     content="[observation elided: tool=t artifact=a tokens≈n]")
     state update → add_messages replaces in place (no RemoveMessage, order preserved)
model may call read_artifact(id, start, end) → tenant/run check → sanitize → slice
```

### 3.5 TKE7 handoff

```
worker / proposer / soul / BRP role / research branch / spawned sub-agent
   └─ build_envelope(full_text) → summary ≤ 1,500 tok + ArtifactRef(full_text) + status + usage
reducer / evaluator / fusion reads envelope.summary; read_artifact only on demand
```

## 4. Design Decisions

### DD-TKE-001: Measure first, gated by the master flag
TKE1 ships before any optimization and is enabled by `token_efficiency_enabled` alone, so baselines (`tests/eval/baselines/tokens.json`) are captured with optimizations off. Extraction always fills the new fields (purely additive), but cache-aware pricing and new metrics only run when the master flag is on — the cost numbers of v3.12.0 deployments do not move silently.

### DD-TKE-002: Cost precedence gateway > litellm > table > none
A gateway-reported figure is what was billed (GW); LiteLLM's `completion_cost(response)` prices cache tiers when a raw response is available, else `cost_per_token` with cache kwargs **[verify]**; the table gains optional `cache_read`/`cache_write`. `estimated=False` for `gateway` and `litellm`.

### DD-TKE-003: Caching lives in the provider layer, not in agents
`CacheAwareChatModel` in `providers/` (Rule #4) applies breakpoints at request time. Agents keep constructing prompts as today; the only agent-visible change is the opt-in `CACHE_STABLE` layout in `SecurePromptBuilder`. Volatile provider facts (min prefix, breakpoint cap, lookback window, param names) come from `config/prompt_cache.yaml`.

### DD-TKE-004 (security): Canary relocation out of the cached prefix
**Problem.** A per-request canary in the system prompt makes every prefix unique → 0 % cache hits. **Decision.** Under `CACHE_STABLE`: (a) the per-request canary moves into the `<turn_context>` block at the tail of the latest user turn; (b) a **prefix canary** — `HMAC-SHA256(prompt_canary_secret, tenant ‖ ttl_bucket)`, truncated, rotated every cache-TTL bucket — stays in the system prompt; the static instruction "never reveal markers" stays in the system prompt. **Why leak detection is preserved.** Canary-based leak detection rests on two properties: the canary is present in the model's context, and output is checked for it. Both hold: the request canary is still in context on every call and `detect_leak` checks every output for it; system-prompt-extraction attacks are still caught by the prefix canary, which is in the system prompt and also checked. The prefix canary is unpredictable to attackers (secret HMAC key, per tenant, rotated) and identical across requests in a bucket, so it is cacheable. **Residual risk.** The prefix canary is shared by requests in a bucket, so a leak identifies tenant + bucket rather than the exact request; the request canary still pins the request. **Fail-closed.** If the layout cannot be built (e.g. no user turn), the builder falls back to `LEGACY` (canary in system prompt, cache miss) and counts the fallback; a canary is never dropped. Red-team recall parity is a release gate (TKE2-09).

### DD-TKE-005: Cache scope per tenant by default
`prompt_cache_scope="tenant"` folds the tenant id into `prefix_hash` / `prompt_cache_key` and into the prefix canary, preventing cross-tenant prefix sharing and the associated timing side-channel on shared provider keys. `"global"` is an explicit operator opt-in for single-tenant deployments.

### DD-TKE-006: Mask, don't remove (delta to LH2)
Removing tools per phase rewrites the first block of the Anthropic cache order (tools → system → messages). `mask` keeps the list stable and enforces the phase at execution, in two places (`react_loop` and `ActionInterceptor`) so a pattern that bypasses `react_loop` is still covered. Unlike LH2 (hygiene only), a masked denial **is** audited, because the model asked for a tool it should not use in that phase. `remove` stays the default.

### DD-TKE-007: Deferred tools are append-only
`search_tools` activation only appends to the exposed list for the run; nothing is removed or reordered, so earlier cache breakpoints stay valid. BM25 is implemented in-house (≈80 lines) — no new dependency. Native `defer_loading` is used only behind `supports_native_tool_search(model)` after TKE0-04.

### DD-TKE-008: Masking before summarizing; replace by id (delta to LH1)
Per JetBrains (2025) masking matches or beats summarization at ~half the cost, and summarization lengthened trajectories. Masking writes same-id `ToolMessage`s so positions are preserved and the `tool_use`/`tool_result` pairing required by providers stays intact. Payloads are never lost: masking happens only after a successful artifact write.

### DD-TKE-009: Hysteresis instead of per-turn trimming
Compacting only when above high-water and down to low-water means the cached prefix is rewritten once per band crossing rather than on every turn past the threshold.

### DD-TKE-010: Heuristic token estimation
`ceil(len(text)/4)` with documented ±15 % error; budgets are soft targets. Exact counts come from provider usage (TKE1).

### DD-TKE-011: TOON only for uniform flat tables
TOON saves 40–58 % on flat arrays but hurts nested data; `maybe_toon` falls back to `stable_dumps`. Output schemas and structured outputs stay JSON.

### DD-TKE-012: Envelopes are additive
`envelope: HandoffEnvelope | None = None` on existing result types; reducers prefer the envelope when present and fall back to the legacy text.

### DD-TKE-013: Code-mode is optional and isolated (TKE10, P3)
Generated stubs run only inside `SandboxExecutor`; every call is bridged back to the host and passes `ActionInterceptor` + `ToolPolicyEngine` per call (Rules #2/#5). It can slip to a later minor without blocking 3.13.0.

## 5. Security & Cost

- Canary relocation: DD-TKE-004. Cache scope: DD-TKE-005. Secrets (`prompt_canary_secret`) as `SecretStr` via `ConfigSourcePort`/`CredentialVault`, never in `os.environ`, never logged.
- Artifact store: unguessable ids, `(tenant, run_id)` check on every read (`ArtifactNotFound` on mismatch — no oracle), TTL purge, SQLite file under the data dir with path confinement (`filesystem_guard`). Artifacts hold **sanitized** content; `read_artifact` output re-enters through the same taint path as the original observation (H).
- Masked-tool denials: fail-closed, audited hash-first, counted. Tool-search results expose schemas only of tools already resolved for the agent — never widen beyond the capability filter.
- Summaries in envelopes and observation stubs are built without string-concatenating untrusted content into instructions (Rule #1); envelope summaries generated by an LLM go through `SecurePromptBuilder`.
- Cost: fail-open everywhere; a pricing miss never breaks a turn. Cache writes cost more than reads (1.25×/2× on Anthropic) — `min_prefix_tokens` and per-family policy avoid write-heavy, read-poor patterns; the hit-ratio metric makes such regressions visible.

## 6. Observability

- Metrics: SPEC-TKE-OTEL-001 (kind-split input tokens, hit-ratio histogram, reasoning tokens, breakpoints, layout fallbacks, masked calls, tool search, masking, artifacts, truncation, TOON, envelopes, RAG drops).
- Span attributes on the existing `provider.get_llm` span: `prismal.cache.family`, `prismal.cache.breakpoints`, `prismal.cache.prefix_hash` (truncated). No new spans on the hot path.
- `RunSummary` cache fields feed `prismal-server` `/v1/usage` and DSH2 panels.
- Eval: `TokenReport` (tokens/success, hit ratio, trajectory length, solve rate, input cost/success).

## 7. Relationship to Existing Specs

- **`loop-hardening/` (LH)** — TKE3 MODIFIES RF-LH-006 / SPEC-LH-GAT-002/003 and ADDS mask enforcement + deferred loading; TKE4 ADDS `MASK_OBSERVATIONS` and MODIFIES RF-LH-004 (hysteresis). Defaults keep LH behavior; the LH snapshot/contract tests (LH3-06/07) keep passing unchanged. LH docs gain a "see token-efficiency" note on ship.
- **`cost-budget-governance/` (C)** — extends `Usage`, `CostMeter`, `compute_cost_usd`; `BudgetGuard` keeps reading `total_tokens`/`cost_usd`.
- **`runtime-hardening/` (H)** — `SecurePromptBuilder`, `ActionInterceptor`, taint registry reused; canary DD reviewed by Security Lead.
- **OBS** — `RunSummary` additive fields. **V** — token metrics + CI gate.
- **Downstream** — GW fills `reported_cost_usd`; AMR consumes hit ratio for cache affinity; MEM reuses `ArtifactStore`, budgets, TOON; PRT feeds MCP `ttlMs` into `ToolSearchIndex.refresh`.

## 8. Testing Strategy

- Unit: extraction fixtures for Anthropic/OpenAI/OpenRouter/DeepSeek/Gemini shapes; pricing precedence; `apply_cache_policy` breakpoint placement incl. moving breakpoint and min-prefix; prefix byte-stability across two turns; canary placement and `detect_leak` for both canaries; mask enforcement; BM25 ranking; artifact tenant isolation and TTL; same-id replacement through `add_messages`; hysteresis band; head+tail excerpt; TOON round-trip; effort passthrough; envelope round-trip of legacy shapes; RAG budget fit.
- Security: H red-team corpus run with `LEGACY` vs `CACHE_STABLE` → recall parity.
- Integration (fakes, no live LLM): graph snapshot with all flags off; disabled contract (tools, prompts, usage, cost); long-loop run with masking keeps solve rate; Skynet with envelopes.
- Eval: A/B on the fixed eval set → success metrics; CI token-regression gate.
- Guards: provider-import, `mcp`/`skills` in agents, `os.environ` reads.

## 9. Rollout

1. Ship all modules dark (master off).
2. **Shadow/measure:** enable `token_efficiency_enabled` only → collect hit ratio (≈0) and baselines; commit `baselines/tokens.json`.
3. Enable `prompt_cache_enabled` + `CACHE_STABLE` in staging; verify red-team parity and hit ratio ≥ 60 % on supervisor turns.
4. **Warn:** `tool_gating_mode="mask"` with denials logged/audited; `tool_defer_enabled` for agents with > 30 tools.
5. Enable `mask_observations` + hysteresis, tool-output budgets, effort `recommended` profile; watch the eval gate.
6. **Enforce:** turn the CI token-regression gate from report-only to blocking once two consecutive green runs exist.
7. Envelopes and RAG budget per pattern; TKE10 only after a separate review.

## 10. Open Questions

1. Does `ChatLiteLLM` (langchain-litellm) forward content-block `cache_control` and `prompt_cache_key` unchanged? **[verify]** TKE0-03 — if not, the wrapper calls `litellm.acompletion` directly inside `providers/`.
2. Prefix-canary TTL bucket: tie to `prompt_cache_ttl` (5 m/1 h) or a fixed 1 h? Proposed: equal to `prompt_cache_ttl`.
3. Should `read_artifact` be metered against Budget as a pseudo-call? Proposed: no (it costs input tokens only, already metered).
4. Default `tool_defer_core` per agent — derive from `DEFAULT_CAPABILITY_MAP` or require explicit config? Proposed: explicit, empty = top-N by usage telemetry later.
5. TKE10 in 3.13.0 or 3.13.x? Proposed: optional; ship only if TKE1–TKE9 land on schedule.
