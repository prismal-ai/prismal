# Prismal Adaptive Model Routing — Technical Design Document

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
| **TASKS** | `specs/adaptive-model-routing/TASKS.md` |

---

## 1. Context

Verified against the code staged for 2026-10-04:

- `providers/registry.py::ProviderRegistry.get_llm(model, streaming, temperature)` builds `ChatLiteLLM(model=resolved_model, …)` where `resolved_model = model or settings.default_model`; the only per-model branch is the Ollama timeout override. `get_llm_with_fallback()` adds one `settings.fallback_model` through `with_fallbacks`.
- `budget/types.py::BudgetStatus(within, soft_exceeded, hard_exceeded, breached_dimension, usage, budget)` is already the verdict shape AMR needs; `budget/meter.py::CostMeter.record_response(message, model, …)` prices calls through `providers/cost.py::compute_cost_usd` (sources `litellm|table|none`, plus `gateway`/`subscription` after TKE1/GW).
- `agents/intent_router.py::match_intent(text) -> str | None` is a pure regex function returning a member name (e.g. `cron_manager`, `code_review`, `skynet`) — a stable, deterministic intent label usable as a predictor and plan-cache key component.
- Feature-specific models are plain settings strings (`skynet_worker_model`, `kokoro_judge_model`, `blind_review_*_model`), consumed directly by those features.

Precedents: the per-run registry pattern (`budget/resolve.py`, `security/hardening_run.py`, `agents/context_compaction.py`) and the factory-injection style of `agents/patterns/` (callables injected, LLM defaults wired lazily).

## 2. Feasibility with the existing core

- **Model choice seam exists.** Every agent obtains its model through `ProviderRegistry.get_llm()`; adding keyword-only `features=`/`state=` parameters is call-compatible, and explicit `model=` keeps precedence.
- **Budget signal exists.** `get_budget_guard(state).check()` gives `BudgetStatus` in O(1); no new metering path.
- **Outcome signals exist or arrive earlier in the roadmap.** Structured-output validation errors are raised by LangChain `with_structured_output`; tool errors are visible in `react_loop`; adequacy comes from DEC `DecisionPort` (3.15.0); usage/cache fields from TKE1 (3.13.0); served model from GW (3.14.0).
- **Batch fan-out seams exist.** `SkynetSupervisor.plan()` yields N orders before `Send`; `MixtureOfAgents.generate` builds its proposer list up front; the eval harness iterates a fixed case list.
- **No new heavy dependency.** The solver is pure Python (lists, `heapq`); batch adapters reuse `httpx`/LiteLLM already pinned. LiteLLM batch support per provider is **[verify]** (AMR0).

## 3. Proposed Architecture

### 3.1 Modules

| Module | Responsibility |
|---|---|
| `routing/types.py`, `routing/ports.py` | Value objects; `RoutingPolicyPort`, `SuccessPredictorPort` |
| `routing/policies/{static,budget_aware,cascade,batch_assignment,composite}.py` | Policies (pure, sync, fail-open) |
| `routing/predictor.py` | Beta-Binomial table predictor + exporter from eval reports |
| `routing/features.py`, `routing/select.py` | Deterministic feature extraction; per-run seeding; `select_model` / `record_outcome` |
| `routing/plan_cache.py`, `routing/response_cache.py` | Intent-keyed plan cache; exact response cache |
| `routing/batch.py`, `providers/batch_adapters.py` | `BatchExecutor`; provider batch API adapters (SDK/HTTP isolated in `providers/`) |
| `config/routing_tiers.yaml`, `config/routing_predictor/<ver>.json` | Tier map, agent tiers, risk floor; predictor table |

State: `state["metadata"]["routing"] = {"enabled": True, "policy": …, "intent": …, "affinity": {agent: {tier, model_ref}}, "escalations": {agent: n}}`. Policy instances and caches live in the in-process registry keyed by `session_id`.

### 3.2 Per-turn data flow (`adaptive_routing_enabled=True`)

