# Adaptive model routing (Phase AMR)

Before 3.16.0, every agent turn paid for whatever model the operator wired (`default_model` plus a
few per-feature settings), however easy the request and however much budget was left. Phase AMR
(3.16.0) puts a **routing policy** behind one port so the model follows the task:

> Prismal picks **which model** (tier, capability, budget, accuracy floor); gateways pick **which
> provider** serves it (DD-AMR-006).

Everything is opt-in. With `adaptive_routing_enabled=False` (the default) `get_llm()` builds exactly
the 3.15.0 `ChatLiteLLM` arguments, the compiled supervisor graph is unchanged (snapshot test) and no
routing code runs. Enabled, routing starts in **shadow**: policies compute choices and
counterfactual savings, but the 3.15.0 model is still used.

## Concepts

| Piece | Module | What it does |
|---|---|---|
| Tiers | `routing/types.py` | `nano < small < medium < frontier` (`TIER_ORDER`); `auto` (a gateway auto router) is terminal |
| Tier map | `config/routing_tiers.yaml` | tier → model ref, agent → tier, risk floor |
| `StaticTierPolicy` | `routing/policies/static.py` | agent → tier → model; the fail-open fallback of every policy |
| `BudgetAwarePolicy` | `routing/policies/budget_aware.py` | one-tier downgrade while over the **soft** budget cap |
| `CascadePolicy` | `routing/policies/cascade.py` | cheapest allowed tier first, escalate on a bad outcome |
| `CompositePolicy` | `routing/policies/composite.py` | `BudgetAware(Cascade(Static))` for `routing_policy=composite` |
| `BatchAssignmentPolicy` | `routing/policies/batch_assignment.py` | OmniRouter-style joint assignment for fan-outs |
| `TableSuccessPredictor` | `routing/predictor.py` | Beta-Binomial P(success) per (intent, tier) |
| `PlanCache` / `ResponseCache` | `routing/plan_cache.py`, `routing/response_cache.py` | intent-keyed plan reuse; exact response cache |
| `BatchExecutor` | `routing/batch.py` + `providers/batch_adapters.py` | provider batch APIs at batch price |

### The tier map

The shipped `config/routing_tiers.yaml` is a **parity map**: every tier is `null`, which resolves to
`settings.default_model`, so enabling routing with the static policy reproduces 3.15.0 for all 26+
agents (parity test). Fill in the tiers to let the policies trade cost for capability:

```yaml
tiers:
  small: anthropic/claude-haiku-4-5
  medium: anthropic/claude-sonnet-4-5
  frontier: gw:openrouter/anthropic/claude-opus-4.5
agents:
  file_manager: small
  coder: frontier
risk_floor:
  high: medium
  critical: frontier
```

Unknown agents take their tier from their legacy model setting (`skynet_worker_model`,
`kokoro_judge_model`, …) when it matches a tier model, else `medium`. The YAML `risk_floor` and
`PRISMAL_ROUTING_RISK_FLOOR` are combined and the **stricter** tier wins; an invalid tier map
refuses to start when routing is enabled (a missing file falls back to `default_model`).

### Where routing happens (ADR-WAVE-001 step 1)

```
explicit model=  >  policy choice (adaptive_routing_enabled, agent + state/features)  >  default_model
```

AMR only picks the model **string**; it never wraps the model. Routing is opted into with
`features=` (never `agent=`, so shadow mode activates no gateway agent map or per-agent effort that
3.15.0 did not); an agent pinned in `llm_gateway_agent_map` keeps its gateway route. The
supervisor-routed agents opt in with two gated one-liners (byte-identical calls when routing is off):

```python
llm = registry.get_llm_with_fallback(**llm_route_kwargs("coder", state))
response = await track_outcome(state, "coder", react_loop(...))
```

