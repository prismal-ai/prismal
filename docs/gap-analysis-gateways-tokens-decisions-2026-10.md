# Prismal — Gap Analysis: LLM Gateways, Token Efficiency, Typed Decision Models and 2026 Agent Patterns

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Date** | 2026-10-04 |
| **Baseline** | `prismal` v3.12.0 · `prismal-server` v0.1.0 · `prismal-sdk` v0.1.0 · 4 front-ends spec-only |
| **Status** | `ACCEPTED` — source document for the six new core SDDs and the satellite deltas listed in §6 |
| **Supersedes** | — (complements `docs/gap-analysis-loops-harness-guardrails-2026-07.md`) |

> Research cut-off: 2026-10-04. Several sources are recent (Sept 2026) and some are single-source or
> vendor-published; every such claim is tagged **[verify]** and each SDD carries a "verify before
> build" prerequisite task for it. Do not hard-code volatile values (model lists, prices, quotas,
> rate limits) — they are loaded from configuration.

---

## 1. Scope

The request was to:

1. Add first-class support for three **LLM gateways**: **OpenRouter**, **OpenCode Go** (and its
   pay-as-you-go sibling **OpenCode Zen**) and **OmniRoute** (self-hosted gateway), plus the
   **OmniRouter** budget-constrained routing algorithm (Mei et al., KDD Explorations / arXiv
   2502.20576 — a different thing with a near-identical name).
2. Compare the architectures and patterns already implemented against what the 2025–2026
   literature added, and turn the gaps into SDD artifacts.
3. Integrate the **typed decision models Jev (TypeSafe AI) and Laya (Convai, Apache-2.0)**.
4. Find and specify ways to **reduce token consumption**.

---

## 2. Current architecture — what the code actually does today (verified 2026-10-04)

| Area | Current state (file) | Consequence |
|---|---|---|
| Provider access | `providers/registry.py`: `ChatLiteLLM(model=…)`; keys mirrored into `os.environ` only for Anthropic / OpenAI / Google + `OLLAMA_API_BASE`; one global `fallback_model` via `with_fallbacks` | No gateway concept, no per-model `api_base` / headers / `extra_body`; OpenRouter only works by accident through LiteLLM env vars; no provider-preference / data-retention controls |
| Cost | `providers/cost.py`: `litellm.cost_per_token(prompt, completion)` → table → 0 | Ignores cached-read / cache-write tokens and gateway-reported cost; flat-fee subscriptions (OpenCode Go) are invisible |
| Usage | `budget/usage.py::extract_token_usage` → prompt/completion only | Cache hit rate cannot be measured — the single most important production metric of context engineering |
| Prompt caching | **None** (`grep cache_control` → 0 hits) | Every call pays full input price for the identical system prompt + tool schemas of 26 agents |
| Prompt layout | `SecurePromptBuilder` puts per-request canary tokens in the system prompt | A per-request value in the prefix defeats any provider prefix cache |
| Tool gating (LH2) | `CompositeToolProvider` **removes** tools per phase | Anthropic cache order is tools → system → messages: changing the tool list invalidates the whole cache on every phase change |
| Context compaction (LH1) | `truncate` (default) or `summarize` via `RemoveMessage` | No observation masking (the cheapest, best-performing strategy per JetBrains 2025); each compaction rewrites the cached prefix; no hysteresis |
| Tool outputs | Raw tool results appended to `messages` | Large MCP / RAG payloads dominate prompt size |
| RAG context | `search(k=5)`; rerank only in `federated.py::_merge_and_rerank` | No token budget for retrieved context; no cross-encoder rerank; no passage compression |
| Multi-agent handoff | Skynet / MoA / Kokoro / BRP / parallel_research return free-form text | Orchestrator re-reads raw worker transcripts ("15× tokens" multi-agent tax) |
| Routing | Regex `intent_router` → LLM `SUPERVISOR` | Every non-regex turn pays a frontier-model call to pick one of ≤ 30 routes |
| Model choice | Static per-feature settings (`skynet_worker_model`, `kokoro_judge_model`, `blind_review_*_model`, …) | No cascade, no budget-aware downgrade, no batch assignment |
| Judges / gates | LLM-as-judge (eval harness), LLM HITL decisions, LLM CRAG grader | Expensive, slow and high-variance for simple typed verdicts |
| Memory | `LongTermMemory` (SQLite, save/recall/expire), `LongTermMemoryStore` (LangGraph `BaseStore`), `ShortTermMemory` | Untyped, ungated writes; no sufficiency/stop decisions on recall; no consolidation |
| Protocols | A2A types `PROTOCOL_VERSION = "0.3.0"`; `mcp>=1.28.1` with session-based client; host streams custom SSE (`token/tool_call/state/done/error`) | A2A v1.0 (2026-03, breaking), MCP 2026-07-28 (stateless, `ttlMs`/`cacheScope`) and AG-UI 1.0 (2026-09-30) not supported **[verify]** |