```
supervisor_node ── maybe_seed_budget_run / hardening / compaction / maybe_seed_routing_run
      │ routes to agent X (regex → DEC-ROUTE → LLM supervisor)
      ▼
agent X node
      │ features = extract_features(state, "X")      (deterministic, no LLM)
      │ budget   = get_budget_guard(state)?.check()
      ▼
policy.select(features, budget=budget)  ──►  ModelChoice(model_ref, tier, reason)
      │   Composite: BudgetAware( Cascade( Static ) )
      │   affinity hit? → reuse escalated tier/model_ref
      │   risk floor  → clamp up (fail-closed)
      ▼
ProviderRegistry.get_llm(features=…) ── gw: ref? ── GatewayRouter.resolve() ── ChatLiteLLM
      │                                     (cascade active ⇒ no gateway `models`, no fallback_model)
      ▼
LLM call ─► RoutingOutcome(structured_valid, tool_errors, usage, served_model)
      │ adequacy = AdequacyChecker.check()  (DecisionPort Noul; None on abstain/timeout)
      ▼
policy.on_outcome(choice, outcome) ── None ──► accept, predictor.update(), OTel savings
      │ ModelChoice (escalated, ≤ 2)
      ▼
retry with escalated model; affinity[(session, X)] := escalated tier
```

### 3.3 Batch fan-out flow (Skynet / MoA / eval)

```
SkynetSupervisor.plan(goal) ─► orders[1..N]
      │ items = [BatchItem(order.id, extract_features(...))]
      ▼
BatchAssignmentPolicy.assign(items, alpha=α, concurrency=C)
      │ build matrices a[i][j] = predictor.p_success, c[i][j] = price(in_i, L̂_ij)
      │ mask ineligible (risk floor, capabilities, gateway data policy, AUTO)
      ▼
solve_dual ── feasible? ──yes──► BatchAssignment(solver="dual")
      │ no
      ▼
solve_greedy ── reaches α? ──yes──► solver="greedy"
      │ no
      ▼
max-accuracy capacity-feasible assignment, feasible=False (counted, never raised)
      ▼
Send(worker, {order, model_choice}) × N   (waves if Σ C_j < N)
```

### 3.4 Batch assignment mathematics (OmniRouter adaptation)

**Notation.** Items `i ∈ {1..N}`, candidate models `j ∈ {1..K}`. `a_ij ∈ [0,1]` predicted success (SPEC-AMR-PRD-001). `c_ij = price_j(t_i^in, L̂_ij)` predicted USD cost, with `t_i^in` the estimated input tokens and `L̂_ij` the expected output tokens; for the solver costs are normalized `ĉ_ij = c_ij / max c`. `C_j` per-model concurrency cap, `W = max(1, ⌈N / Σ_j C_j⌉)` waves. `E_i ⊆ {1..K}` eligible models for item `i`.

**Primal (integer program).**

```
minimize    Σ_i Σ_{j∈E_i} ĉ_ij · x_ij
subject to  (1/N) Σ_i Σ_{j∈E_i} a_ij · x_ij ≥ α            (accuracy floor)
            Σ_i x_ij ≤ W · C_j                    ∀ j      (concurrency cap)
            Σ_{j∈E_i} x_ij = 1                    ∀ i      (one model per item)
            x_ij ∈ {0,1}
```

**Lagrangian.** Relax the coupling constraints with `λ ≥ 0` (accuracy) and `μ_j ≥ 0` (capacity):

```
L(x, λ, μ) = Σ_i Σ_j ĉ_ij x_ij − λ (Σ_i Σ_j a_ij x_ij − N α) + Σ_j μ_j (Σ_i x_ij − W C_j)
```

For fixed `(λ, μ)` the problem **decomposes per item** (only the one-model constraint remains):

```
j*(i) = argmin_{j ∈ E_i} ( ĉ_ij − λ · a_ij + μ_j )        ties → (c_ij, model_ref, item_id)
```

**Dual update (projected subgradient, adaptive step).** At iteration `t` with `A_t = Σ_i a_{i j*(i)}` and `n_j^t = |{i : j*(i) = j}|`:

```
λ_{t+1}   = max(0, λ_t   + η_t · (N α − A_t) / N)
μ_{j,t+1} = max(0, μ_{j,t} + η_t · (n_j^t − W C_j) / N)
η_t       = η_0 / sqrt(t + 1)     (η_0 = 1.0; halve η_0 when the dual value oscillates for 5 iterations)
```

The dual value `D(λ, μ) = L(x*(λ, μ), λ, μ)` is a lower bound on the primal optimum. At every iteration the current `x*` is checked for primal feasibility; the cheapest feasible assignment seen so far is kept (primal recovery). Stop when `(best_feasible_cost − max D) / max(best_feasible_cost, 1e-9) ≤ ε` (`ε = 0.01`) or `t = routing_batch_max_iter` (200). Initialization `λ_0 = 0`, `μ_0 = 0` (pure cheapest), which is optimal whenever the cheapest assignment already satisfies both constraints.

