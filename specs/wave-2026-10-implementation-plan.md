# Wave 2026-10 — Consolidated Implementation Plan

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Date** | 2026-10-05 |
| **Baseline** | `prismal` v3.12.0 (tag `prismal/v3.12.0`) |
| **Source** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md`, `specs/roadmap.md` (wave 9) |
| **Scope** | The seven `READY` SDDs of the wave: TKE, GW, DEC, AMR, PRT, HRS, MEM |
| **Status** | `ACTIVE` — M0, M1 (TKE, v3.13.0) and M2 (GW, v3.14.0) done 2026-10-05; next: M3 (DEC). Per-task detail stays in each spec's `TASKS.md` |

This document does **not** replace the per-spec `TASKS.md`. It sequences them, closes the
cross-spec gaps found while reading them together, and defines milestones with a done-gate.
Task IDs such as `TKE2-05` refer to the spec TASKS; IDs `X0-nn` / `Mn-nn` are new here.

---

## 1. Analysis summary

### 1.1 The seven specs at a glance

| Phase | Spec | Target | Own phases | Effort | Hard deps (in-wave) | Main new code |
|---|---|---|---|---|---|---|
| TKE | `token-efficiency/` | 3.13.0 | TKE0–TKE9 (+TKE10 P3) | 29.5 d (+2.8) | — (LH, C, OBS, V shipped) | `providers/prompt_cache.py`, `agents/{tool_gate,tool_search,artifacts,handoff}.py`, `utils/{toon,token_estimate,stable_json}.py`, `rag/context_budget.py`, `eval/metrics_tokens.py` |
| GW | `llm-gateway-providers/` | 3.14.0 | GW0–GW6 | 23.3 d | TKE1 | `providers/gateways/` (OpenRouter, OpenCode Zen/Go, OmniRoute, generic), `QuotaLedger`, `CostReconciler` |
| DEC | `typed-decision-models/` | 3.15.0 | DEC0–DEC6 | 24.4 d | GW (optional), TKE4 (DEC5-04 only) | `prismal/decisions/` (port, cascade, Laya/Jev/LLM adapters, calibration, shadow) |
| AMR | `adaptive-model-routing/` | 3.16.0 | AMR0–AMR6 | 22.4 d | TKE1, GW1, DEC1 | `prismal/routing/` (policies, cascade, OmniRouter solver, plan cache), `providers/batch_adapters.py` |
| PRT | `protocol-upgrades-2026/` | 3.17.0 | PRT0–PRT6 | 22.2 d | TKE1 + GW5-07 (only PRT5-06) | `a2a/v1/`, `a2a/{negotiation,convert,task_store,oauth}.py`, `mcp/{tool_cache,…}`, `events/agui.py` |
| HRS | `composition-root/DELTA-2026-10-host-read-surface.md` | 3.17.0 (with PRT) | HRS1 | 4.0 d | GW5, DEC3, AMR6, PRT1-06 | `composition/host_surface.py`, `RuntimeContext` read methods |
| MEM | `agentic-memory/` | 3.18.0 | MEM0–MEM6 | 20.9 d | DEC1–3, TKE2/TKE5, AMR5 (optional) | `memory/v2/` (port, adapters, write gate, recall, consolidation, playbooks) |
| | | | **Total** | **≈ 146.7 d** (+2.8 d P3) | | |

All seven share the same constitution: hexagonal port + adapters, master flag default `False`,
byte-for-byte graph snapshot when off, provider SDK imports only in `providers/` (and
`decisions/adapters/`), secrets via `ConfigSourcePort`/`CredentialVault`, verify-before-build
phase 0, strict TDD, SemVer minor per phase.

### 1.2 Real dependency graph (task level, not the roadmap's linear order)

```
TKE0 ─► TKE1 ──┬─► TKE2..TKE9 ─────────────────────────────► [3.13.0]
               │
               ├─► GW1 ─► GW2 ─► GW3 ─► GW4 ─► GW5 ─► GW6 ──► [3.14.0]
               │    │                           │