---

## 3. Research findings (condensed; full citations in each SDD's PLAN §References)

### 3.1 Gateways

- **OpenRouter** (https://openrouter.ai — "the unified interface for every model": 500+ models from
  80+ providers behind one OpenAI-compatible API, prepaid credits usable on any model, provider
  fallback, custom data policies; facts below re-verified against openrouter.ai/docs on 2026-10-04).
  Base `https://openrouter.ai/api/v1`. App headers `HTTP-Referer`, `X-OpenRouter-Title` (legacy
  `X-Title` still accepted) and optional `X-OpenRouter-Categories`. `provider` object: `order`, `allow_fallbacks`,
  `require_parameters`, `data_collection: deny`, `zdr`, `only`/`ignore`, `quantizations`, `sort`,
  `max_price`. Model fallbacks via `models: [...]` (billed for the model that served). Variants
  `:nitro`, `:floor`, `:exacto`, `:free`, `:batch` (`:online` deprecated). `openrouter/auto`
  router. Prompt-caching pass-through (`cache_control` for Anthropic/Gemini; automatic for
  OpenAI/DeepSeek/Grok/Moonshot/Groq). Usage always returned in 2026 (`cost`, `cached_tokens`,
  `cache_write_tokens`, `reasoning_tokens`); `GET /api/v1/generation?id=` for authoritative cost;
  `GET /api/v1/key` for remaining credit (`limit_remaining`, daily/weekly/monthly usage, free-model
  counters); 402 errors carry `error.metadata.limit_source` (`openrouter_credits`,
  `openrouter_key_limit`, `openrouter_in_flight_budget`). **Sticky provider routing**: after a cached
  request OpenRouter keeps the same provider; a `session_id` body field / `x-session-id` header
  (≤ 256 chars, 10-min idle expiry) makes this explicit — essential for prompt-cache hit rate.
  Plugins: `response-healing`, `context-compression` (middle-out truncation), `pdf`,
  `pareto-router`; `web` deprecated. Auto router (`openrouter/auto`): ~30 task classes, 7-day spend
  ranking, wildcard allow/deny lists and cost tiers `low`…`max`, no extra fee. LiteLLM:
  `openrouter/<vendor>/<model>`; `provider` and `plugins` must be passed via `extra_body`; billed cost
  surfaces in `response._hidden_params`. BYOK fee 5 % with a $25,000/month list-price free allowance
  on pay-as-you-go; BYOK keys can be prioritized or fallback. Jev is also served by OpenRouter at
  `POST /api/v1/systemone` (32k context, input-only billing) next to an alpha Decisions API
  (`/api/alpha/decisions`).
