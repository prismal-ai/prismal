# Prismal — Typed Decision Models (Laya / Jev System-One Decisions)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-05, v3.15.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | DEC (Typed Decision Models) |
| **Target package version** | `3.15.0` (SemVer minor — new opt-in functionality) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead, FinOps |
| **Priority** | P2 (cost + latency of typed verdicts; prerequisite for AMR and MEM) |
| **Depends on** | H (`runtime-hardening/`: `TaintRegistry`, `ToolPolicyEngine`, `IndirectInjectionDetector`), V (`agent-eval-harness/`), IDN (audit identity), GW (`llm-gateway-providers/`, optional: Jev via OpenRouter) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2 rows *Routing*, *Judges / gates*; §3.2; §4 principle 5), `specs/token-efficiency/` (TKE4 consumes DEC-CTX), `specs/adaptive-model-routing/` (AMR consumes DEC), `specs/agentic-memory/` (MEM consumes DEC) |

---

## 1. Executive Summary

Prismal pays a generative LLM call for every *typed verdict* it needs: which of ≤ 30 routes the
supervisor should take, whether a RAG chunk is relevant, whether an eval answer is faithful, whether
a HITL gate can auto-approve. These are classification problems with a closed answer space. In
September 2026 two **typed decision models** ("System One" models) appeared that answer exactly
this shape — **Choice** (one of N options), **Score** (one of 2–10 ordered levels) and **Noul**
(yes/no → P(true)) — with full probability distributions, at a fraction of an LLM call's cost and
latency:

- **Jev** (TypeSafe AI, hosted API, also via OpenRouter) — ~$0.042 / 1M input tokens, output free,
  70–650 ms **[verify]**.
- **Laya** (Convai, Apache-2.0, local encoder models incl. `laya-multilingual`) — ~33 ms on a T4,
  0.2–0.5 s on CPU, plus `laya-serve`, a Jev-compatible HTTP server **[verify]**.