DEC1 (no deps) ┼────┼─► DEC2..DEC6 ─────────────┼──────────► [3.15.0]   (DEC5-04 needs TKE4)
               │    │      │                    │
               └────┴──────┴─► AMR1..AMR6 ──────┼──────────► [3.16.0]
                                                │
PRT0..PRT4 (no in-wave deps) ─► PRT5 (needs TKE1 + GW5-07) ─► PRT6 ┐
                                                                   ├─► [3.17.0]
                  HRS1 (needs GW5, DEC3, AMR6, PRT1-06) ───────────┘
                                                         MEM (needs DEC3, TKE2/5) ─► [3.18.0]
```

Key observations:

1. **TKE1 is the single gate** of the wave. Only TKE1 (not all of TKE) blocks GW, AMR and PRT5.
2. **PRT0–PRT4 and DEC1 have no in-wave dependencies** — they can run as a parallel track from day 1.
3. **Critical path:** TKE0 → TKE1 → GW1 → GW5 → (DEC1–DEC3 in parallel) → AMR1–AMR6 → HRS1 → release 3.17.0 → `prismal-server` 0.2.
4. Release order stays strictly TKE → GW → DEC → AMR → PRT+HRS → MEM (each snapshot test baselines against the previous minor), but **build work overlaps** across branches.

### 1.3 Cross-spec gaps found (must be fixed before build)

| # | Gap | Evidence | Fix (task) |
|---|---|---|---|
| G1 | **Two `ChatLiteLLM` wrappers with no composition contract.** TKE2-05 adds `CacheAwareChatModel`; GW1-07 adds `GatewayChatLiteLLM` (subclass, raw-response hook). Neither spec says how they stack, which one owns `cache_control` injection vs metadata capture, or what `get_llm` returns when both flags are on. | `token-efficiency/TASKS.md` TKE2-05; `llm-gateway-providers/ARCHITECTURE.md` §2 (only says "GW passes `cache_control` through unchanged") | X0-03 ADR |
| G2 | **`ProviderRegistry.get_llm` signature is extended by four phases** (TKE2 `agent=`, TKE6 effort/`max_tokens`, GW1 `agent=, session_id=`, AMR1 `features=, state=`), each with its own "kwargs identical to previous version" contract test. No single target signature. | TKE2-05, TKE6-02, GW1-08, AMR1-09 | X0-03 ADR (same) |
| G3 | **TKE phase order is not executable as written.** §1 says `TKE1 → {TKE2, TKE5, TKE6} → TKE3 → TKE4`, but TKE5-02 depends on TKE4-02 (`ArtifactStore`) and TKE4-04 depends on TKE5-01 (`token_estimate`). | `token-efficiency/TASKS.md` §1 vs TKE4/TKE5 tables | X0-02 (reorder: shared foundations first) |
| G4 | **AMR1-01 omits the master flag.** SPEC defines `adaptive_routing_enabled` (and HRS-RTE-001 reads it) but the AMR1-01 task lists only `routing_*`, `plan_cache_*`, `response_cache_enabled`, `batch_*`. | `adaptive-model-routing/SPEC.md` §CFG vs TASKS AMR1-01 | X0-04 (one-line TASKS amend) |
| G5 | **Shared hot files touched by every phase:** `core/config.py`, `core/exceptions.py`, `monitoring/otel.py`, `providers/registry.py`, `providers/cost.py`, `budget/meter.py`, `RunSummary`. Parallel branches will conflict. | all specs (CFG-001 / ERR-001 / OTEL-001 tasks) | X0-05 (convention) |
| G6 | **`RunSummary` gains fields from three phases** (TKE1-07 cache fields, GW5-07 `gateway`/`served_model`, PRT5-06 usage wire with exactly 12 keys). The 12-key wire is the contract with `prismal-server`/`prismal-sdk`; it must be frozen only after TKE1+GW5. | TKE1-07, GW5-07, PRT5-06, PRT6-07 | Sequencing rule in M5 |
| G7 | **`compute_cost_usd` grows by phase** (TKE1 cache kwargs + `reported_cost_usd`, GW5-01 `cost_mode`/`equivalent_usd`, AMR5-05 `batch=`). Needs one precedence table owned by TKE1. | TKE1-03, GW5-01, AMR5-05 | X0-03 ADR appendix |
| G8 | **Time-sensitive verify facts.** GW0-06: DeepSeek ZDR on OpenCode "through October 2026" expires during this wave; DEC0 Jev facts (launched 2026-09-15) and PRT0 (AG-UI 1.0 2026-09-30) are weeks old. | GW0-06, DEC0-*, PRT0-03 | Verify just-in-time (X0-06) |

---

## 2. Plan overview

| Milestone | Content | Release | Effort | Can overlap with |
|---|---|---|---|---|
| **M0** | Spec hygiene + reconciliation (G1–G8) | — | ~2.5 d | — |
| **M1** | TKE — measure first, then cut tokens | **3.13.0** | 29.5 d | Track P (PRT0–4), DEC1 |
| **M2** | GW — gateways | **3.14.0** | 23.3 d | M1 after TKE1 |
| **M3** | DEC — typed decisions (shadow first) | **3.15.0** | 24.4 d | M2 |
| **M4** | AMR — adaptive routing | **3.16.0** | 22.4 d | M3 after DEC1 |
| **M5** | PRT + HRS — protocols + host read surface | **3.17.0** | 26.2 d (≈ 15 d already done on Track P) | — |
| **M6** | MEM — agentic memory | **3.18.0** | 20.9 d | M5 |
| **M7** | Out-of-repo follow-ups (server 0.2, sdk 0.2, front-ends) and 4.0.0 | — | — | — |

Single developer, strictly sequential: ≈ 150 d. With Track P (PRT0–PRT4 + DEC1) and GW overlapping
TKE2–TKE9: ≈ 115–120 d of wall-clock work.

---

## 3. Milestones, phases and tasks

### M0 — Spec hygiene and reconciliation (≈ 2.5 d) — ✅ DONE 2026-10-05

| ID | Task | Est. | Dep | Closes | Status |
|---|---|---|---|---|---|
| X0-01 | Commit the SDD wave (6 spec folders, HRS delta, gap analysis, roadmap update, this plan) on a `spec/wave-2026-10` branch; PR → `develop` | 0.2 d | — | — | DONE — branch `spec/wave-2026-10`, committed (PR → `develop` pending) |
| X0-02 | Amend `token-efficiency/TASKS.md` §1 phase order: TKE1 → **foundations** {TKE2-06 `stable_json`, TKE4-02 `ArtifactStore`, TKE5-01 `token_estimate`} → {TKE2, TKE5, TKE6} → TKE3 → TKE4 → {TKE7, TKE8} → TKE9 | 0.2 d | — | G3 | DONE — `token-efficiency/TASKS.md` §1 |
| X0-03 | **ADR-WAVE-001 "LLM construction contract"**: (a) final `get_llm(model=None, *, agent=None, session_id=None, features=None, state=None, **kw)` signature and which phase adds which kwarg; (b) wrapper stack order — proposal: `GatewayChatLiteLLM` is the *base class* (metadata capture at raw-response seam), `CacheAwareChatModel` applies cache policy on the *input* side, AMR only chooses the model string before construction; (c) `compute_cost_usd` precedence table (gateway-reported → LiteLLM `completion_cost` → table → zero; `batch` and `cost_mode` modifiers). Amend TKE SPEC CCH-003, GW SPEC REG-001, AMR SPEC INT-001 to reference it | 1.0 d | — | G1, G2, G7 | DONE — `docs/adr/ADR-WAVE-001-llm-construction-contract.md`; TKE CCH-003/ACC-003, GW REG-001/COST-001, AMR INT-001 + TKE2-05/GW1-08/AMR1-09 amended |
| X0-04 | Add `adaptive_routing_enabled` to AMR1-01 in `adaptive-model-routing/TASKS.md` | 0.1 d | — | G4 | DONE |
| X0-05 | Hot-file convention: each phase adds its settings as one contiguous `# --- <PHASE> ---` block + one `_validate_<phase>` in `core/config.py`, one exception subtree in `core/exceptions.py`, one `_register_<phase>_metrics()` in `monitoring/otel.py`; rebase parallel branches on `develop` before each merge. Document in `CLAUDE.md` testing notes | 0.3 d | — | G5 | DONE — `CLAUDE.md` § Wave 2026-10 conventions |
| X0-06 | Verify-fact policy: run TKE0 + GW0 + PRT0 up front (they gate the earliest work and are partly done); run DEC0, AMR0, MEM0 **just-in-time** at the start of their milestone (volatile vendor facts). Record outcomes in each spec's `VERIFY.md` / ADR as the specs already require | 0.2 d | — | G8 | DONE — `CLAUDE.md` § Wave 2026-10 conventions |
| X0-07 | Reconcile roadmap staleness: confirm GitHub Release v3.12.0 exists (tag present); add a link to this plan in `specs/roadmap.md` wave 9 | 0.2 d | X0-01 | — | DONE — Release v3.12.0 exists (2026-07-20); 3.7–3.8 shipped inside v3.9.0, no retro-tags; roadmap linked |
| X0-08 | Snapshot-baseline rule: each spec's "byte-for-byte vs previous version" test baselines against **the last released minor at merge time** (not a hard-coded number), so overlapping branches don't invalidate each other | 0.3 d | — | G5/G6 | DONE — `CLAUDE.md` testing notes |