- **OpenCode Zen / Go** — Zen = pay-as-you-go gateway; Go = $10/mo (Go Plus $40/mo) subscription to
  ~28 open models (DeepSeek, Kimi, GLM, Qwen, MiniMax, …) on the same infrastructure. Endpoint is
  **per model**: OpenAI chat (`/zen/go/v1/chat/completions`), Anthropic Messages (`/v1/messages`)
  or Responses (`/v1/responses`); list via `/v1/models`. Limits: monthly $ cap per model, 20 % per
  rolling 5 h, 50 % per week; no documented quota API **[verify]**. Clients should send a custom
  `User-Agent` and a stable `x-opencode-session`. ToS: "designed for OpenCode and other coding
  agents" → restrict to coding agents. Retention exceptions (Grok 30 d, Muse Spark training,
  DeepSeek ZDR "through October 2026") **[verify]**.
- **OmniRoute** (self-hosted, MIT, ~72 k★) — one OpenAI-compatible endpoint over 357 providers,
  19 routing strategies, 4-tier combos (subscription → API key → cheap → free), circuit breakers,
  "RTK + Caveman" compression (claims 15–95 %) **[verify]**. **Risks:** subscription-OAuth upstreams
  (Claude Code / Codex / Gemini CLI) caused account bans and violate provider ToS; CVE-2026-88062
  (unauthenticated RCE when `requireLogin=false`, fixed in 3.8.49). Many look-alike forks.
- **OmniRouter** (paper) — batch-level model assignment minimizing cost subject to an average
  accuracy floor α and per-model concurrency caps; Lagrangian dual decomposition; BERT dual-head
  predictor (capability + output length) blended with k-NN retrieval; ≥ 10.15 % cheaper, up to
  +6.3 % accuracy vs baselines. Related: RouteLLM (strong/weak, ~2× cheaper), FrugalGPT cascades.

### 3.2 Typed decision models (Jev, Laya)

- **Jev** (TypeSafe AI, launched 2026-09-15) — "System One" non-generative model returning typed
  decisions: **Choice** (≤ 255 options), **Score** (2–10 ordered levels), **Noul** (yes/no →
  P(true)), with probabilities and a distribution-shape `confidence`. API `POST
  https://api.typesafe.ai/v1/systemone`, SDK `typesafe-sdk` (`AsyncTypeSafeClient.system_one`),
  also via OpenRouter `/api/v1/systemone`. ~$0.042 / 1M input tokens, output free; 70–650 ms
  **[verify]**. Known weaknesses: literal instructions, **cannot count/compare numbers**, option-order
  bias, **not injection-resistant** (Octomind: injected "pre-approved" text moved block P 0.76 → 0.48).
- **Laya** (Apache-2.0) — local encoder models (`laya` 421M EN, `laya-multilingual` 322M 100+
  langs, `laya-typed-decisions`), script router, `pip install laya`, ~33 ms on T4, CPU 0.2–0.5 s,
  `laya-serve` exposes a **Jev-compatible** `/v1/systemone`, LangGraph `LayaRouter` for conditional
  edges, calibration helpers (`fit_abstention_thresholds`). Weak zero-shot (needs fine-tuning /
  calibration), ≤ 20 options, 512-token EN context; raw outputs over-confident (ECE 0.466 before
  temperature fitting) — vendor benchmarks **[verify]**.
- **Jev-Mem** (arXiv 2609.23986) — System-One controller gates memory *writes* (type, relation,
  edges) and *reads* (view routing, budget, sufficiency, stop). +11 % LoCoMo, 6.6× faster build,
  −36.7 % query latency. No forget/delete decisions.
- **Consensus guidance** — a typed decision is a *proposal, never authorization*; pair with
  deterministic checks; pin model versions; shuffle option order for high-stakes Choice; shadow
  mode before enforcement; LLM fallback on abstention/timeout.

### 3.3 Token reduction (highest-ROI evidence)

