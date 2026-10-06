# Token Efficiency (Phase TKE)

Cuts the tokens and dollars each agent turn spends without changing what the
agents do. The work starts by **measuring**: cache-aware accounting and a
committed token baseline land before any optimization, so every saving can be
proven in the eval harness. Everything is **opt-in and additive**: with
`token_efficiency_enabled=False` (the default) the compiled supervisor graph,
`get_tools_for_agent()`, `SecurePromptBuilder.build()`, the `ChatLiteLLM`
construction and the metered `Usage` numbers are identical to v3.12.0. A graph
snapshot test and a disabled-path contract test check this.

Every sub-flag below takes effect **only when the master flag is on**.

| # | Control | Module(s) | Flag |
|---|---|---|---|
| TKE1 | Cache-aware accounting + token report | `budget/{types,usage,meter}.py`, `providers/cost.py`, `eval/metrics_tokens.py` | master |
| TKE2 | Provider prompt caching + cache-stable layout | `providers/prompt_cache.py`, `security/prompt_builder.py` | `prompt_cache_enabled`, `prompt_layout` |
| TKE3 | Cache-safe tool exposure (mask) + deferred tools | `agents/tool_gate.py`, `agents/tool_search.py` | `tool_gating_mode`, `tool_defer_enabled` |
| TKE4 | Observation masking + artifact store | `agents/context_compaction.py`, `agents/artifacts.py` | `context_compaction_strategy="mask_observations"`, `context_compaction_high_water` |
| TKE5 | Tool-output budgets + TOON | `agents/tool_output_budget.py`, `utils/toon.py` | `tool_output_budget_enabled`, `tool_output_toon_enabled` |
| TKE6 | Effort / reasoning budget per role | `providers/prompt_cache.py` (`CacheAwareChatModel`) | `llm_effort_enabled` |
| TKE7 | Multi-agent handoff envelopes | `agents/handoff.py` | `handoff_envelope_enabled` |
| TKE8 | RAG context budget (+ rerank / compression) | `rag/context_budget.py`, `providers/{rerank,compression}.py` | `rag_context_budget_enabled` |

```bash
export PRISMAL_TOKEN_EFFICIENCY_ENABLED=true   # master — nothing below works without it
```

## TKE1 — Measure first

`TokenCounts` and `Usage` gain `cache_read_tokens`, `cache_write_tokens` and
`reasoning_tokens`. `Usage` also gains `cost_source` plus the `cache_hit_ratio`
and `uncached_prompt_tokens` helpers. `extract_token_usage()` reads them from
LangChain's `input_token_details` / `output_token_details` (which
`langchain_litellm` fills for every provider), with a fallback to raw
OpenAI/OpenRouter/Anthropic shapes. `compute_cost_usd()` prices cache reads and
writes at their own rates and follows the precedence fixed in
[ADR-WAVE-001](adr/ADR-WAVE-001-llm-construction-contract.md): gateway-reported
cost, then LiteLLM (cache-aware), then the pricing table (`cache_read` /
`cache_write` entries, falling back to `input`), then zero. With the master flag
on, `CostMeter` normalizes Anthropic-raw counts, prices the cache tokens and
emits `prismal.llm_input_tokens_total{kind=uncached|cache_read|cache_write}`,
`prismal.llm_reasoning_tokens_total` and the `prismal.llm_cache_hit_ratio`
histogram. `RunSummary` exposes the same cache fields.

The eval harness reports a `TokenReport` (tokens per successful task,
cache-hit ratio, solve rate, LLM calls per task, input cost per success) in
every scorecard. The CI-style gate compares it against a committed baseline:

```bash
python -m prismal.eval run --suite tests/eval/sets/smoke.yaml --live-api \
    --json out.json --write-token-baseline tests/eval/baselines/tokens.json   # capture
python -m prismal.eval token-gate --current out.json [--report-only]           # gate
```

The gate fails when tokens per successful task grow more than 10 % or the
cache-hit ratio drops more than 0.10 below the baseline. Use `--report-only`
until two green runs have been recorded.

**Known limitation: baseline not yet committed.** The `TokenReport` is built
from trajectory messages. A local run on 2026-10-05 (Ollama `qwen3:14b`)
recorded 0 tokens, because internal LLM calls (supervisor routing) and
rebuilt direct answers carry no `usage_metadata` in the graph's update stream.
This was already true of `Trajectory.tokens` in v3.12. A meaningful baseline
needs a live run and, ideally, token capture from the per-run `CostMeter` /
`RunSummary` instead of from state messages. Until then the PLAN §4 savings
targets are unmeasured.

## TKE2 — Prompt caching and a cache-stable layout