`track_outcome` turns the call into a `RoutingOutcome` — tool errors counted inside `react_loop`,
token usage priced at the chosen model (for `routing_savings_usd`), the DEC adequacy check when
`decisions_enabled` — so the cascade can escalate. Only parse/validation errors
(`ValidationError`, `JSONDecodeError`, `OutputParserException`) count as structured failures;
timeouts, rate limits and policy errors never escalate. CodeAct reports via
`record_agent_outcome`. Calls without a `session_id` are routed statelessly (no shared bucket).

### Selection order

1. **Affinity** (DD-AMR-003): within a turn an agent keeps its model; after an escalation the
   agent stays at the escalated tier for later turns of the thread (no re-descent), so the provider
   prompt-cache prefix survives. Persisted as `metadata["routing"]["affinity"]` with the escalation
   count; a persisted affinity is accepted only if its model matches the tier map. State hints in
   `metadata["routing"]` may only *raise* risk or *set* taint.
2. **Policy** — fail-open: any exception returns the static choice with `reason="fallback:<err>"`.
3. **Security floors** — fail-closed: never below `routing_risk_floor[risk]` (high → medium,
   critical → frontier), never `auto` for critical, and a tainted turn never below the agent's
   static tier. An enabled config without a `critical` floor refuses to start.
   Floors run on every hop, including same-turn sticky choices (taint can arrive mid-turn), and a
   tainted turn never runs on `auto` unless `auto` is the agent's static tier.
4. **Shadow** returns the 3.15.0 model and records the choice under `metadata["routing"]["shadow"]`
   (no savings are counted for models that never ran).

### Cascade

`select` starts at `routing_cascade_start_tier` (default `small`), capped by the agent's static tier,
raised to the risk floor and to the first tier with the needed capabilities
(`routing_tier_capabilities`: `tools`, `structured_output`). `on_outcome` escalates one tier when:

| Trigger | Cause |
|---|---|
| `structured_valid is False` | `structured_invalid` |
| adequacy P(true) < `routing_adequacy_tau` (0.7) | `inadequate` |
| `tool_errors ≥ routing_cascade_tool_error_threshold` (2) | `tool_errors` |
| adequacy check **abstained** and risk is high/critical | `inadequate` |

At most `routing_cascade_max_escalations` (2) escalations; `frontier` and `auto` are terminal
(`prismal.routing_escalations_total{reason="cap"}`).

**Adequacy** is a DEC Noul decision (`AdequacyChecker`). Request and answer enter the decision state
only through `DecisionStateBuilder` (sanitized, fenced, authority-claim flagged); a tainted state
abstains without calling the backend.

### Gateways (no double routing)

Under a cascade, a `gw:` call is sent **without** the gateway `models[]` fallback list and without
`settings.fallback_model` — the cascade is the fallback. `auto` tier refs are terminal and never
batch candidates; gateway data-policy denials are eligibility masks applied before solving.

### Batch assignment (Skynet, MoA, eval)

`BatchAssignmentPolicy.assign(items, alpha=, concurrency=)` minimizes predicted cost subject to a
mean predicted accuracy ≥ α (`routing_batch_alpha`, 0.8) and per-model concurrency caps
(`routing_model_concurrency`, else `parallel_max_workers`; waves when Σ caps < N). It runs a
Lagrangian dual decomposition with primal recovery, then a greedy fallback, and finally returns the
max-accuracy assignment with `feasible=False` — it never raises and is deterministic.

Fan-out items are tainted when the run's taint registry marks their text; tainted items are never
assigned below the agent's static tier, the merged risk floor applies, and gateway policy denials
(`GatewayRouter.resolve`) make a candidate ineligible before solving.

* **Skynet** — `SkynetSupervisor(batch_policy=...)` (built from settings by the subgraph builder when
  `routing_policy=batch`) assigns each round's orders jointly;
  `SwarmWorker` uses `order.context["routing_model"]` when routing is enforced.
* **MoA** — `MixtureOfAgents(batch_policy=...)` (wired by `make_mixture_node`) assigns the proposer
  slots; the aggregator keeps its static model. Proposers share one feature set, so the solver may
  pick the same model for every slot (it optimises cost, not diversity).
