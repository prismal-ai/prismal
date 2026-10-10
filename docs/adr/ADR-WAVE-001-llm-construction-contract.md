# ADR-WAVE-001 — LLM construction contract for wave 2026-10

| Field | Value |
|---|---|
| **Status** | `ACCEPTED` |
| **Date** | 2026-10-05 |
| **Author** | Ernesto Crespo |
| **Scope** | `providers/registry.py::ProviderRegistry.get_llm` / `get_llm_with_fallback`, `providers/cost.py::compute_cost_usd`, `providers/prompt_cache.py::CacheAwareChatModel`, `providers/gateways/chat_model.py::GatewayChatLiteLLM` |
| **Affects specs** | TKE (`SPEC-TKE-CCH-003`, `SPEC-TKE-EFF-001`, `SPEC-TKE-ACC-003`), GW (`SPEC-GW-REG-001`, `SPEC-GW-COST-001`), AMR (`SPEC-AMR-INT-001`, `SPEC-AMR-BEX-001`) |
| **Plan** | `specs/wave-2026-10-implementation-plan.md` §1.3 gaps G1, G2, G7 (task X0-03) |

## 1. Context

Four phases of wave 2026-10 change how an LLM is built, each written as a separate SDD:

| Phase | Change | Spec |
|---|---|---|
| TKE2 | `get_llm(agent=)`; wraps the model in `CacheAwareChatModel` (input-side cache policy) | `SPEC-TKE-CCH-003` |
| TKE6 | per-agent `reasoning_effort` + `max_tokens` on supporting models | `SPEC-TKE-EFF-001` |
| GW1 | `get_llm(agent=, session_id=)`; returns `GatewayChatLiteLLM(ChatLiteLLM)` (raw-response metadata capture, quota preflight); no double fallback | `SPEC-GW-REG-001` |
| AMR1 | `get_llm(features=, state=)`; policy selects the model string | `SPEC-AMR-INT-001` |

Read together, the specs leave three things undefined: the **final signature** of `get_llm` (each
spec shows only its own kwargs), the **stacking order** of the two `ChatLiteLLM` wrappers when both
flags are on, and one **precedence table** for `compute_cost_usd`, which TKE1, GW5 and AMR5 each extend.

As of v3.12.0, `get_llm(model=None, streaming=False, temperature=None)` builds a bare `ChatLiteLLM`
with global `max_tokens`, `request_timeout` and `max_retries`, and attaches the Langfuse callback.
`compute_cost_usd(model, prompt_tokens, completion_tokens, *, settings=None)` tries litellm, then
the pricing table, then zero.

## 2. Decision

### 2.1 Final `get_llm` signature (reached after AMR1)

```python
class ProviderRegistry:
    def __init__(self, settings: Settings | None = None, *,
                 gateway_router: GatewayRouter | None = None) -> None: ...          # GW1

    def get_llm(
        self,
        model: str | None = None,
        streaming: bool = False,
        temperature: float | None = None,
        *,
        agent: str | None = None,             # TKE2 (used by TKE2, TKE6, GW1 ToS guard, AMR1)
        session_id: str | None = None,        # GW1  (sticky routing / x-opencode-session)
        features: TaskFeatures | None = None, # AMR1
        state: AgentState | None = None,      # AMR1
    ) -> BaseChatModel: ...

    def get_llm_with_fallback(...same parameters...) -> BaseChatModel | RunnableWithFallbacks[...]: ...
```

- All new parameters are **keyword-only and default to `None`**. With every new kwarg at its default
  and every wave flag off, the `ChatLiteLLM` constructor arguments are identical to v3.12.0.
- The **first phase to merge** introduces a parameter. Later phases reuse it and do not redefine it.
  `agent` is introduced by TKE2. If GW1 merges first, GW1 introduces it with the same semantics.
- `agent` is the graph node name (e.g. `"coder"`). When it is `None`, `CacheAwareChatModel` may
  recover it at call time from `config["metadata"]["langgraph_node"]` (TKE2). Construction-time
  consumers have no `RunnableConfig`, so they see `None`. These are the GW ToS guard, TKE6 effort and
  AMR selection. **Gateway refs restricted by agent** (`gw:opencode-go/*`) therefore require an
  explicit `agent=`. Otherwise they are refused (`GatewayPolicyError`, as in `SPEC-GW-OC-001`).

### 2.2 Construction pipeline (single order, every phase plugs into one step)

```
1. model string   explicit `model`                          (explicit beats adaptive — AMR INT-001)
                  > AMR select_model(state, agent)           (iff adaptive_routing_enabled and features/state given)
                  > settings.default_model
2. gateway        GatewayRouter.resolve(model, agent, org)   (iff llm_gateways_enabled and ref is `gw:`; zero I/O)
3. inner kwargs   v3.12.0 base kwargs
                  + TKE6 effort: reasoning_effort, per-agent max_tokens (iff llm_effort_enabled and supported)
                  + GW: api_base, api_key, extra_headers, model_kwargs.extra_body (incl. hashed session_id)
                  + GW: max_retries=0 iff resolved.owns_model_fallback
4. inner model    GatewayChatLiteLLM(**kw) if step 2 resolved, else ChatLiteLLM(**kw)
5. callbacks      Langfuse handler (unchanged)
6. outer wrapper  CacheAwareChatModel(inner=<step 4>, policy, agent)   (iff prompt_cache_enabled)
7. fallback       get_llm_with_fallback only: primary.with_fallbacks([fallback]) on the step-6 objects,
                  skipped when the primary gateway owns model fallback (GW no-double-fallback)
```