Both are *literal*, cannot count or do arithmetic, are biased toward the first Choice option, and
are **not prompt-injection resistant**. Phase DEC therefore adds them as a **cascade behind a
port** with hard guard-rails: a typed decision *proposes*, deterministic code *authorizes*
(gap-analysis §4 principle 5). Every integration ships `off`, is promoted to `shadow` (decision runs
alongside today's LLM path and only logs agreement) and reaches `enforce` only after per-decision,
per-language calibration — including Spanish, which is a first-class traffic language for prismal
(`intent_router.py` already matches English + Spanish).

## 2. Context and Problem

Verified against the code (gap-analysis §2, 2026-10-04):

- **Routing.** `agents/intent_router.py::match_intent()` is a stdlib regex short-circuit that only
  fires for high-confidence intents (cron, ToT, ETL, debate/consensus, code review, multimodal,
  Kokoro, Skynet, BRP) and returns `None` otherwise; every `None` falls through to the LLM
  `SUPERVISOR`, i.e. **one frontier call per non-regex turn just to pick a route** from
  `effective_valid_routes`.
- **Judges / gates.** The eval harness (`prismal.eval.judges`, Phase V) uses LLM-as-judge; HITL
  decisions (`agents/subgraphs/gates.py::hitl_gate()`), the CRAG grader (`rag/crag`) and the Skynet
  evaluator (`SkynetSupervisor.evaluate()`) are LLM calls returning what is, structurally, a typed
  verdict. They are expensive, slow and high-variance.
- **No typed-decision seam.** `agents/extension/ports.py` defines `AuditPort`, `ToolProviderPort`,
  `PolicyPort`, `ObservabilityPort`, … but nothing for "answer a closed question with
  probabilities". There is no place to plug a cheaper classifier, no calibration concept and no
  shadow-mode harness.
- **Security baseline to preserve.** Phase H already provides `TaintRegistry`,
  `IndirectInjectionDetector`, `RunawayGuard` and `ToolPolicyEngine`; Phase IDN provides
  `PolicyPort` and agent DIDs; `AuditLogger` is hash-chained. DEC must not weaken any of them — a
  decision-model failure or manipulation must never relax a security control (fail-closed on
  security, gap-analysis §4 principle 4).

## 3. Target Users

- **Platform host / FinOps** — wants fewer frontier calls per turn and a provable cost delta.
- **AI Engineer / Flow author** — wants a one-line typed decision (`decide(...)`) with a cascade,
  calibration and fallback already handled.
- **Security Lead** — wants proof (tests) that decisions never authorize critical actions, never
  clear injection flags and never decide over tainted input alone.
- **Spanish-speaking operators (VE/ES deployments)** — need decisions calibrated on Spanish data
  before any enforcement.

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Cheaper routing | Share of non-regex supervisor turns routed without an LLM call, at agreement with the LLM supervisor (shadow-measured on the accepted subset) | ≥ 40 % routed at ≥ 97 % agreement |
| Cheaper eval judging | Eval-judge cost vs LLM-only judge, at aggregate score agreement | −50 % cost within 1 pt |
| Latency | p95 decision latency | ≤ 150 ms (Laya local GPU) / ≤ 700 ms (Jev) |
| Calibration quality | Post-calibration ECE per decision, per language slice (`en`, `es`) | ≤ 0.05 |
| Spanish parity | Precision at τ on the `es` slice vs `en` slice | gap ≤ 2 pt; else `es` stays in shadow |
| Security | Forbidden-transition rate on the DEC6 injection corpus (escalate → accept for a forbidden/critical decision) | 0 |
| Fail-closed | Backend error / timeout / abstention results in escalation, never "allow" | 100 % (tested) |
| Opt-in safety | All DEC flags off ⇒ compiled supervisor graph + outputs byte-for-byte unchanged | 100 % (snapshot test) |

## 5. Scope

### In Scope
- **DEC1** — `prismal/decisions/` types (`DecisionKind`, `QuestionSpec`, `Decision`, `GatePolicy`),
  `DecisionPort`, fake adapter, `DecisionCascade` (rule → Laya → Jev → LLM).
- **DEC2** — adapters: Jev-compatible `systemone_http` (TypeSafe API, OpenRouter, self-hosted
  `laya-serve`), `laya_local` (in-process), `jev_sdk` (`typesafe-sdk`), `llm_fallback`
  (structured output via `ProviderRegistry`).
- **DEC3** — calibration (temperature scaling for Laya, isotonic for Jev, threshold fitting,
  versioned files, startup enforcement), shadow mode, audit records.
- **DEC4** — integrations DEC-ROUTE (supervisor tier-1) and DEC-EVAL (judge cascade).
- **DEC5** — integrations DEC-HITL, DEC-TOOL, DEC-RAG, DEC-CTX, DEC-SWARM, DEC-SAFE.
- **DEC6** — security test suite (injection corpus incl. Octomind-style "pre-approved" attack,
  option-order shuffle, forbidden uses), `docs/decisions.md`, notebook, example.

### Out of Scope
- Fine-tuning Laya inside prismal (RLCD notebooks run out-of-band; prismal consumes checkpoints and
  calibration files).
- Shipping or operating `laya-serve` (host concern; prismal is only a client).
- Memory write/read gating (Jev-Mem pattern) — **MEM** consumes `DecisionPort` in 3.18.0.
- Model routing / difficulty prediction — **AMR** consumes DEC in 3.16.0.
- Multimodal decisions (both models are text-only).
- Any use listed as forbidden in SPEC-DEC-SEC-002.

## 6. Phases Overview

| Phase | Focus | Ships behind |
|---|---|---|
| DEC0 | Verify-before-build of all **[verify]** facts | — |
| DEC1 | Types, port, fake, cascade, gate policy | `decisions_enabled` |
| DEC2 | Backends (HTTP, Laya local, Jev SDK, LLM fallback) | `decision_backend_order` |
| DEC3 | Calibration, shadow mode, audit | `decision_modes` |
| DEC4 | Routing + eval-judge integrations | `decision_routing_enabled`, `decision_eval_judge_enabled` |
| DEC5 | HITL, tool, RAG, compaction, swarm, safety integrations | one flag each |
| DEC6 | Security suite, docs, notebook, release | — |

## 7. Fit per component (summary — rationale in ARCHITECTURE §3.1)

| Component | Kind | Fit |
|---|---|---|
| Supervisor routing (non-regex turns) | Choice | **good** |
| CRAG chunk relevance, TKE4 compaction relevance | Noul / Score | **good** |
| Eval judge — faithfulness, relevance, tone rubrics | Score / Noul | **good** |
| Eval judge — math / code / logic rubrics | — | **forbidden** (LLM-only) |
| HITL gates, tool proposal, Skynet text evaluator | Noul / Choice / Score | **partial** (bands / proposal only) |
| Safety pre-screen | Noul | **partial** (escalate-only) |
| Injection-flag clearing, RunawayGuard budgets, count/arithmetic/date, critical authorization, tainted input alone | — | **forbidden** |

## 8. Risks (summary)

| Risk | Mitigation |
|---|---|
| Prompt injection moves decisions (Octomind: block P 0.76 → 0.48) | Proposal-only; escalate-only in security contexts; taint ⇒ corroboration; authority-claim detector; injection corpus in CI |
| Over-confident raw outputs (Laya raw ECE 0.466) | Mandatory calibration before `enforce`; gate on calibrated `answer_confidence` + margin |
| Misreading vendor `confidence` (distribution shape, not P(answer)) | Two distinct fields; gate never uses `spread_confidence` |
| First-option bias | Option shuffle check for high/critical Choice |
| Spanish quality unknown (Jev "other languages untested"; Laya EN fails confidently on some scripts) | `laya-multilingual` for non-EN; `es` calibration slice required before enforce |
| Volatile vendor facts (price, rate limits, SDK names, ZDR) | **[verify]** tasks DEC0-xx; values in config, never hard-coded |
| Silent model upgrade | Pinned `jev_model` (never `jev-latest`); calibration keyed by model version; startup refuses enforce on mismatch |
| Behavior leak when off | Per-integration flags; snapshot test |

## 9. Dependencies

- `prismal/agents/intent_router.py`, `agents/supervisor.py` (`effective_valid_routes`).
- `prismal/agents/extension/ports.py` (`AuditPort`, `PolicyPort` style; `DecisionPort` lives in
  `prismal/decisions/ports.py`).
- `prismal/security/` (H: `TaintRegistry`, `IndirectInjectionDetector`, `ToolPolicyEngine`;
  `AuditLogger`; `SecurePromptBuilder`; `GuardrailsEngine`).
- `prismal/eval/` (V), `prismal/rag/crag`, `agents/subgraphs/gates.py`, `agents/skynet/`.
- `prismal/providers/registry.py` (`ProviderRegistry`, LLM fallback tier), GW credential refs.
- `prismal/core/config.py`, `core/exceptions.py`, `monitoring/otel.py`, `budget/meter.py`.

## 10. References

- M. Chromiak, *Typed Decision Models: Jev and Laya in Agentic AI* — https://mchromiak.github.io/articles/2026/Sep/17/Typed-Decision-Models-Jev-and-Laya-in-Agentic-AI/
- MarkTechPost, *TypeSafe AI releases Jev* — https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/
- TypeSafe docs: SDK https://docs.typesafe.ai/sdk/python · confidence https://docs.typesafe.ai/confidence.md · jaggedness https://docs.typesafe.ai/model-jaggedness/jev-1.13.md · models https://docs.typesafe.ai/models.md
- OpenRouter, Jev guide — https://openrouter.ai/docs/guides/community/jev
- Laya — https://github.com/NandhaKishorM/laya · https://laya.convaiinnovations.com/ · https://flowtivity.ai/blog/laya-open-source-jev-alternative/
- Jev-Mem — https://arxiv.org/abs/2609.23986 · CMU judge cascade — https://arxiv.org/abs/2609.26550
- LangChain, *Jev for agent evals in LangSmith* — https://www.langchain.com/blog/jev-agent-evals-langsmith
- VentureBeat (Octomind injection test) — https://venturebeat.com/security/companies-are-putting-jev-in-charge-of-ai-agent-decisions-and-prompt-injection-can-influence-the-verdict
- Microsoft Agent Framework issue #8556 ("a Choice result should be a proposal, not authorization") — https://github.com/microsoft/agent-framework/issues/8556

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial full SDD from gap-analysis 2026-10 §3.2 and the DEC design brief |
