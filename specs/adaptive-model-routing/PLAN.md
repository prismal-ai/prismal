# Prismal — Adaptive Model Routing (Tier Policies, Cascades, Batch Assignment, Plan Cache)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | AMR (Adaptive Model Routing) |
| **Target package version** | `3.16.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, FinOps, Security Lead |
| **Priority** | P1 (largest remaining cost lever after prompt caching) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2 rows "Routing" / "Model choice", §3.1 OmniRouter, §3.3 rows #4, #9, #10), `specs/token-efficiency/` (TKE1 usage fields, TKE6 effort), `specs/llm-gateway-providers/` (GW `gw:` model refs, no-double-fallback), `specs/typed-decision-models/` (DEC `DecisionPort`), `specs/cost-budget-governance/` (C `BudgetGuard`), `specs/agent-eval-harness/` (V predictor data) |

---

## 1. Executive Summary

Prismal chooses models **statically**: one global `default_model` plus per-feature settings (`skynet_worker_model`, `kokoro_judge_model`, `blind_review_*_model`, …). Every turn of every agent pays for whatever model the operator wired, regardless of how hard the request is, how much budget is left, or whether a cheaper model would have produced an equally good answer. The literature since 2023 is unambiguous that this is the single largest controllable cost lever after prompt caching: FrugalGPT cascades report up to 98 % cost reduction at matched quality **[verify]**, RouteLLM ~2× cheaper with strong/weak routing **[verify]**, and OmniRouter (Mei, Xu, Guo, Lin, Zhang — Rutgers; arXiv 2502.20576; KDD Explorations) reports up to +6.30 % accuracy and ≥ 10.15 % lower cost than S3, PO, EmbedLLM, RouterDC, Hybrid-LLM and CARROT by assigning a **batch** of queries jointly under an accuracy floor and per-model concurrency caps **[verify]**.

**Adaptive Model Routing (Phase AMR)** introduces a `RoutingPolicyPort` with composable policies — static tier map, budget-aware downgrade, cheapest-first cascade with typed adequacy checks, and an OmniRouter-style batch assigner for fan-out workloads (Skynet, MoA, eval sets) — plus two orthogonal savers: an **intent-keyed plan cache** (Agentic Plan Caching, −50.31 % cost / −27.28 % latency **[verify]**) and a **BatchExecutor** for offline jobs via provider batch APIs. Everything is opt-in behind `adaptive_routing_enabled=False`; with the flag off the compiled supervisor graph and every existing output are byte-for-byte unchanged.

---

## 2. Context and Problem (code facts, gap analysis §2)

- **Static model choice.** `providers/registry.py::ProviderRegistry.get_llm(model=None, streaming=False, temperature=None)` resolves `model or settings.default_model` and returns `ChatLiteLLM(model=…)`. There is no notion of task difficulty, tier, or budget at the call site. Feature-specific models are plain strings in `core/config.py`.
- **Single global fallback.** `get_llm_with_fallback()` wraps one `settings.fallback_model` via `with_fallbacks`. It is a *failure* fallback (network/quota), not a *quality* escalation, and it stacks with LiteLLM retries and — after GW — with gateway model fallbacks (risk of double fallback, already addressed by GW's DD).
- **Routing spends a frontier call.** `agents/intent_router.py::match_intent()` is a regex short-circuit; every non-regex turn goes to the LLM `SUPERVISOR` with the default (frontier) model. DEC-ROUTE (3.15.0) attacks the routing call itself; AMR attacks the *model choice of the routed agent*.
- **Budget only cuts, never adapts.** `budget/guard.py::BudgetGuard.check()` returns a `BudgetStatus(within, soft_exceeded, hard_exceeded, …)`; soft caps today only shorten patterns via `Degradation(reduce=True)`. Nothing downgrades the model when a soft cap is hit.
- **No plan reuse.** `agents/patterns/llm_compiler.py` and plan-and-execute flows call a planner LLM for every request, even for repetitive read-only workflows with identical intent and target.
- **No batch pricing.** Eval runs, BRP offline mode, contextual chunk generation (TKE8) and future memory consolidation (MEM4) run synchronously at full on-demand price; provider batch APIs offer ~50 % discounts **[verify]**.
- **Measurement prerequisite is in place by 3.13.0.** TKE1 adds `cache_read_tokens` / `cache_write_tokens` / `reasoning_tokens` to `budget/types.py::Usage` and gateway-reported cost precedence to `providers/cost.py` — AMR's savings counter (`prismal.routing_savings_usd`) is computed from these, never estimated ad hoc.

---

## 3. Target Users

- **FinOps / Operator:** wants spend to track task difficulty and remaining budget automatically, with a provable savings number per tenant.
- **AI Engineer / Flow Author:** wants Skynet / MoA / eval fan-outs to use cheap models where they suffice without hand-tuning per-feature model strings.
- **Platform Host (`prismal-server`):** wants per-tenant tier maps injected through `ConfigSourcePort` and zero behavior change unless enabled.
- **Security Lead:** wants routing to never downgrade a high-risk or critical action below a configured capability floor and never route sensitive data through a gateway the data policy forbids.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Spend reduction at matched quality | Cost on the standard eval set vs. 3.15.0 static baseline | −40 % with ≤ 1 pt quality loss |
| Fan-out efficiency | Skynet / MoA batch cost vs. all-frontier assignment at same α | ≥ −25 % at mean predicted accuracy ≥ α |
| Plan reuse | Plan-cache hit ratio on the repetitive-workflow eval set | ≥ 25 % |
| Offline cost | Batch-executed eval / consolidation jobs vs. on-demand | −50 % **[verify per provider]** |
| Cache-friendliness | Supervisor-turn cache-hit ratio (TKE1 metric) after enabling cascade | No drop > 5 pts vs. TKE baseline (cache affinity) |
| Solver performance | `BatchAssignmentPolicy.assign()` for N=200, K=8 | p95 ≤ 50 ms pure Python |
| Opt-in safety | All AMR flags off ⇒ graph snapshot + outputs unchanged | 100 % (snapshot test) |
| Observability | Routing decisions, escalations, plan-cache hits, batch jobs, savings emitted | 5 OTel instruments |

---

## 5. Scope

### In Scope
- **AMR1** — `prismal/routing/` types and `RoutingPolicyPort`; `StaticTierPolicy` (agent → tier → model ref via `routing_tier_models` YAML; refs may be `gw:openrouter/...`); `BudgetAwarePolicy` (soft cap ⇒ one-tier downgrade; hard cap unchanged → `BudgetGuard` aborts); `CompositePolicy`; `select_model(state, agent)` helper; `ProviderRegistry.get_llm(..., features=None)`.
- **AMR2** — `CascadePolicy`: start at cheapest allowed tier; escalate on (a) structured-output validation failure, (b) DecisionPort Noul adequacy below τ, (c) repeated tool-call errors; max 2 escalations; **cache affinity** (once a thread escalates it stays).
- **AMR3** — `BatchAssignmentPolicy` (OmniRouter-style Lagrangian dual decomposition + greedy fallback) and `SuccessPredictor` (per (intent, tier) historical success table from the eval harness, optional DecisionPort Score blend, output-length table from TKE1 usage).
- **AMR4** — `PlanCache` (intent-keyed, read-only intents only, TTL, invalidation on tool-catalog/prompt version change, small-tier adaptation) and `ResponseCache` (exact key, deterministic read-only tool-free turns, off by default).
- **AMR5** — `BatchExecutor` over provider batch APIs (Anthropic Message Batches, OpenAI Batch, OpenRouter `:batch` **[verify]**), polling with backoff, batch-price metering.
- **AMR6** — integration (supervisor-routed agents, Skynet, MoA, eval harness), OTel, `docs/routing.md`, notebook, example.

### Out of Scope
- Training a BERT dual-head predictor as in the paper (prismal ships a table predictor; a learned predictor is pluggable via `SuccessPredictorPort`).
- Embedding-based semantic response caches as a default (0–12 % hit rate on agentic workloads per arXiv 2602.18922 **[verify]**); pluggable only.
- Provider selection for a given model (owned by gateways — see DD-AMR-006).
- Replacing DEC-ROUTE (agent selection) — AMR selects the *model* for the already-selected agent.
- Changing `BudgetGuard` semantics or hard-cap behavior.

---

## 6. Phases Overview

| Phase | Focus | Depends on | Est. |
|---|---|---|---|
| AMR0 | Verify volatile facts (batch APIs, paper numbers, LiteLLM batch support) | — | 0.8 d |
| AMR1 | Types, port, static + budget-aware + composite policies, registry wiring | GW1, TKE1, C | 3.3 d |
| AMR2 | Cascade policy, adequacy check, cache affinity | AMR1, DEC1 | 3.0 d |
| AMR3 | Success predictor + batch assignment solver | AMR1, V | 4.4 d |
| AMR4 | Plan cache + response cache | AMR1 | 3.4 d |
| AMR5 | BatchExecutor | AMR1, GW | 3.0 d |
| AMR6 | Integration, observability, snapshot, docs | AMR1–5 | 4.5 d |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Quality regression from downgrades | Cascade escalation triggers; risk-tier capability floor; eval gate (≤ 1 pt loss) before `enforce` |
| Model switching fragments provider prompt caches | Cache affinity (sticky model per thread/agent); escalation is one-way within a thread |
| Double routing / double fallback with gateways | DD-AMR-006: prismal owns semantic model choice, gateway owns provider choice; `openrouter/auto` only as terminal tier `auto` |
| Predictor drift / stale success table | Versioned predictor file; online `on_outcome()` updates with Beta prior; recalibration task in eval harness |
| Plan cache returns a stale or unsafe plan | Read-only intents only, write-capable tool scopes excluded, tainted inputs never cached, version-keyed invalidation |
| Batch API semantics differ per provider | AMR0 verification; adapter per provider; fail-open to synchronous execution |
| Behavior leak when disabled | Every wiring point gated; snapshot test (SPEC-AMR-SNP-001) |

---

## 8. Dependencies

- `providers/registry.py` (`get_llm`, `get_llm_with_fallback`), `providers/gateways/` (GW `GatewayRouter`, `ResolvedModel`), `providers/cost.py` (TKE1 cache-aware pricing).
- `budget/guard.py`, `budget/resolve.py::get_budget_guard`, `budget/types.py::BudgetStatus`, `Usage` (TKE1 fields).
- `decisions/ports.py::DecisionPort` (DEC1) — optional; absent ⇒ adequacy check (b) disabled.
- `agents/intent_router.py::match_intent()` and DEC-ROUTE output as intent source; `agents/skynet/`, `agents/patterns/mixture_of_agents.py`, `agents/patterns/llm_compiler.py`.
- Eval harness (`specs/agent-eval-harness/`) for predictor data and the savings gate; `monitoring/otel.py`.

---

## 9. References

- OmniRouter — https://arxiv.org/abs/2502.20576 ; https://www.kdd.org/exploration_files/p107_Omnirouter_camera_ready.pdf ; https://github.com/dongyuanjushi/OmniRouter
- RouteLLM — https://arxiv.org/abs/2406.18665 ; FrugalGPT — https://arxiv.org/abs/2305.05176
- Agentic Plan Caching — https://arxiv.org/abs/2506.14852 ; semantic-cache hit rates on agentic workloads — https://arxiv.org/html/2602.18922v1
- Anthropic Message Batches — https://platform.claude.com/docs/en/build-with-claude/batch-processing
- OpenRouter auto router — https://openrouter.ai/docs/guides/routing/routers/auto

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial full SDD from the 2026-10 gap analysis (§2, §3.1, §3.3 #4/#9/#10) |