| # | Mechanism | Evidence |
|---|---|---|
| 1 | Provider prompt caching + cache-stable layout | reads 0.1× (Anthropic/OpenAI/DeepSeek), Manus: hit-rate is "the" metric |
| 2 | Cache-safe tool exposure (mask, don't remove) + deferred tool loading | Tool Search: −85 % tool tokens, accuracy 49 → 74 % |
| 3 | Observation masking before summarization | JetBrains: −50–56 % cost, equal/better solve rate; summarization made runs 13–15 % longer |
| 4 | Model cascade / routing | FrugalGPT up to −98 %; RouteLLM ~2×; OmniRouter −10 % at higher accuracy |
| 5 | Sub-agent result contracts (summary + artifact refs) | Anthropic multi-agent: 15× tokens; references avoid the telephone game |
| 6 | Code-mode / programmatic tool calling | −37 % typical, −98.7 % bulk-data |
| 7 | RAG rerank → fewer chunks; contextual retrieval | −67 % retrieval failures with rerank; top-20 → top-5 |
| 8 | Effort / thinking budget per role | −48 to −76 % output tokens |
| 9 | Intent-keyed plan/response cache | APC −50 % cost; embedding semantic caches hit only 0–12 % on agentic workloads |
| 10 | Batch APIs for offline work | −50 %, stacks with caching |
| + | Typed decision models instead of LLM calls for typed verdicts | Jev judge cascade within 0.9 pt of frontier at 41 % cost **[verify]** |
| + | TOON for uniform tabular payloads | −40–58 % on flat data; worse on nested |
| + | LLMLingua-2 passage compression | 2–5× compression; not for code/JSON/cached prefix |

### 3.4 2025–2026 agent patterns vs prismal

| Pattern | Maturity | In prismal? | Action |
|---|---|---|---|
| Orchestrator–worker with context isolation + artifact refs | Mature | Partial (Skynet, spawner) — no artifact contract | **TKE** handoff envelope |
| Context engineering (write / select / compress / isolate) | Mature | Partial (LH1) | **TKE** masking, layout, budgets |
| Prompt-cache-first design | Mature | **No** | **TKE** |
| Tool search / deferred loading / progressive disclosure | Mature/beta | Skills have it; MCP tools do not | **TKE** |
| Code-mode tool execution (CodeAct + MCP as code) | Maturing | `codeact_agent` + sandbox exist, not for MCP chains | **TKE** (optional phase) |
| Model routing / cascades / budgeted batch assignment | Mature | **No** | **AMR** |
| Typed System-1 / System-2 split | Converging | **No** | **DEC** |
| Agentic memory (typed, gated, graph, sleep-time consolidation) | Mature (Mem0/Zep), research (Jev-Mem, A-MEM) | Untyped store | **MEM** |
| ACE playbooks / GEPA prompt optimization | Research / mature in DSPy | **No** | MEM procedural memory (playbooks); GEPA left as future |
| Durable execution / long-horizon harness | Mature (LangGraph 1.0) | Checkpointer yes; harness artefacts no | Out of scope (future) |
| Plan caching (APC) | Research/early prod | **No** | **AMR** |
| MCP 2026-07-28 / A2A v1.0 / AG-UI 1.0 | New standards | v0.3 A2A, session MCP, custom SSE | **PRT** + server/SDK deltas |

---

## 4. Design principles carried over (constitution)

1. **Hexagonal ports, opt-in flags, byte-for-byte graph snapshot when off** — every new layer is a
   `Protocol` port + adapters + `<feature>_enabled=False`.
2. **Provider SDK imports only in `prismal/providers/`** (and the new `prismal/decisions/adapters/`
   for `laya` / `typesafe-sdk`, guarded by deferred imports and an AST test).
3. **Secrets never in `os.environ` mirrors for new code** — resolved through `ConfigSourcePort` /
   `CredentialVault`; never in URLs or logs.
4. **Fail-open on cost/telemetry, fail-closed on security** — a gateway cost lookup failure never
   breaks a turn; a decision-model failure never relaxes a security control.
5. **Typed decisions propose, deterministic code authorizes.**
6. **Measure first** — TKE1 accounting lands before any optimization so savings are provable in the
   eval harness (tokens per successful task, cache-hit ratio, trajectory length).
7. **Strict SemVer minor per phase**, TDD, specs reconciled on ship (Status, TASKS, roadmap).

---

## 5. New core SDDs (repo `prismal`, `specs/`)

| Order | Phase | Spec folder | Target | Depends on | Summary |
|---|---|---|---|---|---|
| 1 | TKE | `token-efficiency/` | `3.13.0` | LH, C, OBS, V | Cache-aware accounting, prompt caching + stable layout, cache-safe tool gating + deferred tools, observation masking, tool-output budgets + TOON, effort per role, handoff envelopes, RAG context budget, eval token gate |
| 2 | GW | `llm-gateway-providers/` | `3.14.0` | W, R, IDN, C, TKE1 | `GatewayPort` + OpenRouter / OpenCode Zen+Go / OmniRoute / generic OpenAI-compatible adapters; data policy; gateway-reported cost + quota → Budget; ToS guards |
| 3 | DEC | `typed-decision-models/` | `3.15.0` | H, V, GW (optional Jev via OpenRouter) | `DecisionPort` + Laya / Jev / LLM-fallback cascade, calibration + shadow mode, integration points (routing, eval judge, HITL, tool proposal, RAG relevance, compaction relevance, swarm evaluator) |
| 4 | AMR | `adaptive-model-routing/` | `3.16.0` | GW, TKE1, DEC, C | `RoutingPolicyPort`: tier map, cascade, budget-aware downgrade, OmniRouter batch assignment, cache affinity, intent-keyed plan cache, batch executor |
| 5 | PRT | `protocol-upgrades-2026/` | `3.17.0` | I, Y | A2A v1.0 (dual-stack with 0.3), MCP 2026-07-28 stateless client + `ttlMs` tool-list cache, AG-UI 1.0 event translator |
| 6 | MEM | `agentic-memory/` | `3.18.0` | DEC, TKE, R | `MemoryPort`, typed + decision-gated writes (Jev-Mem), sufficiency/stop on recall, optional temporal-graph adapter, sleep-time consolidation (batch), ACE playbooks as procedural memory |

## 6. Satellite repos

| Repo | New spec | Target | Content |
|---|---|---|---|
| `prismal-server` | `specs/host-v0.2-usage-gateways-agui/` | `0.2.0` | Per-tenant gateway + decision config from vault; `GET /v1/usage`, `/v1/budget`; usage in `done` event; AG-UI stream mode; A2A v1.0 routes; WebSocket transport; fake-LLM mode for contract tests; admin read endpoints (audit, tenants) |
| `prismal-sdk` | `specs/client-v0.2-usage-agui/` | `0.2.0` | Usage/cache fields, AG-UI event models + iterators, `usage()`/`budget()`, A2A v1.0 methods, unblocks CSB-05-01 via server fake-LLM mode |
| `prismal-dashboard` | `specs/usage-and-gateway-console/` | `0.2.0` (after bootstrap 0.1) | Token/cost/cache panels, gateway quota windows, decision shadow-mode disagreements, budget & audit viewers (closes DSH-FUTURE-03/04) |
| `prismal-webchat` / `prismal-tui` / `prismal-chatbot` | `specs/<bootstrap>/DELTA-2026-10-usage-agui.md` | folded into 0.1 | Render usage; webchat may adopt AG-UI; no engine logic in front-ends |
| `prisma-notebooks` | one notebook per new phase | — | Added as each phase ships |

## 7. Recommended implementation order

1. **Docs hygiene** (roadmap reconciliation, tags v3.6–3.8, GitHub Release v3.12.0) — 0.5 d.
2. **prismal TKE1** (measure) → **TKE2–TKE5** (caching, cache-safe gating, masking, tool budgets).
3. **prismal GW** (OpenRouter first, then OpenCode Zen/Go, then OmniRoute).
4. **prismal DEC** in shadow mode → enable routing + eval-judge integrations.
5. **prismal AMR** and **PRT** (parallelizable).
6. **prismal-server v0.2** (needs TKE1 usage, GW per-tenant config, PRT AG-UI) — also unblocks SDK contract tests.
7. **prismal-sdk v0.2**.
8. **prismal MEM**.
9. Front-ends: **dashboard** (bootstrap + console) → **webchat** → **tui** → **chatbot**.
