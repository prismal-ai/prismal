# Prismal Token Efficiency — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | TKE |
| **Target package version** | `3.13.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/token-efficiency/PLAN.md` |
| **Architecture** | `specs/token-efficiency/ARCHITECTURE.md` |
| **TASKS** | `specs/token-efficiency/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`; frozen dataclasses for value objects; constructors accept `settings: Settings | None = None`.
- Hot-path helpers (extraction, pricing, cache policy, estimation, TOON, budgets) are sync and **never raise** (fail-open on cost/telemetry). Security checks (masked-tool enforcement, artifact tenant scope, canary layout) **fail closed**.
- Provider/model-library imports (`litellm`, cross-encoder, LLMLingua) live only in `prismal/providers/` (Critical Rule #4). No `prismal.mcp`/`prismal.skills` import in `prismal/agents/**` (Rule #9).
- Every sub-flag is effective only when `token_efficiency_enabled=True`. With the master off, every public entry point below returns its pre-TKE result unchanged.
- Run state lives under `state["metadata"]["tke"]` (serializable markers only); live objects (artifact store handle, tool-search index, activated deferred tools) live in an in-process registry keyed by `session_id`, mirroring `budget/resolve.py` and `agents/context_compaction.py::_RUN_ENGINES`.
- Requirement keywords: **MUST** / **SHOULD** / **MAY**. `[verify]` = provider fact confirmed by a TKE0 task before build.

## Module Summary

New/extended modules are listed in `ARCHITECTURE.md` §3.1; each requirement below names its module.

## Delta to Loop Hardening (`specs/loop-hardening/`, shipped 3.7.0)

| LH requirement | TKE change | Kind | Back-compat default |
|---|---|---|---|
| RF-LH-006 / SPEC-LH-GAT-002 (`CompositeToolProvider` narrows by phase) | In `mask` mode the list is not narrowed; the narrowed set becomes an execution-time allow-set (SPEC-TKE-GAT-001) | **MODIFIED** | `tool_gating_mode="remove"` = LH2 exactly |
| SPEC-LH-GAT-003 (`tool_registry` threading) | `get_tools_for_agent*` return name-sorted list in `mask` mode; expose `allowed_tool_names` | **MODIFIED** | unchanged in `remove` |
| — | Execution-time enforcement, synthetic `ToolMessage`, tail hint (GAT-001); deferred loading + `search_tools` (DEF-001) | **ADDED** | `tool_defer_enabled=False` |
| RF-LH-003 / SPEC-LH-CTX-001 (`CompactionStrategy`) | New member `MASK_OBSERVATIONS` (MSK-001); `CompactionResult.replaced` | **ADDED** | strategy default stays `"truncate"` |
| RF-LH-004 (trigger) | High/low-water hysteresis (MSK-002) | **MODIFIED** | `context_compaction_high_water=0` = LH trigger |
| RF-LH-005, 007, 008, 009, 011 | none | unchanged | — |

---

## SPEC-TKE-ACC-001: Usage fields (`budget/types.py`) — MUST
```python
@dataclass(frozen=True)
class TokenCounts:
    prompt_tokens: int = 0            # unchanged semantics: total input incl. cached
    completion_tokens: int = 0        # unchanged semantics: total output incl. reasoning
    cache_read_tokens: int = 0        # NEW
    cache_write_tokens: int = 0       # NEW
    reasoning_tokens: int = 0         # NEW (subset of completion_tokens)
    @property
    def total(self) -> int: ...       # unchanged: prompt + completion
    @property
    def uncached_prompt_tokens(self) -> int: ...   # max(0, prompt - cache_read - cache_write)
    def normalized(self) -> TokenCounts: ...       # Anthropic-raw -> prompt = input + read + write
@dataclass(frozen=True)
class Usage:
    # existing: prompt_tokens, completion_tokens, cost_usd, calls, wall_clock_s, estimated (unchanged)
    cache_read_tokens: int = 0        # NEW — summed by __add__
    cache_write_tokens: int = 0       # NEW
    reasoning_tokens: int = 0         # NEW
    cost_source: str = ""             # NEW — last non-empty source; "" when unknown
    @property
    def cache_hit_ratio(self) -> float: ...   # cache_read / prompt_tokens; 0.0 when prompt == 0
```
- WHEN two `Usage` values are added, THE SYSTEM SHALL sum each of the three new fields and keep `total_tokens == prompt_tokens + completion_tokens`.
- THE SYSTEM SHALL construct `Usage()`/`TokenCounts()` with every new field `0`/`""`, so existing positional/keyword constructions are unaffected.

## SPEC-TKE-ACC-002: Extraction (`budget/usage.py`) — MUST
```python
def extract_token_usage(message: object) -> TokenCounts: ...  # signature unchanged
```
Precedence for the new fields (prompt/completion precedence is unchanged):
1. `usage_metadata["input_token_details"]["cache_read" | "cache_creation"]`, `usage_metadata["output_token_details"]["reasoning"]` (LangChain standard).
2. `response_metadata["token_usage"]`: `prompt_tokens_details.cached_tokens`, `cache_write_tokens` / `prompt_tokens_details.cache_write_tokens`, Anthropic `cache_read_input_tokens` / `cache_creation_input_tokens`, `completion_tokens_details.reasoning_tokens` **[verify]** (TKE0-01).
3. Zero.
- WHEN a message carries any supported cache/reasoning shape, THE SYSTEM SHALL populate the matching `TokenCounts` field with the non-negative integer value.
- IF Anthropic-raw semantics are detected (`cache_read + cache_write > prompt_tokens`), THEN `TokenCounts.normalized()` SHALL return `prompt_tokens = input + cache_read + cache_write`; `CostMeter` SHALL apply it only WHERE `token_efficiency_enabled` is true (so v3.12.0 numbers never move with the master off).
- THE SYSTEM SHALL return the same `prompt_tokens`/`completion_tokens` as v3.12.0 for every fixture lacking cache fields (contract test).

## SPEC-TKE-ACC-003: Cache-aware pricing (`providers/cost.py`) — MUST
```python
CostSource = Literal["gateway", "litellm", "table", "none"]
@dataclass(frozen=True)
class CostEstimate:
    cost_usd: float
    source: str                       # CostSource
    @property
    def estimated(self) -> bool: ...  # source not in {"gateway", "litellm"}
def compute_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int, *, settings: Settings | None = None,
    cache_read_tokens: int = 0, cache_write_tokens: int = 0,      # NEW
    reported_cost_usd: float | None = None,                       # NEW — filled by GW adapters
    response: object | None = None,                               # NEW — raw LiteLLM response, if any
) -> CostEstimate: ...
```
> **Amended 2026-10-05 (M0/X0-03):** TKE1 owns the full precedence table; GW5 (`cost_mode`) and AMR5 (`batch`) extend it as recorded in `docs/adr/ADR-WAVE-001-llm-construction-contract.md` §2.3.

`budget_pricing[model]` entries gain optional `cache_read` / `cache_write` (USD per 1k). Precedence: `reported_cost_usd is not None` → `"gateway"`; `litellm.completion_cost(response)` when a LiteLLM `ModelResponse` is available, else `litellm.cost_per_token(..., cache_read_input_tokens=, cache_creation_input_tokens=)` **[verify]** → `"litellm"`; table → `"table"` (cache tokens priced at `cache_read`/`cache_write`, falling back to `input` when absent); else `"none"`.
- WHERE `reported_cost_usd` is provided, THE SYSTEM SHALL return it unchanged with `source="gateway"` and `estimated=False`.
- WHEN all new keyword arguments are left at defaults, THE SYSTEM SHALL return exactly the v3.12.0 `CostEstimate` (contract test).
- IF any pricing path raises, THEN THE SYSTEM SHALL fall through to the next path and never raise.

## SPEC-TKE-ACC-004: Metering and metrics (`budget/meter.py`, `monitoring/otel.py`) — MUST
```python
class CostMeter:
    def record_response(
        self, message: object, model: str, *, wall_clock_s: float = 0.0,
        agent: str | None = None, pattern: str | None = None,
        reported_cost_usd: float | None = None,   # NEW
    ) -> Usage: ...
```
- WHERE `token_efficiency_enabled` is true, WHEN a response is recorded, THE SYSTEM SHALL pass cache tokens to `compute_cost_usd`, increment `prismal.llm_input_tokens_total{agent,model,kind}` for `kind ∈ {uncached, cache_read, cache_write}`, record `prismal.llm_cache_hit_ratio{agent}` and add `prismal.llm_reasoning_tokens_total{agent,model}`.
- WHILE `token_efficiency_enabled` is false, THE SYSTEM SHALL compute `cost_usd` with v3.12.0 arguments only and emit only the pre-existing `budget_*` metrics.

## SPEC-TKE-ACC-005: Run summary and eval metrics — MUST
```python
# monitoring/observability_types.py — additive fields on RunSummary
cache_read_tokens: int = 0
cache_write_tokens: int = 0
reasoning_tokens: int = 0
cache_hit_ratio: float = 0.0
# eval/metrics_tokens.py
@dataclass(frozen=True)
class TokenReport:
    tokens_per_successful_task: float; cache_hit_ratio: float; solve_rate: float
    mean_trajectory_length: float     # LLM calls per task
    input_cost_usd_per_success: float
def build_token_report(results: Sequence[EvalCaseResult]) -> TokenReport: ...
def load_baseline(path: str = "tests/eval/baselines/tokens.json") -> TokenReport: ...
def check_token_regression(
    current: TokenReport, baseline: TokenReport, *,
    max_token_regression: float = 0.10, max_hit_ratio_drop: float = 0.10,
) -> list[str]: ...   # empty == pass; each string names one violated threshold
```
- WHEN the eval harness finishes a run, THE SYSTEM SHALL include the `TokenReport` fields in its report output.
- IF `tokens_per_successful_task` exceeds baseline by > 10 % or `cache_hit_ratio` drops by > 0.10 absolute, THEN `check_token_regression` SHALL return a non-empty list and the CI job SHALL exit non-zero.

## SPEC-TKE-CCH-001: Cache family and policy (`providers/prompt_cache.py`) — MUST
```python
class CacheFamily(StrEnum):
    ANTHROPIC_EXPLICIT = "anthropic_explicit"   # claude-*, anthropic/*, openrouter/anthropic/*, bedrock/vertex claude
    OPENAI_AUTO = "openai_auto"                 # gpt-*, o*, openai/*, openrouter/openai/*
    GEMINI_IMPLICIT = "gemini_implicit"
    DEEPSEEK_AUTO = "deepseek_auto"
    NONE = "none"
def detect_cache_family(model: str) -> CacheFamily: ...   # pure; unknown -> NONE
@dataclass(frozen=True)
class CachePolicy:
    ttl: Literal["5m", "1h"] = "5m"
    max_breakpoints: int = 4                    # [verify]
    lookback_blocks: int = 20                   # [verify]
    min_prefix_tokens: Mapping[str, int] = field(default_factory=dict)  # family/model -> tokens, from config
    openai_retention: str | None = None         # e.g. "24h" [verify param name]
def load_cache_policy(settings: Settings) -> CachePolicy: ...
```
- WHERE the call goes through OpenRouter (GW), cache hits also depend on OpenRouter keeping the same upstream provider; GW sends a hashed sticky `session_id` (SPEC-GW-OR-001) and TKE1 reads `prompt_tokens_details.cached_tokens` / `cache_write_tokens` and `cache_discount` from the gateway response.
- WHEN `detect_cache_family` receives `"openrouter/anthropic/claude-sonnet-4-5"`, THE SYSTEM SHALL return `ANTHROPIC_EXPLICIT`; an unknown string SHALL return `NONE`.

## SPEC-TKE-CCH-002: `apply_cache_policy` — MUST
```python
@dataclass(frozen=True)
class CacheApplied:
    messages: list[BaseMessage]; tools: list[dict[str, Any]]; request_kwargs: dict[str, Any]
    breakpoints: int; family: CacheFamily
    prefix_hash: str                   # sha256 of stable_dumps(tools) + system text
def apply_cache_policy(
    messages: Sequence[BaseMessage], tools: Sequence[dict[str, Any]], *,
    model: str, agent: str, policy: CachePolicy, tenant: str | None = None,
) -> CacheApplied: ...   # pure, never raises; on error returns inputs unchanged, family=NONE
```
- WHERE family is `ANTHROPIC_EXPLICIT`, THE SYSTEM SHALL set `cache_control={"type":"ephemeral"(, "ttl")}` on the last tool definition, the end of the system prompt and the last stable message, never exceeding `max_breakpoints`, and SHALL move the message breakpoint so it is never more than `lookback_blocks` blocks behind the tail.
- WHERE family is `OPENAI_AUTO`, THE SYSTEM SHALL set `request_kwargs["prompt_cache_key"] = f"prismal:{agent}:{prefix_hash[:16]}"` (plus retention when configured) and leave messages untouched.
- IF the estimated prefix is below `min_prefix_tokens` for the model, THEN THE SYSTEM SHALL place no breakpoint.
- WHERE family is `GEMINI_IMPLICIT`, `DEEPSEEK_AUTO` or `NONE`, THE SYSTEM SHALL return inputs unchanged with empty `request_kwargs`.
- WHEN `tenant` is given and `prompt_cache_scope="tenant"`, THE SYSTEM SHALL include the tenant id in `prefix_hash`.

## SPEC-TKE-CCH-003: Registry wiring (`providers/registry.py`) — MUST
> **Amended 2026-10-05 (M0/X0-03):** the merged `get_llm` signature, the construction pipeline and the wrapper stack order (`CacheAwareChatModel` is the *outer* composition wrapper over `ChatLiteLLM` **or** GW's `GatewayChatLiteLLM`, input-side only, returning the inner `ChatResult` unchanged) are defined by `docs/adr/ADR-WAVE-001-llm-construction-contract.md` §2.1–2.2, which wins over this snippet.
```python
class CacheAwareChatModel(BaseChatModel):          # in providers/prompt_cache.py
    inner: BaseChatModel
    policy: CachePolicy
    agent: str | None = None                        # None -> config["metadata"]["langgraph_node"]
    def bind_tools(self, tools: Sequence[Any], **kw: Any) -> Runnable[...]: ...   # sorts by name
class ProviderRegistry:
    def get_llm(
        self, model: str | None = None, streaming: bool = False,
        temperature: float | None = None, *, agent: str | None = None,   # NEW (TKE2/TKE6)
    ) -> BaseChatModel: ...
```
- WHERE `prompt_cache_enabled` is true, THE SYSTEM SHALL return a `CacheAwareChatModel` wrapping the `ChatLiteLLM` instance; agents SHALL need no code change.
- WHILE `prompt_cache_enabled` is false, THE SYSTEM SHALL return the identical `ChatLiteLLM` construction as v3.12.0.
- WHEN tools are bound through the wrapper, THE SYSTEM SHALL order them by `name` and serialize schemas with `stable_dumps`.

## SPEC-TKE-LAY-001: Cache-stable prompt layout (`security/prompt_builder.py`) — MUST
```python
class PromptLayout(StrEnum):
    LEGACY = "legacy"
    CACHE_STABLE = "cache_stable"
@dataclass(frozen=True)
class TurnContext:
    canary: str                        # per-request canary (unchanged generation)
    request_id: str
    now_iso: str
    extra: Mapping[str, str] = field(default_factory=dict)
class SecurePromptBuilder:
    def build(self, *, system: str, user: str,
              layout: PromptLayout = PromptLayout.LEGACY,          # NEW
              turn_context: TurnContext | None = None) -> list[BaseMessage]: ...
    def prefix_canary(self, tenant: str | None) -> str: ...        # NEW, HMAC(secret, tenant, ttl bucket)
    def detect_leak(self, output: str) -> LeakVerdict: ...         # checks request + prefix canaries
def stable_dumps(obj: object) -> str: ...  # utils/stable_json.py: sort_keys, (",", ":"), ensure_ascii=False
```
- WHERE layout is `CACHE_STABLE`, THE SYSTEM SHALL emit a system message containing only static instructions plus the prefix canary, and append a `<turn_context>` block (canary, request id, timestamp) at the tail of the latest user turn.
- WHEN a model output contains either the request canary or the prefix canary, `detect_leak` SHALL report a leak and the existing H leak handling SHALL fire.
- IF the builder cannot construct the `CACHE_STABLE` layout, THEN THE SYSTEM SHALL fall back to `LEGACY`, increment `prismal.prompt_cache_layout_fallbacks_total` and never omit the canary (fail-closed).
- THE SYSTEM SHALL produce byte-identical system-message text for two consecutive requests of the same agent/tenant within one prefix-canary TTL bucket.

## SPEC-TKE-GAT-001: Mask-mode gating (`agents/tool_gate.py`) — MUST — **MODIFIES RF-LH-006, SPEC-LH-GAT-002/003**
```python
ToolGatingMode = Literal["remove", "mask"]
class CompositeToolProvider:
    def allowed_tool_names(self, *, agent_name: str, capabilities: list[str] | None,
                           phase: str | None) -> frozenset[str] | None: ...   # NEW; None = no restriction
@dataclass(frozen=True)
class GateVerdict:
    allowed: bool; tool: str; phase: str | None; reason: str = ""
def check_tool_call(tool_name: str, allow_set: frozenset[str] | None, phase: str | None) -> GateVerdict: ...
def masked_tool_message(call: ToolCall, verdict: GateVerdict) -> ToolMessage: ...
def allowed_tools_hint(allow_set: frozenset[str], phase: str) -> str: ...
```
- WHILE `tool_gating_mode="remove"` (default), THE SYSTEM SHALL behave exactly as LH2 (RF-LH-006 unchanged).
- WHERE `tool_gating_mode="mask"`, THE SYSTEM SHALL expose the agent's full, name-sorted tool list independent of phase and compute the phase allow-set via `allowed_tool_names`.
- IF a model calls a tool outside the allow-set, THEN THE SYSTEM SHALL NOT execute it, SHALL return `ToolMessage("tool '<name>' not available in phase <phase>", tool_call_id=…)`, SHALL audit `tool_gate_masked_call` (hash-first) and increment `prismal.tool_gate_masked_calls_total{agent,phase}` — enforced in both `react_loop` and `ActionInterceptor` (fail-closed).
- WHERE mask mode is active, THE SYSTEM SHALL append `allowed_tools_hint` to the tail turn-context block, not to the system prompt.

## SPEC-TKE-DEF-001: Deferred tool loading (`agents/tool_search.py`) — MUST (native path SHOULD) — **ADDED to LH2**
```python
@dataclass(frozen=True)
class ToolSearchHit:
    name: str; score: float; schema: dict[str, Any]
class ToolSearchIndex:
    def __init__(self, tools: Sequence[BaseTool], *, k1: float = 1.5, b: float = 0.75) -> None: ...
    def search(self, query: str, k: int = 5) -> list[ToolSearchHit]: ...   # BM25 over name + description
def make_search_tools_tool(index: ToolSearchIndex, activate: Callable[[Sequence[str]], None]) -> BaseTool: ...
def split_core_and_deferred(tools: Sequence[BaseTool], *, core: frozenset[str],
                            threshold: int) -> tuple[list[BaseTool], list[BaseTool]]: ...
def supports_native_tool_search(model: str) -> bool: ...   # capability table, [verify]
```
- WHERE `tool_defer_enabled` is true, WHEN an agent's resolved tools exceed `tool_defer_threshold` (default 30), THE SYSTEM SHALL expose only core tools plus `search_tools(query, k)`.
- WHEN `search_tools` returns hits, THE SYSTEM SHALL append those tools to the agent's exposed list for the rest of the run (append-only, never reordered or removed).
- WHERE `supports_native_tool_search(model)` is true, THE SYSTEM SHALL mark deferred tools `defer_loading=True` instead of using the meta-tool.
- THE SYSTEM SHALL keep a seam `ToolSearchIndex.refresh(tools, *, ttl_ms: int | None = None)` for MCP `ttlMs`/`cacheScope` (PRT, forward reference).

## SPEC-TKE-ART-001: Artifact store (`agents/artifacts.py`) — MUST
```python
@dataclass(frozen=True)
class ArtifactRef:
    id: str                            # secrets.token_urlsafe(16); unguessable
    run_id: str; tenant: str; tool: str | None; tokens_estimate: int; created_at: float
    kind: str                          # "tool_output" | "handoff" | "rag" | ...
@dataclass(frozen=True)
class ArtifactSlice:
    ref: ArtifactRef; content: str; start: int; end: int; total_chars: int
@runtime_checkable
class ArtifactStorePort(Protocol):
    def put(self, content: str, *, run_id: str, tenant: str, kind: str,
            tool: str | None = None, ttl_s: int | None = None) -> ArtifactRef: ...
    def get(self, artifact_id: str, *, run_id: str, tenant: str,
            start: int = 0, end: int | None = None) -> ArtifactSlice: ...
    def purge_expired(self, now: float | None = None) -> int: ...
class InMemoryArtifactStore: ...                         # default
class SqliteArtifactStore:                               # filesystem/SQLite adapter
    def __init__(self, path: str, *, settings: Settings | None = None) -> None: ...
def make_read_artifact_tool(store: ArtifactStorePort, *, run_id: str, tenant: str) -> BaseTool: ...
```
- IF `get` is called with a `tenant` or `run_id` different from the stored one, THEN THE SYSTEM SHALL raise `ArtifactNotFound` (indistinguishable from a missing id) and audit the attempt.
- WHEN `read_artifact(id, start, end)` returns content, THE SYSTEM SHALL route it through the same sanitize/taint path as the original tool output.
- WHEN an artifact's TTL (`artifact_ttl_s`, default 86 400) elapses, `purge_expired` SHALL delete it.

## SPEC-TKE-MSK-001: Observation masking (`agents/context_compaction.py`) — MUST — **ADDED to LH1**
```python
class CompactionStrategy(StrEnum):
    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    MASK_OBSERVATIONS = "mask_observations"   # NEW
OBSERVATION_STUB = "[observation elided: tool={tool} artifact={artifact_id} tokens≈{tokens}]"
RelevanceFn = Callable[[ToolMessage, Sequence[BaseMessage]], float]   # optional DEC hook
class ContextCompactor:
    def __init__(self, *, settings: Settings | None = None, summarizer_fn: ... = None,
                 budget_guard: BudgetGuard | None = None,
                 artifact_store: ArtifactStorePort | None = None,     # NEW
                 relevance_fn: RelevanceFn | None = None) -> None: ...  # NEW
    async def mask_observations(self, messages: Sequence[BaseMessage], *,
                                run_id: str, tenant: str) -> CompactionResult: ...
```
`CompactionResult` gains `replaced: tuple[BaseMessage, ...] = ()`; `to_state_update` appends them.

- WHERE strategy is `MASK_OBSERVATIONS`, THE SYSTEM SHALL replace the content of every `ToolMessage` older than the last `context_mask_keep_turns` (default 10) human/AI turns with `OBSERVATION_STUB`, storing the full payload in the artifact store first.
- THE SYSTEM SHALL emit replacements as `ToolMessage`s with the **same `id` and `tool_call_id`** so `add_messages` replaces them in place (position preserved, no `RemoveMessage`).
- IF the artifact store write fails, THEN THE SYSTEM SHALL leave that observation unmasked (never lose data) and increment `prismal.observations_mask_errors_total`.
- WHERE `relevance_fn` is injected, THE SYSTEM SHALL keep observations scoring ≥ `context_mask_relevance_keep` unmasked; without it, behavior is purely positional.

## SPEC-TKE-MSK-002: Hysteresis — MUST — **MODIFIES RF-LH-004**

- WHILE `context_compaction_high_water == 0` (default), THE SYSTEM SHALL use the LH trigger (`context_compaction_max_messages` / token threshold) unchanged.
- WHERE `context_compaction_high_water > 0`, WHEN the estimated tokens of `state["messages"]` exceed it, THE SYSTEM SHALL compact until the estimate is ≤ `context_compaction_low_water`, and SHALL NOT compact again until high-water is exceeded again.
- `_validate_token_efficiency` SHALL reject `low_water >= high_water` when `high_water > 0`.

## SPEC-TKE-OUT-001: Tool-output budget (`agents/tool_output_budget.py`) — MUST
```python
@dataclass(frozen=True)
class BudgetedOutput:
    content: str; truncated: bool; original_tokens: int; artifact: ArtifactRef | None
class ToolOutputBudget:
    def __init__(self, *, settings: Settings | None = None,
                 artifact_store: ArtifactStorePort | None = None) -> None: ...
    def max_tokens_for(self, tool_name: str) -> int: ...   # tool_output_max_tokens_by_tool or default 2,000
    def apply(self, tool_name: str, output: object, *, run_id: str, tenant: str) -> BudgetedOutput: ...
def estimate_tokens(text: str) -> int: ...   # utils/token_estimate.py: ceil(len/4); documented ±15 %
```
- WHERE `tool_output_budget_enabled` is true, WHEN a tool output's estimate exceeds its budget, THE SYSTEM SHALL replace it with a head (60 %) + tail (40 %) excerpt within budget, a `[truncated: … artifact=<id>]` marker and an artifact ref — applied in both `react_loop` and the `@prismal_node` middleware.
- THE SYSTEM SHALL apply the budget **after** the H taint/sanitize step so the artifact holds the sanitized payload.

## SPEC-TKE-TOON-001: TOON encoder (`utils/toon.py`) — SHOULD
```python
def is_uniform_tabular(obj: object) -> bool: ...   # list[dict] with identical keys and scalar values
def encode_toon(rows: Sequence[Mapping[str, object]], *, name: str = "items") -> str: ...
def maybe_toon(obj: object) -> str: ...            # TOON when uniform, else stable_dumps(obj)
```
- WHERE `tool_output_toon_enabled` is true, WHEN a tool/RAG result is uniform tabular, THE SYSTEM SHALL encode it as TOON; nested or non-uniform data and all output schemas SHALL stay JSON.

## SPEC-TKE-EFF-001: Effort per role — MUST

Settings `llm_effort_by_agent: dict[str, Literal["low","medium","high"]]`, `llm_max_tokens_by_agent: dict[str, int]`, `llm_effort_profile: Literal["none","recommended"] = "none"` (`recommended` = supervisor/routers/workers `low`, planner/critic/judges `high`).

- WHERE `llm_effort_enabled` is true, WHEN `get_llm(agent=…)` targets a model for which `litellm.supports_reasoning(model)` **[verify]** is true, THE SYSTEM SHALL pass `reasoning_effort` and the per-agent `max_tokens`; otherwise it SHALL omit `reasoning_effort`.
- THE SYSTEM SHALL keep effort constant for an agent for the whole run (cache-safe).

## SPEC-TKE-HND-001: Handoff envelope (`agents/handoff.py`) — MUST
```python
@dataclass(frozen=True)
class HandoffEnvelope:
    summary: str
    artifacts: tuple[ArtifactRef, ...] = ()
    status: Literal["ok", "partial", "failed"] = "ok"
    usage: Usage | None = None
def build_envelope(text: str, *, store: ArtifactStorePort | None, run_id: str, tenant: str,
                   max_summary_tokens: int, usage: Usage | None = None,
                   status: str = "ok") -> HandoffEnvelope: ...
def envelope_to_message_text(env: HandoffEnvelope) -> str: ...
```
- WHERE `handoff_envelope_enabled` is true, THE SYSTEM SHALL attach an envelope (summary ≤ `handoff_summary_max_tokens`, default 1 500; full text as artifact) to Skynet `WorkerResult`, MoA proposers, Kokoro souls, BRP roles, `parallel_research` and spawner results, and reducers SHALL read `summary` and fetch artifacts only on demand.
- THE SYSTEM SHALL add envelope fields as optional (`envelope: HandoffEnvelope | None = None`) so pre-TKE shapes serialize and deserialize unchanged.

## SPEC-TKE-RAG-001: RAG context budget (`rag/context_budget.py`) — MUST (rerank/compression SHOULD)
```python
@runtime_checkable
class RerankerPort(Protocol):
    def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RetrievedChunk]: ...
def fit_to_budget(chunks: Sequence[RetrievedChunk], budget_tokens: int) -> list[RetrievedChunk]: ...
class CrossEncoderReranker: ...      # providers/rerank.py, extra [rerank], deferred import
class PassageCompressor: ...         # providers/compression.py, extra [compression] (LLMLingua-2)
```
- WHERE `rag_context_budget_enabled` is true, THE SYSTEM SHALL retrieve `rag_retrieve_k` (default 20), rerank (cross-encoder when `rag_rerank_enabled` and installed, else `_merge_and_rerank`), and pass the top chunks whose estimate fits `rag_context_budget_tokens` (default 3 000).
- WHERE `rag_passage_compression_enabled` is true, THE SYSTEM SHALL compress retrieved prose passages only and SHALL NOT compress code, JSON or any cached-prefix content.
- IF the optional extra is missing, THEN THE SYSTEM SHALL log once and fall back without raising.

## SPEC-TKE-CMD-001 (OPTIONAL, P3): Code-mode for MCP chains (`agents/codeact_mcp.py`) — MAY
```python
def render_mcp_module(tools: Sequence[BaseTool]) -> str: ...   # typed Python stubs, one function per tool
class SandboxToolBridge:
    async def call(self, tool: str, args: Mapping[str, Any], *, state: AgentState) -> str: ...
```
- WHERE `codeact_mcp_code_mode_enabled` is true, THE SYSTEM SHALL expose MCP tools to `codeact_agent` as an importable module inside `SandboxExecutor`, route every call through the bridge with `ActionInterceptor.check()` + `ToolPolicyEngine` per call, and return only the script's final stdout to the model.

## SPEC-TKE-CFG-001: Settings (`core/config.py`) — MUST

| Setting | Type | Default | Phase |
|---|---|---|---|
| `token_efficiency_enabled` | `bool` | `False` | master |
| `prompt_cache_enabled` / `prompt_cache_ttl` / `prompt_cache_scope` | `bool` / `"5m"\|"1h"` / `"tenant"\|"global"` | `False` / `"5m"` / `"tenant"` | TKE2 |
| `prompt_cache_policy_path` | `str` | `"config/prompt_cache.yaml"` | TKE2 |
| `prompt_canary_secret` | `SecretStr` | resolved via `ConfigSourcePort`/`CredentialVault` | TKE2 |
| `tool_gating_mode` | `"remove"\|"mask"` | `"remove"` | TKE3 |
| `tool_defer_enabled` / `tool_defer_threshold` / `tool_defer_core` | `bool` / `int` / `list[str]` | `False` / `30` / `[]` | TKE3 |
| `context_mask_keep_turns` / `context_mask_relevance_keep` | `int` / `float` | `10` / `0.8` | TKE4 |
| `context_compaction_high_water` / `_low_water` | `int` | `0` / `0` | TKE4 |
| `artifact_store_backend` / `artifact_store_path` / `artifact_ttl_s` | `"memory"\|"sqlite"` / `str` / `int` | `"memory"` / `"data/artifacts.db"` / `86400` | TKE4 |
| `tool_output_budget_enabled` / `tool_output_max_tokens_default` / `_by_tool` | `bool` / `int` / `dict[str,int]` | `False` / `2000` / `{}` | TKE5 |
| `tool_output_toon_enabled` | `bool` | `False` | TKE5 |
| `llm_effort_enabled`, `llm_effort_profile`, `llm_effort_by_agent`, `llm_max_tokens_by_agent` | see EFF-001 | `False`, `"none"`, `{}`, `{}` | TKE6 |
| `handoff_envelope_enabled` / `handoff_summary_max_tokens` | `bool` / `int` | `False` / `1500` | TKE7 |
| `rag_context_budget_enabled` / `rag_context_budget_tokens` / `rag_retrieve_k` | `bool` / `int` / `int` | `False` / `3000` / `20` | TKE8 |
| `rag_rerank_enabled` / `rag_passage_compression_enabled` | `bool` | `False` | TKE8 |
| `codeact_mcp_code_mode_enabled` | `bool` | `False` | TKE10 |

`context_compaction_strategy` (LH) additionally accepts `"mask_observations"`. Env prefix `PRISMAL_`. `_validate_token_efficiency` rejects unknown literals, negative budgets/thresholds, `low_water >= high_water` (when high > 0), `tool_defer_threshold < 1`. `prompt_canary_secret` is never mirrored to `os.environ` and never logged.

## SPEC-TKE-ERR-001: Exceptions (`core/exceptions.py`) — MUST
```python
class TokenEfficiencyError(PrismalError): ...
class TokenEfficiencyConfigError(TokenEfficiencyError): ...   # load-time only
class PromptCacheError(TokenEfficiencyError): ...             # internal; never escapes apply_cache_policy
class ArtifactStoreError(TokenEfficiencyError): ...
class ArtifactNotFound(ArtifactStoreError): ...
class MaskedToolCallError(TokenEfficiencyError): ...          # internal; surfaced as synthetic ToolMessage
```

## SPEC-TKE-OTEL-001: Metrics (`monitoring/otel.py`) — SHOULD

Counters: `prismal.llm_input_tokens_total{agent,model,kind}`, `prismal.llm_reasoning_tokens_total{agent,model}`, `prismal.prompt_cache_breakpoints_total{family}`, `prismal.prompt_cache_layout_fallbacks_total`, `prismal.tool_gate_masked_calls_total{agent,phase}`, `prismal.tool_search_calls_total{agent}`, `prismal.tool_deferred_activated_total{agent}`, `prismal.observations_masked_total`, `prismal.observations_mask_errors_total`, `prismal.artifacts_stored_total{kind}`, `prismal.artifact_reads_total`, `prismal.tool_output_truncated_total{tool}`, `prismal.toon_encoded_total`, `prismal.handoff_envelopes_total{pattern}`, `prismal.rag_passages_dropped_total`. Histograms: `prismal.llm_cache_hit_ratio{agent}`, `prismal.rag_context_tokens`.

## SPEC-TKE-COMPAT-001: Opt-in safety — MUST

- WHILE `token_efficiency_enabled` is false, THE SYSTEM SHALL produce a byte-for-byte identical compiled supervisor graph, `get_tools_for_agent()` output, `SecurePromptBuilder.build()` output, `ChatLiteLLM` construction and `Usage.prompt_tokens/completion_tokens/cost_usd` values versus v3.12.0.
- THE SYSTEM SHALL keep existing AST guards (provider imports, `mcp`/`skills` in `agents/**`, `os.environ` reads) passing unmodified.

Per-requirement test mapping: `TASKS.md` §8 (Traceability Matrix).