**Done when:** PR merged to `develop`; ADR-WAVE-001 accepted; the three spec amendments merged. *(Committed locally 2026-10-05; push + PR still to do.)*

---

### M1 — TKE: Token Efficiency → 3.13.0 (29.5 d; TKE10 deferred) — ✅ IMPLEMENTED 2026-10-05

> Open items carried forward: TKE1-08 baseline capture + TKE9-04 eval A/B (trajectories record 0 tokens for internal LLM calls — needs a live run and/or meter-based capture); not tagged/released yet.

Principle: **measure first** — nothing optimizes until the TKE1 baseline is committed.

| Phase | Tasks | Est. | Gate (done when) |
|---|---|---|---|
| **M1.1 Verify** | TKE0-01..06 (usage fields per provider, LiteLLM cache pricing, `cache_control` passthrough, `defer_loading`, `reasoning_effort`, canary inventory) | 1.5 d | `config/prompt_cache.yaml` written; fixtures recorded |
| **M1.2 Accounting (measure)** | TKE1-01..09 — `Usage`/`TokenCounts` cache+reasoning fields, extraction, cache-aware `compute_cost_usd`, meter, settings + exceptions, OTel, `RunSummary`, token report + **baseline `tests/eval/baselines/tokens.json`** | 3.8 d | defaults reproduce v3.12.0 cost/usage; baseline committed. **Unblocks GW, AMR, PRT5** |
| **M1.3 Foundations** (X0-02) | TKE2-06 `stable_json` + sorted tool binding · TKE4-02 `ArtifactStore` (memory + SQLite, tenant/run scope) · TKE5-01 `token_estimate` | 1.5 d | three modules green in isolation |
| **M1.4 Prompt caching + stable layout** | TKE2-01..05, TKE2-07..10 (cache family, policy, Anthropic breakpoints, OpenAI `prompt_cache_key`, `CacheAwareChatModel` per ADR-WAVE-001, `PromptLayout`/HMAC prefix canary, move per-request values to `TurnContext`, red-team parity) | 4.9 d | byte-identical prefixes across two supervisor turns; **canary detection parity on H red-team corpus** (release blocker) |
| **M1.5 Output budgets + TOON · Effort** | TKE5-02..05 · TKE6-01..03 | 3.0 d | no tool output above budget reaches `messages`; effort only on supporting models |
| **M1.6 Cache-safe tool exposure** (delta LH2) | TKE3-01..08 (mask mode, `tool_gate`, interceptor re-check, BM25 tool search, append-only activation + `refresh(ttl_ms)` seam for PRT, native `defer_loading`) | 3.9 d | tool list identical across phases in mask mode; **LH tests pass unmodified** |
| **M1.7 Observation masking** (delta LH1) | TKE4-01, 03..06 (mask strategy, `read_artifact` tool, hysteresis, `relevance_fn` seam for DEC) | 2.4 d | order + tool-call pairing preserved; hysteresis compacts once per band |
| **M1.8 Handoff envelopes · RAG budget** | TKE7-01..04 (Skynet, MoA, Kokoro, BRP, parallel_research, spawner) · TKE8-01..05 (`fit_to_budget`, `[rerank]`, `[compression]`) | 5.0 d | legacy checkpoints load; retrieved context ≤ budget; missing extras never raise |
| **M1.9 Integration + gate + release** | TKE9-01..08 (snapshot all-off, disabled contract, CI token gate report-only→blocking, eval A/B, AST guards, docs, example, notebook, README/CHANGELOG) + **R-checklist** (§4) | 3.3 d | PLAN §4 targets met (≥ 50 % input cost, ≥ 30 % tokens/success, ≥ 60 % supervisor hit ratio, 0 solve-rate regression) |
| *(deferred)* | TKE10 code-mode for MCP chains — move to a later minor; do not block 3.13.0 | 2.8 d | — |