Rules:

- **Inheritance vs composition.** `GatewayChatLiteLLM` *subclasses* `ChatLiteLLM`, so it is the
  innermost concrete model and owns the raw-response seam (served model, billed cost,
  `response_metadata["prismal_gateway"]`, quota preflight). `CacheAwareChatModel` *wraps*
  (composition) whatever step 4 produced. It acts on the input side only: breakpoints,
  `prompt_cache_key` and name-sorted `bind_tools`. It returns the inner `ChatResult` unchanged, so
  gateway metadata reaches `CostMeter` untouched.
- **AMR never wraps.** It only chooses the model string in step 1 (and, under cascade, drops the
  gateway `models[]` list in step 3 per `SPEC-AMR-GW-001`).
- **Key ownership inside `extra_body` / request kwargs:** GW owns `provider`, `models`, `plugins`
  and `session_id`. TKE2 owns `prompt_cache_key`, retention and `cache_control` blocks (added at
  request time, never at construction). TKE6 owns `reasoning_effort`. A key set by two owners is a
  bug, and the registry contract test asserts that the sets are disjoint.
- **Type checks.** Wave code must not depend on `isinstance(llm, ChatLiteLLM)`. The wrapper must
  delegate `bind_tools`, `with_structured_output`, `with_config`, streaming (`_stream`/`_astream`)
  and `callbacks` to `inner`.

### 2.3 Final `compute_cost_usd` signature and precedence

```python
def compute_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int, *,
    settings: Settings | None = None,
    cache_read_tokens: int = 0, cache_write_tokens: int = 0,      # TKE1
    reported_cost_usd: float | None = None,                       # TKE1 (filled by GW)
    response: object | None = None,                               # TKE1
    cost_mode: Literal["metered", "subscription"] = "metered",    # GW5
    batch: bool = False,                                          # AMR5
) -> CostEstimate: ...   # CostEstimate(cost_usd, source, equivalent_usd=0.0)
```

| Step | Condition | Result | `source` |
|---|---|---|---|
| 1 | `cost_mode == "subscription"` | `cost_usd=0.0`; `equivalent_usd` = `reported_cost_usd` if given, else the price from steps 3–4 | `"subscription"` |
| 2 | `reported_cost_usd is not None` | that value unchanged; `batch` ignored (already the billed amount) | `"gateway"` |
| 3 | litellm prices it (`completion_cost(response)` if a raw response is available, else `cost_per_token` with cache kwargs) | price × batch ratio if `batch` | `"litellm"` |
| 4 | `settings.budget_pricing[model]` exists (`cache_read`/`cache_write` fall back to `input`) | price × batch ratio if `batch` | `"table"` |
| 5 | otherwise | `0.0` | `"none"` |

- The batch ratio comes from configuration (`batch_price_ratio`, verified in AMR0-03) and is never
  hard-coded. `source` stays the same with `batch=True`.
- Any exception in a step falls through to the next step. The function never raises.
- With every new kwarg at its default, the result equals v3.12.0 (TKE1 contract test, re-run by GW5
  and AMR5).
- TKE1 owns this table. GW5 and AMR5 add rows by amending this ADR, not their own spec only.

## 3. Consequences

- One registry contract test (`tests/unit/providers/test_registry_contract.py`) is created by the
  first wave phase that touches `get_llm`. Each later phase **extends** it with its own
  "flag off ⇒ identical kwargs" case and a "both wrappers on" case. Nothing replaces it.
- Required combination tests: TKE2+GW1 (metadata survives the wrapper, `bind_tools` sorted,
  disjoint body keys), GW1+AMR1 (no `models[]` under cascade, explicit `model=` wins), and
  TKE6+GW1 (effort passed through the gateway kwargs only when supported).
- The spec texts keep their per-phase snippets, with a pointer to this ADR as the source of truth
  for the merged signature.

## 4. Alternatives considered

- **`CacheAwareChatModel` as a `ChatLiteLLM` subclass too.** Rejected because of diamond-style
  multiple inheritance with `GatewayChatLiteLLM`, and because cache policy is an input concern
  that should not care which concrete model sits below it.
- **Gateway as an outer wrapper.** Rejected: billed cost and served model are only visible at the
  raw-response seam inside `ChatLiteLLM`, and the subclass is what the GW spec already verifies (GW0-03).
- **A `get_llm(options: LlmOptions)` dataclass.** Rejected for this wave because it breaks the
  v3.12.0 signature that existing callers use. It could be revisited for 4.0.0.

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-05 | Ernesto Crespo | Initial decision (M0 / X0-03) |