**Greedy fallback** (when dual recovery finds no feasible `x`):

1. Start from the cheapest capacity-feasible assignment: process items by ascending regret (`second-cheapest − cheapest`), assign the cheapest eligible model with residual capacity.
2. While `mean accuracy < α`: over all items and all eligible `j'` with residual capacity, pick the upgrade maximizing `Δa / Δĉ` (`Δa > 0`; `Δĉ ≤ 0` ⇒ take immediately), via a max-heap keyed `(Δa/Δĉ, −item_id)`; apply, update capacities, re-push affected entries lazily.
3. If no improving upgrade exists and `mean < α`, return the max-accuracy capacity-feasible assignment (per item `argmax a_ij` subject to residual capacity, items ordered by largest accuracy spread) with `feasible=False`.

**Complexity.** Matrix build `O(N·K)` predictor calls (O(1) table lookups). Dual: `O(T·N·K)` time, `O(N·K)` memory — N=200, K=8, T=200 ⇒ 3.2·10⁵ inner operations (< 50 ms CPython target). Greedy: `O(N·K·log(N·K))` heap operations plus at most `N·(K−1)` upgrades. Deterministic: no randomness, fixed tie-breaks.

**Departures from the paper (documented).** OmniRouter uses a BERT dual-head predictor (capability via sigmoid, output-length bucket via softmax) blended with top-k retrieval of similar past queries, evaluated on 2,700 questions (MMLU, GPQA-Diamond, MATH-500, GSM8K) over 10 models with 81.3 % predictor capability accuracy **[verify]**. Prismal ships a Beta-Binomial table keyed by `(intent, tier)` with optional DecisionPort Score blending and a median output-length table from TKE1 usage — no training dependency, explainable, and replaceable through `SuccessPredictorPort`. The optimization structure (objective, constraints, dual decomposition with adaptive multipliers) follows the paper.

### 3.5 Plan cache flow

```
planner node ── features.intent ∈ plan_cache_intents? ── tools read-only? ── untainted?
      │ all yes                                                        │ any no → planner LLM (no cache)
      ▼
key = (intent, canonical_target, org_id, tool_scope_hash, prompt_version)
      │ hit (TTL ok) ── adapt slots with small tier via SecurePromptBuilder ── ok ──► skip planner
      │ miss / adapt failure ──► planner LLM ──► validate plan (Kahn DAG check) ──► store(key, plan)
```

## 4. Design Decisions

### DD-AMR-001: Policies are composable decorators over one port
`BudgetAware(Cascade(Static))` mirrors the middleware style of `@prismal_node`; each concern is testable alone and the default composite is a single config value.

### DD-AMR-002: Static parity first
`StaticTierPolicy` with a parity tier map reproduces 3.15.0 exactly; operators adopt cascade/budget behavior incrementally without changing agents.