* **Eval** — `python -m prismal.eval run --suite S.yaml --routing cascade --json routed.json`, then
  `python -m prismal.eval routing-gate --static static.json --routed routed.json` (target −40 %
  cost with ≤ 1 pt quality loss). `export_predictor_table` writes
  `config/routing_predictor/<version>.json` from eval results.

### Plan cache and response cache

`PlanCache` keys plans by `(intent, canonical target, org_id, tool_scope_hash, prompt_version,
user_scope)` and
serves only allow-listed read-only intents (`plan_cache_intents`) with no write/exec tools in scope
and untainted input. A hit is adapted by a small-tier model through `SecurePromptBuilder` and
re-validated as a DAG before use; any failure is a miss. A new tool catalog or prompt version is a
structural miss; tenants (and, with `user_scope`, users) never share entries. Plans are stored only
after a run with zero failed tasks, and a cached plan that fails is evicted. The LLM-Compiler node
uses it through `plan_cache_for(state, ...)` on the first planning attempt only. `ResponseCache` (exact key) serves only tool-free, temperature-0, read-only,
untainted turns.

### BatchExecutor

`BatchExecutor.run(requests)` groups requests by provider family (Anthropic Message Batches, OpenAI
Batch, OpenRouter `:batch`), polls with capped exponential backoff and jitter, meters results at
`batch_price_ratio` × on-demand price and falls back to synchronous calls on a missing adapter,
failure or deadline. Tainted, HITL-gated and data-policy-rejected requests are never uploaded. A
completed job whose results cannot be fetched is reported per request (`results_unavailable`), never
re-run (no double billing); sync fallback is bounded by `parallel_max_workers`. OpenRouter batch
bodies carry the GW privacy defaults (`data_collection: deny`, `zdr`), credentials are sent only to
the adapter's own origin (never to a foreign `results_url`), and `default_adapters(key_resolver=)`
lets a host inject tenant keys.

## Configuration

```bash
PRISMAL_ADAPTIVE_ROUTING_ENABLED=true
PRISMAL_ROUTING_MODE=shadow                 # shadow | enforce
PRISMAL_ROUTING_POLICY=composite            # static | cascade | budget_aware | composite | batch
PRISMAL_ROUTING_TIER_MODELS=config/routing_tiers.yaml
PRISMAL_ROUTING_RISK_FLOOR='{"high":"medium","critical":"frontier"}'
PRISMAL_ROUTING_CASCADE_START_TIER=small
PRISMAL_ROUTING_CASCADE_MAX_ESCALATIONS=2   # <= 3
PRISMAL_ROUTING_ADEQUACY_TAU=0.7
PRISMAL_ROUTING_BATCH_ALPHA=0.8             # (0, 1]
PRISMAL_ROUTING_PREDICTOR_PATH=config/routing_predictor/v1.json   # required for policy=batch
PRISMAL_PLAN_CACHE_ENABLED=true
PRISMAL_PLAN_CACHE_INTENTS='["researcher"]'
PRISMAL_BATCH_API_ENABLED=true
PRISMAL_BATCH_PRICE_RATIO=0.5
```

## Observability

Counters `prismal.routing_decisions_total{policy,tier}`, `prismal.routing_escalations_total{reason}`,
`prismal.plan_cache_hits_total{intent}`, `prismal.plan_cache_misses_total{reason}`,
`prismal.batch_jobs_total{status}` and `prismal.routing_savings_usd` (micro-USD: frontier
counterfactual − actual, priced with `compute_cost_usd`); span `prismal.routing.select`
(`policy`, `tier`, `model_ref`, `reason`).

## Rollout

1. **Shadow** — enable, keep `routing_mode=shadow`, compare choices and savings on the eval set.
2. **Budget-aware** — downgrade only on soft caps in staging; watch quality and the cache-hit ratio.
3. **Enforce** — cascade for low/medium-risk agents; batch for Skynet/MoA once the feasibility rate
   is ≥ 95 %; plan cache per intent allowlist.