---

### Track P — parallel work started during M1 (no in-wave deps)

Run on separate branches while M1 is in progress; they merge but are **released** in their own milestone.

| ID | Tasks | Est. | Notes |
|---|---|---|---|
| P-1 | PRT0-01..05 — A2A v1.0/v1.0.1, MCP 2025-11-25 + 2026-07-28, AG-UI 1.0 verification + `ADR-PRT-001` + golden fixtures | 2.0 d | do early: AG-UI 1.0 (2026-09-30) and MCP SDK support decide the scope of PRT3/PRT5 |
| P-2 | PRT1-01..06 — A2A v1 types, negotiation, codecs, versioned Agent Card | 2.6 d | v0.3 card snapshot unchanged |
| P-3 | PRT2-01..09 — dual-stack handler, `tasks/list`, tenant mismatch, OAuth PKCE + device code, v1 client | 4.3 d | |
| P-4 | PRT3-01..07 — MCP protocol adapter (legacy/stateless/auto) + TTL tool-list cache | 4.0 d | wire PRT3-06 `ttlMs` into TKE3-06 `refresh(ttl_ms)` once TKE3 is merged |
| P-5 | PRT4-01..06 — MCP tasks extension + input requests | 2.4 d | |
| P-6 | DEC1-01..10 — decision types, port + contract suite, fake, gate, validation, state builder, cascade, metrics | 4.8 d | DEC1-11 snapshot waits until 3.14.0 is released (X0-08) |