`ProviderRegistry.get_llm(..., agent=)` returns a `CacheAwareChatModel`
wrapping the `ChatLiteLLM` when `prompt_cache_enabled` is on. The wrapper is an
outer composition layer (ADR-WAVE-001 §2.2). It only touches the request side
and returns the inner result unchanged:

- **Anthropic (explicit cache):** `cache_control` breakpoints on the last
  tool, the end of the system prompt and the last stable message. At most 4
  breakpoints are placed, inside the 20-block lookback, and none when the
  prefix is below the per-model minimum.
- **OpenAI (automatic cache):** a `prompt_cache_key` of the form
  `prismal:<agent>:<prefix-hash>`, plus an optional `prompt_cache_retention`.
- **Gemini / DeepSeek / unknown:** no-op.
- **Tool binding:** tools are bound sorted by name and serialized with
  `stable_dumps`, so the tool prefix is byte-stable.

The agent name comes from `agent=` or, at call time, from the
`langgraph_node` metadata, so none of the 26 agents needs a code change.
Volatile provider facts live in `config/prompt_cache.yaml`. They were verified
against litellm 1.86.2 in `specs/token-efficiency/VERIFY.md`.

### Canary relocation (DD-TKE-004, security)

`SecurePromptBuilder` puts a per-request canary in the system prompt, and that
defeats any prefix cache. With `prompt_layout="cache_stable"`:

- the system prompt carries static instructions plus a **prefix canary**
  (`HMAC(prompt_canary_secret, tenant, daily bucket)`), which is stable within
  the bucket;
- the per-request canary, request id and timestamp move to a
  `<turn_context>` block at the tail of the user turn;
- `builder.detect_leak(output)` and
  `GuardrailsEngine.validate_output(text, canary=builder.canaries)` check
  **both** canaries;
- any failure falls back to the legacy layout, never drops the canary, and
  increments `prismal.prompt_cache_layout_fallbacks_total`.

Leak-detection recall on the Phase H red-team corpus is identical (100 %)
for both layouts (`tests/unit/security/test_canary_leak_cache_stable.py`).
That parity is a release blocker. Set `PRISMAL_PROMPT_CANARY_SECRET` through
your config source or vault. Without it, a per-process random secret is used,
which is stable within a process. The supervisor also moves its recalled
memory out of the system prompt into the tail block.

```bash
export PRISMAL_PROMPT_CACHE_ENABLED=true
export PRISMAL_PROMPT_LAYOUT=cache_stable
export PRISMAL_PROMPT_CANARY_SECRET=...      # via ConfigSourcePort / CredentialVault
```

## TKE3 — Cache-safe tool exposure (delta to LH2)

The provider prompt cache is ordered tools → system → messages, so LH2's
"remove tools per phase" invalidates the whole cache on every phase change.
`tool_gating_mode="mask"` keeps the **exposed list full and name-sorted**
whatever the phase. The phase-narrowed set becomes an **execution allow-set**
(`CompositeToolProvider.allowed_tool_names()`). A call outside it is never
executed:

- it gets `ToolMessage("tool '<name>' not available in phase <phase>")`;
- it is audited hash-first as `tool_gate_masked_call` and counted;
- `react_loop` and `ActionInterceptor.set_tool_allow_set()` both enforce
  this (fail-closed);
- the allowed names are appended to the tail user turn as a hint, never to the
  system prompt.

`tool_gate_react_kwargs(state, agent_name=...)` returns the `react_loop`
kwargs. If the allow-set cannot be computed it fails **closed** with an empty
allow-set. `build_default_tool_provider(settings)` now wires the LH phase map
and the gating mode when `tool_gating_enabled` is on. `remove` (the default) is
LH2 exactly, and the LH tests pass unmodified.

**Deferred tools:** with `tool_defer_enabled`, an agent with more than
`tool_defer_threshold` (default 30) tools exposes only `tool_defer_core` plus a
BM25 `search_tools(query, k)` meta-tool. Hits are appended to the exposed list
for the rest of the run and are never reordered (`plan_deferred_tools`,
`react_loop(deferred_tools=, rebind_llm=)`). On models with provider-native
tool search (the Anthropic family, verified TKE0-04) the deferred tools are
marked `defer_loading=True` instead. `ToolSearchIndex.refresh(tools,
ttl_ms=)` is the seam for MCP `ttlMs` (Phase PRT).

## TKE4 — Observation masking and the artifact store (delta to LH1)

`context_compaction_strategy="mask_observations"` replaces the content of every
`ToolMessage` older than the last `context_mask_keep_turns` human/AI turns with
`[observation elided: tool=… artifact=… tokens≈…]`:

- the full payload is stored first;
- the replacement keeps the **same id and tool_call_id**, so `add_messages`
  replaces it in place with order and pairing preserved;