### DD-AMR-003: One-way escalation + cache affinity
Model switching fragments provider prompt caches (TKE2's main win). Escalation never descends within a thread; affinity reuses the escalated model so the cached prefix survives. Downgrades happen only at turn boundaries and only from budget pressure.

### DD-AMR-004: Adequacy via typed decision, abstention is risk-aware
A Noul adequacy check costs a fraction of an LLM judge (DEC). Abstain/timeout accepts for low/medium risk and escalates for high/critical — the safe direction (more capable model) is always chosen when uncertain at high stakes.

### DD-AMR-005: Table predictor, not a trained model
Eval-harness data already exists per intent; a Beta-Binomial table is deterministic, auditable and cold-start safe (prior). A learned predictor can plug in later via `SuccessPredictorPort`.

### DD-AMR-006: No double routing with gateways
Prismal owns **which model** (semantic choice: tier, capability, budget, accuracy floor); gateways own **which provider serves that model** (OpenRouter `provider.sort`, `only/ignore`, ZDR). Consequences: (a) under `CascadePolicy`, gateway model-level fallbacks (`models: [...]`) and `settings.fallback_model` are disabled for the call — the cascade *is* the fallback; (b) gateway-native auto routers (`openrouter/auto`) are allowed only as tier `AUTO`, which is terminal (no escalation from/to it) and excluded from batch assignment because its cost/accuracy are not predictable; (c) the served model reported by the gateway is the unit of predictor feedback; (d) gateway data-policy denials are applied as eligibility masks *before* selection. This prevents compounding retries, unaccountable cost, and contradictory routing loops.

### DD-AMR-007: Intent-keyed plan cache, semantic cache not default
Agentic workloads show 0–12 % hit rates for embedding semantic caches **[verify]** while APC reports −50.31 % cost / −27.28 % latency with keyed plan templates **[verify]**. The fix is canonical intent keys plus a tiered cascade (exact key → adaptation by a small model → planner). Embedding backends remain pluggable.

### DD-AMR-008: Batch API behind a provider-isolated port
Batch APIs are provider-specific (JSONL uploads, custom ids, 24 h windows **[verify]**); adapters live in `providers/` (Rule #4). The executor always has a synchronous fallback so offline jobs never stall.

### DD-AMR-009: Security floors are fail-closed
Cost logic is fail-open (any error ⇒ static choice). Risk floors are not: an invalid floor configuration refuses startup, and tainted turns never downgrade below the static tier.

## 5. Security & cost

- Feature extraction is deterministic; untrusted text never chooses a model directly. Adequacy questions embed request/answer through `SecurePromptBuilder`; DEC's forbidden list applies (no decision over tainted input without corroboration — tainted ⇒ adequacy treated as abstain).
- Plan cache: tenant-scoped keys, read-only intents, no write/exec tools, untainted inputs only; cached plans are re-validated (DAG check) before use.
- Batch executor never batches tainted or HITL-gated requests; batch payloads are written to provider storage — gateway/provider data policies are evaluated first.
- Savings counter compares against a frontier counterfactual priced with the same `compute_cost_usd` precedence (gateway > litellm > table).

## 6. Observability

- Counters/histograms per SPEC-AMR-OTEL-001; span `prismal.routing.select`; attributes on existing `provider.get_llm` span: `prismal.routing.tier`, `prismal.routing.reason`.
- Eval report columns: cost, quality, savings, escalation rate per intent, batch feasibility rate, plan-cache hit ratio.

## 7. Relationship to existing specs

- **TKE (3.13.0):** consumes `Usage` cache/reasoning fields and effort settings (TKE6 effort stays constant per agent per run — affinity complements it).
- **GW (3.14.0):** `gw:` refs as tier targets; no-double-fallback rule extended to cascades (DD-AMR-006).
- **DEC (3.15.0):** `DecisionPort` for adequacy (Noul) and optional predictor Score; DEC-ROUTE supplies intent labels.
- **C (budget):** `BudgetStatus` input; hard caps remain `BudgetGuard`'s job.
- **MEM (3.18.0):** consumes `BatchExecutor` for sleep-time consolidation.

## 8. Testing strategy

- Unit: tier ordering; each policy; escalation triggers and caps; affinity; predictor posterior math; dual solver on hand-solved fixtures (N≤6, verified by brute force over all Kᴺ assignments); greedy fallback; infeasible path; determinism (same input twice → equal); plan-cache eligibility/invalidation/tenant isolation; batch backoff schedule with a fake clock.
- Property-based (hypothesis): for random small instances, `solve_dual` feasible result cost ≤ greedy cost + ε and both satisfy caps.
- Integration (fakes only): Skynet round assigned via batch policy; cascade escalation on structured-output failure; gateway call without `models` list under cascade.
- Snapshot: all flags off ⇒ graph + `ChatLiteLLM` argument recording identical.
- Benchmark: `assign()` N=200, K=8 p95 ≤ 50 ms.

## 9. Rollout

1. **Shadow:** `adaptive_routing_enabled=True`, `routing_mode="shadow"` (default) — policies compute choices and counterfactual savings, the static model is still used; compare on the eval set.
2. **Warn:** apply `budget_aware` only (downgrades on soft cap) in staging; monitor quality and cache-hit ratio.
3. **Enforce:** enable `cascade` for low/medium-risk agents; enable `batch` for Skynet/MoA once feasibility rate ≥ 95 %; plan cache per intent allowlist.

## 10. Open questions

- `routing_mode` (SPEC-AMR-CFG-001) is a separate `shadow|enforce` setting defaulting to `shadow`; should it later merge with DEC's per-integration `decision_mode` enum for a single operator knob?
- Should affinity also span agents in the same thread when they share the same tier model (shared cache prefix is per system prompt, so likely no benefit)?
- Learned predictor (BERT dual-head + k-NN) as an optional extra in a later minor?