---

### M2 — GW: LLM Gateway Providers → 3.14.0 (23.3 d; starts once TKE1 is merged)

> **Implemented 2026-10-05.** GW0 changed three facts (`llm-gateway-providers/VERIFY.md`): OmniRoute floor 3.8.51 (CVE-2026-88062 affects ≤ 3.8.50), no OmniRoute version endpoint (`omniroute_declared_version`), DeepSeek ZDR on OpenCode ended 2026-08-31 (deny-list). Open: GW6-07 notebook (other repo); `config/opencode_models.yaml` is illustrative.

| Phase | Tasks | Est. | Gate |
|---|---|---|---|
| **M2.1 Verify** | GW0-01..08 (OpenRouter mostly verified 2026-10-04; LiteLLM hook name; OpenCode endpoints/ToS/**retention exceptions — DeepSeek ZDR expires Oct 2026**; OmniRoute ≥ 3.8.49 CVE fix; vault accessor) → `VERIFY.md` | 1.3 d | every fact sourced + dated |
| **M2.2 Port, router, registry** | GW1-01..10 (`GatewayPort`, policy, generic OpenAI-compatible adapter, `GatewayRouter`, `GatewayChatLiteLLM` per ADR-WAVE-001, `get_llm(agent=, session_id=)`, **no double fallback**, snapshot + kwargs contract + no-`os.environ`-write) | 5.2 d | non-`gw:` refs and flag-off path byte-identical; `resolve()` does zero I/O |
| **M2.3 OpenRouter** | GW2-01..10 (resolve + attribution headers + sticky `session_id`, ZDR/`data_collection: deny` defaults, `models[]` fallback ownership, variants + `auto` gate, reported cost, `/key` + `/generation`, 402/429 typing, plugins allow-list) | 4.0 d | every captured request carries privacy defaults |
| **M2.4 OpenCode Zen/Go + QuotaLedger** | GW3-01..09 | 3.8 d | non-coding agent → `GatewayPolicyError` with zero requests; exhausted 5 h window refuses pre-network |
| **M2.5 OmniRoute + compliance** | GW4-01..07 | 2.1 d | cannot enable below version floor or without attestation |
| **M2.6 Cost, quota → Budget, OTel** | GW5-01..08 (`cost_mode`/`equivalent_usd`, meter tags, ledger record, `CostReconciler`, quota gauge + `Degradation`, 6 metrics, `RunSummary.gateway/served_model`) | 3.2 d | OpenRouter meters `source="gateway"`; Go meters `cost_usd=0`, `equivalent_usd>0`. **Unblocks PRT5-06, HRS1-04** |
| **M2.7 Integration + release** | GW6-01..09 (`build_runtime` gateways, per-tenant keys, secret hygiene, `live_api` OpenRouter `:free`, docs, examples, notebook, eval) + R-checklist | 3.7 d | coverage ≥ 80 % on `providers/gateways/` |

---

### M3 — DEC: Typed Decision Models → 3.15.0 (24.4 d; DEC1 already done on Track P)

| Phase | Tasks | Est. | Gate |
|---|---|---|---|
| **M3.1 Verify (just-in-time)** | DEC0-01..09 (Jev wire format + pinned model, `typesafe-sdk`, confidence bands, price/limits, ZDR, Laya API + `laya-serve`, **reproduce Laya benchmarks**, Octomind/CMU sources) | 1.9 d | facts in `docs/decisions.md#verified-facts` |
| **M3.2 Finish DEC1** | DEC1-11 snapshot vs 3.14.0 | 0.3 d | |
| **M3.3 Backends** | DEC2-01..06 (SystemOne HTTP incl. OpenRouter `/api/v1/systemone` via GW2-01, Laya local, Jev SDK shim, LLM fallback, extras `[laya]`/`[jev]`, secret-leak test) | 3.6 d | all adapters pass the contract suite (never raise) |
| **M3.4 Calibration, shadow, audit** | DEC3-01..06 (temperature/isotonic, `CalibrationStore` + startup enforcement, **Spanish slice** + per-language mode split, `ShadowRunner`, hash-only audit, calibrate CLI) | 4.0 d | enforce refuses to start without calibration. **Unblocks MEM, HRS1-06** |
| **M3.5 Routing + eval judge** | DEC4-01..04 (supervisor after `match_intent`, option-count routing, eval-judge cascade) | 3.2 d | enforce routes accepted turns without LLM call |
| **M3.6 Other integrations** | DEC5-01..06 (HITL bands, tool proposal, CRAG grading, TKE4 relevance scorer, Skynet evaluator, safety escalate-only) | 3.0 d | HITL/SAFE monotonicity proven |
| **M3.7 Security + release** | DEC6-01..06 (EN/ES injection corpus, shuffle test, forbidden-use AST/behaviour tests, bench, docs, release) + R-checklist | 3.6 d | zero forbidden transitions on corpus |

---

### M4 — AMR: Adaptive Model Routing → 3.16.0 (22.4 d; needs TKE1, GW1, DEC1)

| Phase | Tasks | Est. | Gate |
|---|---|---|---|
| **M4.1 Verify (just-in-time)** | AMR0-01..04 (OmniRouter/RouteLLM/FrugalGPT/APC figures, batch APIs, LiteLLM batch support → adapter path) | 0.8 d | |
| **M4.2 Port + static/budget/composite** | AMR1-01..09 (incl. `adaptive_routing_enabled`, X0-04; 26-agent parity tier map; `shadow` mode; `get_llm(features=, state=)` per ADR-WAVE-001) | 3.3 d | parity map yields identical `ChatLiteLLM.model` for all agents |
| **M4.3 Cascade + floors** | AMR2-01..05 (cascade, `AdequacyChecker` over DEC Noul, cache affinity, gateway interplay — no `models[]` under cascade, security floors) | 3.0 d | small → medium escalation; affinity persists |
| **M4.4 Predictor + batch assignment** | AMR3-01..07 (Beta-posterior predictor, eval-report export, Lagrangian dual + greedy solvers, brute-force fixtures, hypothesis, bench p95 ≤ 50 ms) | 4.4 d | dual == brute force on all feasible fixtures; deterministic |
| **M4.5 Plan + response cache** | AMR4-01..07 | 3.4 d | tool-catalog change forces miss; no cross-tenant hit |
| **M4.6 BatchExecutor** | AMR5-01..06 (Anthropic/OpenAI/OpenRouter `:batch` adapters, executor, batch-price metering, taint/HITL exclusion) | 3.0 d | failing job falls back to sync |
| **M4.7 Integration + release** | AMR6-01..09 (OTel, Skynet/MoA `assign()`, eval `--routing` gate −40 % cost / ≤ 1 pt, supervisor-routed agents, snapshot, docs, AST guard) + R-checklist | 4.5 d | eval gate met in shadow. **Unblocks HRS1-07** |

---

### M5 — PRT + HRS → 3.17.0 (≈ 11.4 d remaining after Track P)

| Phase | Tasks | Est. | Gate |
|---|---|---|---|
| **M5.1 Rebase Track P** | Rebase PRT1–PRT4 branches on 3.16.0; re-run fixtures; wire PRT3-06 TTL into TKE3-06 `refresh(ttl_ms)` | 0.5 d | |
| **M5.2 AG-UI + usage wire** | PRT5-01..08 (12 golden `astream` transcripts, event models, translator, state snapshot/delta, interrupts, **`RunSummary.to_usage_wire` — 12 keys, frozen now that TKE1 + GW5-07 fields exist (G6)**, `finish_run` usage) | 3.6 d | 12 transcripts match; START/END balanced |
| **M5.3 Host read surface** | HRS1-01..10 (view types, `build_runtime(config_source=)`, `budget_status`, `quota_windows`, audit page + chain verify, `DecisionStatsRecorder`, `RoutingStatsRecorder`, `a2a_routes`, signature snapshot, docs) | 4.0 d | signature snapshot frozen for 3.x |
| **M5.4 Interop, guards, release** | PRT6-01..09 (11 metrics, `events/**` HTTP-free guard, snapshot, MCP/A2A integration, `interop` conformance suites, **usage-wire fixture shared with server/SDK**, docs, example) + R-checklist | 3.3 d | `pytest -m "unit or integration"` green |

---

### M6 — MEM: Agentic Memory → 3.18.0 (20.9 d)

| Phase | Tasks | Est. | Gate |
|---|---|---|---|
| **M6.1 Verify (just-in-time)** | MEM0-01..05 (Jev-Mem, Mem0/Zep/ACE figures, Graphiti API/licence, **legacy memory signatures**, LoCoMo licence) | 0.8 d | MEM0-04 blocks MEM1-05/06/10 |
| **M6.2 Port, adapters, tenancy, forget** | MEM1-01..11 (legacy stores wrapped never edited, `MemoryService`, `ForgetPolicy` + "only ForgetPolicy calls purge" AST guard, facades, idempotent backfill) | 5.0 d | safety invariants exist before any gate |
| **M6.3 Decision-gated writes + taint** | MEM2-01..06 (T1–T7 taint rules, shadow) | 3.0 d | tainted `UPDATES` never supersedes untainted |
| **M6.4 Budgeted recall** | MEM3-01..05 (view routing, multi-hop, sufficiency/stop, TOON render, prompt tail block) | 2.7 d | recall never exceeds `budget_tokens` (property test) |
| **M6.5 Consolidation** | MEM4-01..06 (cron per org, merge, contradictions, episodic→semantic via `BatchExecutor` if available, retention rules) | 2.7 d | second run reports zeros; never calls `purge` |
| **M6.6 Playbooks** | MEM5-01..04 (ACE curate/reflect, delta discipline) | 2.3 d | prefix bytes identical across turns |
| **M6.7 Graph, eval, release** | MEM6-01..08 (SQLite temporal graph, optional Graphiti, OTel, LoCoMo subset, snapshot, security tests, docs) + R-checklist | 4.4 d | eval meets PLAN §4 in shadow |

---

### M7 — After the wave (out of this repo / later majors)

- `prismal-server` 0.2 (`host-v0.2-usage-gateways-agui/`, RH2) — after 3.17.0 (needs HRS surface + usage wire).
- `prismal-sdk` 0.2 (CS2) → `prismal-dashboard` DSH2 → webchat → tui → chatbot.
- TKE10 code-mode (deferred minor).
- **4.0.0**: remove A2A v0.3 — only after every host speaks v1.0.

---

## 4. Per-release checklist (R-checklist, every milestone)

1. Full gate: `uv run pytest -m "not live_api"` (with `PRISMAL_LLM_PROVIDER=""` for Kokoro-style suites), `uv run ruff format .` (**whole tree** — CI checks all files), `uv run ruff check .`, `uv run mypy prismal`, `uv run bandit -r prismal -c pyproject.toml`; coverage ≥ 80 % on new modules.
2. Graph snapshot all-flags-off + disabled-contract test green against the previous minor (X0-08).
3. Spec reconciliation: `Status` → `IMPLEMENTED` in the four docs; TASKS statuses `DONE`; [verify] markers resolved or dated.
4. `pyproject.toml` bump, `CHANGELOG.md` section, `README.md`, `CLAUDE.md` architecture section, `specs/roadmap.md` row moved to ✅.
5. PR feature → `develop`, then `develop` → `main`; tag `prismal/vX.Y.Z` (triggers PyPI + GitHub Release + GHCR).
6. Notebook in `prisma-notebooks`.

---

## 5. Top risks across the wave

| Risk | Impact | Mitigation |
|---|---|---|
| Wrapper/`get_llm` conflicts between TKE2, TKE6, GW1, AMR1 (G1/G2) | Broken kwargs contracts, double-wrapped models | ADR-WAVE-001 before M1.4; one registry contract test extended per phase |
| Canary relocation weakens injection detection (TKE2-07) | Security regression | Red-team parity is a **release blocker** for 3.13.0 |
| Vendor facts change (Jev, Laya, OpenCode, AG-UI, MCP SDK) | Rework | Just-in-time verify; volatile values in config, not code |
| Long-lived parallel branches (Track P) drift | Merge pain | Rebase on each release; hot-file convention (X0-05) |
| Laya zero-shot below baseline / scarce Spanish labels | DEC stays in shadow | Acceptable: DEC ships shadow-only; enforce per language when calibrated |
| Scope (≈ 147 d) | Wave slips | TKE10 deferred; GRF-001 (MEM graph) and RSC-001 (AMR response cache) are SHOULD — cut first if needed |