- if the store write fails, the observation is not masked (data is never lost).

An optional `relevance_fn` (the DEC hook) keeps relevant observations. A
high/low-water band (`context_compaction_high_water` / `_low_water`, measured in
estimated tokens) compacts once per crossing. `high_water=0` keeps the LH
trigger. Note that each stub costs about 13 tokens, so a very long history may
stay above low-water once every eligible observation has been masked.

`ArtifactStorePort` has in-memory (default) and SQLite adapters. Artifacts
are scoped to tenant and run: a mismatch raises `ArtifactNotFound`, which looks
the same as a missing id, and is audited. Each artifact has a TTL
(`artifact_ttl_s`). The `read_artifact(id, start, end)` tool returns slices
through the input sanitizer.

## TKE5 — Tool-output budgets and TOON

With `tool_output_budget_enabled`, a tool output larger than its budget
(`tool_output_max_tokens_by_tool` or the 2 000-token default) is cut to a 60 %
head plus 40 % tail excerpt. A `[truncated: … artifact=<id>]` marker points to
the full output in the artifact store. The budget is applied in `react_loop`,
where it replaces the legacy 4 000-char cap, and in the `@prismal_node`
middleware (`tool_output_budget_middleware`, just outside
`hardening_middleware`). In both places it runs **after** the Phase H
sanitize/taint step. With `tool_output_toon_enabled`, uniform tables
(lists of flat dicts) are encoded as TOON. Nested data stays JSON.

## TKE6 — Effort per role

`llm_effort_by_agent` (`low|medium|high`), `llm_max_tokens_by_agent` and the
`recommended` profile (planners, critics and judges get `high`; everyone else
gets `low`) are applied at call time by `CacheAwareChatModel`.
`reasoning_effort` is passed only to models where `litellm.supports_reasoning`
is true. Effort stays constant per agent for the whole run, which keeps it
cache-safe.

## TKE7 — Handoff envelopes

With `handoff_envelope_enabled`, worker and proposer outputs carry a
`HandoffEnvelope`: a deterministic summary of at most
`handoff_summary_max_tokens` tokens, with the full text stored as an artifact.
Reducers and aggregators read the summary and fetch artifacts only on demand.
Envelope fields are optional, so legacy checkpoints still load.

Wired patterns: Skynet, Mixture-of-Agents, Kokoro (judge input only; souls
still see each other's raw text between rounds), `parallel_research`, BRP and
the spawner. In BRP the reviewers must see the full spec and implementation,
so the envelope is attached only as metadata. Note also that with envelopes on,
Skynet's `concat` and `first_success` reducers return the envelope text
(summary plus artifact pointer), not the raw text. Run artifact stores live in
memory until the host calls `clear_run_artifact_store(run_id, tenant=...)`.
The registry is keyed by `(tenant, run_id)` and holds at most 256 runs (least
recently used are evicted first). Every artifact scope comes from the
`session_id` and `metadata.org_id`; when there is no `session_id`, a fresh
anonymous scope (`anon-<random>`) is used, never a shared constant, so runs
without a session never see each other's artifacts.

## TKE8 — RAG context budget

With `rag_context_budget_enabled`, retrieval asks for `rag_retrieve_k`
(default 20) chunks, reranks them, and passes on only the chunks that fit in
`rag_context_budget_tokens` (default 3 000). Reranking uses a cross-encoder
under `rag_rerank_enabled` with the `[rerank]` extra, and falls back to the
score ranking otherwise. With `rag_passage_compression_enabled` and the
`[compression]` extra, LLMLingua-2 compresses prose passages, but never code,
JSON or cached prefixes. Wired engines: `RAGEngine`, CRAG (and therefore
`self_rag`), HyDE and Hybrid (and through them `adaptive`). Fusion,
hierarchical, multi-vector, multimodal and federated are not wired yet,
because each needs its own retrieve-to-context design.

## LH delta (MODIFIED / ADDED)

| LH requirement | TKE change | Kind | Back-compat default |
|---|---|---|---|
| RF-LH-006 / SPEC-LH-GAT-002 | mask mode: list not narrowed; allow-set enforced at execution | MODIFIED | `tool_gating_mode="remove"` |
| SPEC-LH-GAT-003 | name-sorted list in mask mode; `allowed_tool_names` | MODIFIED | unchanged in `remove` |
| SPEC-LH-CTX-001 | `MASK_OBSERVATIONS` strategy; `CompactionResult.replaced` | ADDED | strategy stays `truncate` |
| RF-LH-004 | high/low-water hysteresis | MODIFIED | `context_compaction_high_water=0` |

## Deferred

TKE10 (code mode for MCP chains, optional P3) is deferred to a later minor
release.