Example: [`examples/adaptive_routing.py`](../examples/adaptive_routing.py).

## Verified facts

AMR0 "verify before build", checked **2026-10-06**. Volatile values live in settings, never in code.

| Task | Fact | Source | Outcome |
|---|---|---|---|
| AMR0-01 | OmniRouter: Mei, Xu, Guo, Lin, Zhang (Rutgers), arXiv 2502.20576, latest **v6 (2025-11-28)** | arxiv.org/abs/2502.20576 | confirmed |
| AMR0-01 | Up to +6.30 % accuracy, ≥ 10.15 % lower cost vs S3, PO, EmbedLLM, RouterDC, Hybrid-LLM, CARROT; 2,700 questions (MMLU, GPQA-Diamond, MATH-500, GSM8K) over 10 LLMs | arxiv.org/html/2502.20576v6 | confirmed |
| AMR0-01 | Predictor: BERT dual head (capability sigmoid + output-length bucket), **top-16** retrieval fusion; 81.3 % capability accuracy; Lagrangian dual decomposition with adaptive multipliers | arxiv.org/html/2502.20576v6 | confirmed (k = 16) |
| AMR0-01 | "KDD Explorations" venue | arXiv metadata | not verifiable (no journal-ref; cite as preprint) |
| AMR0-02 | RouteLLM (arXiv 2406.18665): cost cut "by over 2 times in certain cases" without quality loss | arxiv.org/abs/2406.18665 | confirmed |
| AMR0-02 | FrugalGPT (arXiv 2305.05176): match GPT-4 with up to 98 % cost reduction | arxiv.org/abs/2305.05176 | confirmed |
| AMR0-02 | Agentic Plan Caching = **arXiv 2506.14852** (Stanford, NeurIPS 2025): −50.31 % cost, −27.28 % latency, 96.61 % of optimal accuracy | arxiv.org/abs/2506.14852 | confirmed (ID found) |
| AMR0-02 | arXiv 2602.18922 ("Why Agent Caching Fails…"): **APC 0–12 % cache accuracy**, GPTCache 37.9 % | arxiv.org/abs/2602.18922 | **changed** — accuracy, not "semantic cache hit rate" |
| AMR0-03 | Anthropic Message Batches: 50 % discount, async, inline requests, ≤ 100,000 requests / 256 MB, 24 h expiry, JSONL `results_url` kept 29 days | platform.claude.com batch-processing docs | confirmed |
| AMR0-03 | OpenAI Batch: 50 %, `completion_window="24h"`, JSONL via Files API (`purpose="batch"`), ≤ 50,000 requests / 200 MB | developers.openai.com batch guide | confirmed |
| AMR0-03 | OpenRouter `:batch`: a **true async** batch API (`POST /api/v1/batches` → 202 `validating`, poll `GET`, `DELETE` cancels), inline JSON, `24h` only, one provider per batch, "typically 50 %"; a model without a `:batch` endpoint returns 400 | openrouter.ai/docs/batch-quickstart | confirmed (async — adapter is a real batch adapter, not a sync passthrough) |
| AMR0-04 | LiteLLM 1.86.2 exposes `acreate_batch` / `aretrieve_batch` / `acancel_batch` / `acreate_file` / `afile_content`; the full **OpenAI** path works | local `litellm/batches`, `litellm/files` | confirmed → OpenAI adapter uses LiteLLM |
| AMR0-04 | LiteLLM 1.86.2 **cannot create Anthropic batches** (`NotImplementedError`) nor fetch their `results_url` | `litellm/llms/anthropic/batches/transformation.py` | **changed** → Anthropic and OpenRouter adapters use raw `httpx` inside `providers/` |
| AMR0-04 | Default `batch_price_ratio = 0.5` (all three providers state 50 %) | the three docs above | confirmed |
