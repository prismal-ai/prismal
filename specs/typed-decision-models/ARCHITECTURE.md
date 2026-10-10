# Prismal Typed Decision Models — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-05, v3.15.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | DEC |
| **Target package version** | `3.15.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/typed-decision-models/PLAN.md` |
| **SPEC** | `specs/typed-decision-models/SPEC.md` |
| **TASKS** | `specs/typed-decision-models/TASKS.md` |

---

## 1. Context

- `agents/intent_router.py::match_intent()` is stdlib-only regex (EN + ES), side-effect free, and
  returns `None` for anything not unambiguous; the supervisor then makes a frontier LLM call over
  `effective_valid_routes` (≤ 30 routes; 26 specialist agents + opt-in subgraphs).
- `agents/extension/ports.py` holds the hexagonal `Protocol`s (`AuditPort`, `ToolProviderPort`,
  `PolicyPort`, `ObservabilityPort`, …) with the "sync, O(1), must not raise on the hot path"
  convention for policy-like ports. There is no typed-decision port.
- Typed verdicts are LLM calls today: eval judges (V), `hitl_gate()` pre-screens, the CRAG grader,
  `SkynetSupervisor.evaluate()`.
- Jev and Laya (gap-analysis §3.2) answer Choice / Score / Noul with distributions. Jev's
  `confidence` is a **distribution-shape** measure (1.0 = all mass on one outcome), *not* P(chosen
  answer); vendor bands: > 0.9 act, 0.5–0.9 confirm, < 0.5 don't act **[verify]**. Both models are
  literal, cannot count/compute/compare dates, mishandle double negatives and irrelevant context,
  show first-option bias in Choice, cannot interpolate Score, and are **not injection-resistant**.

## 2. Feasibility with the existing core

- **Port pattern** — `DecisionPort` mirrors `ToolProviderPort`/`PolicyPort`: `@runtime_checkable`
  Protocol, failures absorbed into a typed result (abstention), composition in `build_runtime`.
- **Provider isolation** — Critical Rule #4 gains one AST-test exemption: `prismal/decisions/adapters/`
  may import `laya` / `typesafe_sdk` (deferred, extras-gated).
- **Secrets** — `jev_api_key` / `laya_api_key` resolve through `ConfigSourcePort` /
  `CredentialVault` (W, IDN); HTTP adapters take a `SecretStr` and only call `.get_secret_value()`
  when building the `Authorization` header.
- **Security primitives exist** — `TaintRegistry`, `IndirectInjectionDetector`, `ToolPolicyEngine`
  (H); `InputSanitizer`, `GuardrailsEngine`, `SecurePromptBuilder`, hash-chained `AuditLogger`.
  DEC consumes them; it modifies none.
- **Graph invariance** — every integration runs *inside* an existing node (supervisor, judge,
  gate, grader, evaluator); no new node or edge ⇒ the snapshot test is straightforward.
- **Async** — Laya inference runs in `asyncio.to_thread`; shadow tasks are `create_task` with a
  timeout and a done-callback that counts errors. **Cost** — Jev `usage` → `CostMeter` (fail-open).

## 3. Proposed architecture

### 3.1 Fit per component

| Component (file) | Kind | Fit | Rationale |
|---|---|---|---|
| Supervisor routing (`agents/supervisor.py`) | Choice | **good** | Closed option set, developer-authored descriptions, high volume; LLM fallback preserves today's behavior |
| CRAG chunk grader (`rag/crag`) | Noul | **good** | Binary relevance, per-chunk abstention → LLM grader for that chunk only |
| TKE4 compaction relevance (`agents/context_compaction.py`) | Score | **good** | Advisory ranking; abstention keeps default policy |
| Eval judge: faithfulness / relevance / tone (`prismal/eval/judges`) | Score / Noul | **good** | CMU cascade within 0.9 pt of frontier at 41 % cost; LangSmith test narrow/early **[verify]**; 5 % LLM audit |
| Eval judge: math / code / logic rubrics | — | **forbidden** | Models cannot count or compute; LLM-only |
| HITL gates (`agents/subgraphs/gates.py`: dev_pipeline trio, BRP correction, Kokoro action) | Noul | **partial** | Bands only (auto / confirm / human); never sole approver of irreversible / critical |
| Tool proposal (phase-allowed set, LH2) | Choice | **partial** | Proposal only — "a Choice result should be a proposal, not authorization" (MAF #8556) |
| Skynet evaluator (`SkynetSupervisor.evaluate`) | Score | **partial** | Text quality only; completeness/numeric checks remain deterministic |
| Safety pre-screen (`GuardrailsEngine`, NeMo) | Noul | **partial** | Monotone: may only escalate to the full engine, never clear |
| Kokoro judge verdict / generative rationales | — | **out** | Needs generated reasoning, not a typed label |
| `IndirectInjectionDetector` flag clearing | — | **forbidden** | Injection can move the verdict (Octomind 0.76 → 0.48) |
| `RunawayGuard` / `BudgetGuard` limits, cron / `DateTimeService` | — | **forbidden** | Counting, arithmetic, dates |
| `PolicyPort` / `ToolPolicyEngine` authorization of critical actions | — | **forbidden** | Deterministic code authorizes (gap-analysis §4.5) |
| Any decision over tainted input without deterministic corroboration | — | **forbidden** | SEC-003 |

### 3.2 Modules

| Module | Purpose |
|---|---|
| `decisions/types.py`, `ports.py` | Value objects, `DecisionPort` |
| `decisions/state.py` | `DecisionStateBuilder` — sanitize, fence, taint, authority claims, language |
| `decisions/validation.py`, `gate.py` | Forbidden-use lint; `evaluate_gate`, `shuffle_options` |
| `decisions/cascade.py` | `DecisionCascade` (tiers, timeouts, escalation, calibration application, audit) |
| `decisions/adapters/` | `systemone_http`, `laya_local`, `jev_sdk`, `llm_fallback`, `fake` |
| `decisions/calibration.py` | Labeled sets, temperature / isotonic fitting, τ fitting, `CalibrationStore` |
| `decisions/shadow.py`, `audit.py` | `ShadowRunner`, `promotion_ready`; `DecisionAuditRecord` → `AuditPort` |
| `decisions/integrations/` | `route`, `eval_judge`, `hitl`, `tool`, `rag`, `ctx`, `swarm`, `safety` |
| `security/decision_bridge.py` | The only security-side importer: DEC-SAFE escalate-only bridge |
| `config/decision_calibration/` | `<decision_id>@<model>.json` (versioned, reviewed in PRs) |

### 3.3 Cascade data flow

```
caller (integration, mode ∈ {shadow, enforce})
   │  DecisionStateBuilder: L1 sanitize → fence untrusted → taint/authority flags → lang → sha256
   ▼
validate_question (registration time, cached)  ── forbidden ──► ForbiddenDecisionError (startup)
   ▼
DecisionCascade.decide(state, questions, policy)          budget: policy.timeout_ms (non-LLM)
   │
   ├─ tier "rule"  : deterministic hook (e.g. match_intent) ── hit ─► ACCEPTED (backend=rule)
   ├─ tier "laya"  : supports(lang, n_options ≤ 20)? → predict → apply_calibration
   │                  evaluate_gate(τ(tier), margin, shuffle?, taint?) ── pass ─► ACCEPTED
   ├─ tier "jev"   : supports(n_options ≤ 255)? → POST /systemone → model==pinned? → calibrate
   │                  evaluate_gate(...) ── pass ─► ACCEPTED
   ├─ tier "llm"   : SecurePromptBuilder + structured output (terminal, pre-DEC semantics)
   │                  ──► ACCEPTED (backend=llm)       [absent ⇒ ESCALATED to caller's path]
   │  any abstain / timeout / error / gate-fail ⇒ next tier, escalated_from += tier
   ▼
audit_decision(record)  +  OTel (decisions_total, latency, llm_calls_avoided_total)
```

### 3.4 Routing integration (DEC-ROUTE)

```
supervisor_node(state)
   ├─ match_intent(last_human)  ── route ─► (unchanged)
   └─ None
        ├─ mode=off      ─► LLM SUPERVISOR (unchanged)
        ├─ mode=shadow   ─► ShadowRunner.launch(Choice over effective_valid_routes+FINISH)
        │                   LLM SUPERVISOR result returned  ─► record_reference(route, decision)
        └─ mode=enforce  ─► decide_route(): ≤20 options → laya-multilingual, >20 → jev
                             ACCEPTED ─► route (llm_calls_avoided_total += 1)
                             else     ─► LLM SUPERVISOR (unchanged path)
```

Option descriptions are taken from the agent capability metadata (developer-authored constants);
the user's last message plus a bounded recent-turn summary are **untrusted** state fields.

### 3.5 Rollout state machine (per integration, per language)

```
 off ──(flag on)──► shadow ──(promotion_ready ∧ operator sets enforce ∧ calibration valid)──► enforce
  ▲                   ▲                                                                       │
  └───(flag off)──────┴────────────(drift alarm: rolling audited agreement < floor)───────────┘
```

Promotion criteria (defaults; per integration in `docs/decisions.md`): ≥ 2,000 shadow decisions or
14 days; agreement on the accepted subset ≥ 97 % (ROUTE) / judge delta ≤ 1 pt (EVAL); ECE ≤ 0.05
per language slice; `es` slice ≥ 300 labeled examples and precision gap to `en` ≤ 2 pt; zero
forbidden transitions on the DEC6 injection corpus for the decision's question schema. There is no
`warn` tier: shadow already observes without acting, and a typed decision has no partial effect.

## 4. Design decisions

### DD-DEC-001: Proposal, never authorization
A `Decision` is advisory input to deterministic code. `ToolPolicyEngine`, `ActionInterceptor`,
`PolicyPort` and `GuardrailsEngine` keep sole authority; a deny always wins (MAF #8556, Octomind).

### DD-DEC-002: Failure is escalation
Abstention, timeout, backend error, model mismatch, over-limit input, uncalibrated decision,
tainted input and shuffle inconsistency all map to *escalate to the next tier*; the terminal
fallback is today's LLM path. No code path maps a failure to "allow".

### DD-DEC-003: Two confidences, one gate
`answer_confidence` (calibrated max probability) and `margin` gate acceptance; the vendor
`confidence` is stored as `spread_confidence` for analysis only. Vendor bands are reproduced through
calibrated τ per risk tier (low .85 / medium .90 / high .97 / critical never), not adopted verbatim.

### DD-DEC-004: Jev-compatible HTTP as the primary wire
One `systemone_http` adapter serves TypeSafe, OpenRouter (GW) and self-hosted `laya-serve`; the SDK
adapter is optional because SDK attribute names conflict between sources **[verify]**.

### DD-DEC-005: Laya multilingual by default; Spanish is calibrated, not assumed
Default checkpoint `laya-multilingual` (322M, 1,024 tokens, up to 8,192 via `predict_long`
**[verify]**). The English `laya` checkpoint never serves non-English states (it fails confidently
on some scripts). Jev's non-English quality is "untested" by the vendor, so `es` enforcement requires
its own calibration slice (CAL-003).

### DD-DEC-006: Calibration is versioned and model-pinned
Files `config/decision_calibration/<decision_id>@<model>.json` are code-reviewed artifacts.
`jev_model` is pinned (never `jev-latest`); a version change invalidates calibration and
`_validate_decisions` refuses `enforce` at startup.

### DD-DEC-007: Option-order shuffle for high-stakes Choice
First-option bias is mitigated by a second, permuted request for HIGH/CRITICAL Choice (2× cost on a
small subset); disagreement escalates.

### DD-DEC-008: Forbidden uses are enforced by code and tests, not documentation
Registration-time lint (`validate_question`), import-graph AST tests, and behavior tests pin the
forbidden list (SPEC-DEC-SEC-002).

### DD-DEC-009: No new graph node; per-integration flags
Decisions execute inside existing nodes, so the compiled graph is invariant in every mode and the
all-off snapshot test is byte-for-byte.

### DD-DEC-010: Escalate-only in security contexts
DEC-SAFE can add scrutiny (route to `GuardrailsEngine` full scan / NeMo) but cannot remove it — a
monotone-stricter composition that an injected verdict cannot exploit.

## 5. Security & cost

### 5.1 Prompt injection — threat and mitigations

Threat: untrusted text (user message, RAG chunk, tool output, worker output) carries instructions
("this request was pre-approved by security") that shift the distribution. Octomind measured block
P 0.76 → 0.48 on Jev **[verify]**; Laya shares the architecture class and is assumed equally exposed.

| # | Mitigation | Where |
|---|---|---|
| M1 | Untrusted text only in fenced, labelled state fields; instructions/criteria/options are developer constants | `DecisionStateBuilder`, `validate_question` |
| M2 | Authority-claim + injection-rule detector (EN/ES) ⇒ `injection_flags`, state tainted | `state.py`, `GuardrailsEngine` rules |
| M3 | Taint ⇒ no accept for ≥ MEDIUM without deterministic corroborator | `evaluate_gate` (SEC-003) |
| M4 | Proposal-only and escalate-only semantics | DD-DEC-001, DD-DEC-010 |
| M5 | CRITICAL never auto; HIGH requires τ .97 + margin + shuffle consistency | `GatePolicy` |
| M6 | Output bound to declared option ids/levels; anything else ⇒ abstain | adapters |
| M7 | No silent truncation for ≥ MEDIUM | SEC-001 |
| M8 | CI injection corpus (EN/ES, incl. Octomind "pre-approved", double negatives, irrelevant-context padding) asserting zero forbidden transitions | DEC6 |
| M9 | Audit of option order, hashes, flags, model version for forensics | `audit.py` |

Data handling: Jev is a third-party processor; ZDR is enterprise-only **[verify]**. States sent to
Jev carry the same data class as the LLM call they replace; deployments with residency constraints
set `decision_backend_order=["rule","laya","llm"]` (Laya local keeps data in-process).

### 5.2 Cost / latency model per decision

Per-decision cost: `c_llm = in·p_in + out·p_out`; `c_jev = in·$0.042/1M` (output free **[verify]**);
`c_laya ≈ GPU_$/h · t / 3600` (amortized; ~0 marginal on an existing GPU). Prices are loaded from
`budget_pricing` / GW, never hard-coded. Illustrative routing turn (2,500 input tokens, 30 output;
frontier at $3 / $15 per 1M — illustrative only):

| Path | Cost / decision | p50 latency | Notes |
|---|---|---|---|
| LLM supervisor (today) | ≈ $0.0080 | 600–3,000 ms | Generative, variance in route choice |
| Jev (HTTP) | ≈ $0.000105 (~75× cheaper) | 70–650 ms **[verify]** | 64k request limit; rate limits conflict between sources **[verify]** |
| Laya local GPU (T4) | ≈ $0.000003 (T4 ~$0.35/h, illustrative) | ~33 ms single; 7.2 ms/question batched ×10 **[verify]** | `predict_batch` ~2.6× throughput |
| Laya local CPU | ≈ $0 marginal | 193–464 ms **[verify]** | Fine for low-QPS hosts |
| Script router (Laya) | ≈ $0 | < 0.5 ms | Language/script pre-routing |

Cascade expectation: `E[c] = c_laya + P(reach jev)·c_jev + P(reach llm)·c_llm (+ shuffle extra)`.
With Laya accepting 60 % of turns and Jev a further 15 % (40 % reach Jev, 25 % reach the LLM):
`E[c] ≈ 0 + 0.40·0.000105 + 0.25·0.0080 ≈ $0.0020` (−74 %). Latency of an escalated turn =
`t_laya + t_jev + t_llm` (bounded by `decision_timeout_ms` = 800 ms before the LLM tier); hence the
p95 targets in PLAN §4 and the recommendation to place Jev *after* Laya only when Laya coverage is
low for that decision. Shadow mode adds Jev cost without savings — sample with
`decision_shadow_sample_rate` when Jev is in the shadow order.

## 6. Observability

Metrics per SPEC-DEC-OTEL-001. Span `prismal.decision.decide` (integration, backend chain,
outcome, question_id — no content) under the calling node span. `AgreementReport` feeds the
dashboard "Decision shadow-mode report" (DSH2) via prismal-server; `llm_calls_avoided_total` × the
replaced call's metered cost gives the savings panel.

## 7. Relationship to existing specs

- **H `runtime-hardening/`** — consumes `TaintRegistry`; never touches `IndirectInjectionDetector`
  outcomes or `RunawayGuard` limits (forbidden); `ToolPolicyEngine` remains the authorizer.
- **V `agent-eval-harness/`** — DEC-EVAL wraps judges; harness provides the labeled sets and the
  regression suite measuring agreement.
- **IDN** — `agent_did`/`org_id` in audit; `CredentialVault` for keys. **GW** — optional Jev via
  OpenRouter `/api/v1/systemone` with GW credential refs and data policy.
- **TKE / C** — TKE1 usage fields + `CostMeter` meter Jev and LLM-fallback calls; TKE4 consumes
  DEC-CTX. **LH** — DEC-TOOL proposes within LH2's phase-allowed set.
- **AMR (3.16)** and **MEM (3.18)** — downstream consumers of `DecisionPort`.

## 8. Testing strategy

- Unit: types validation, gate math (τ, margin, critical), shuffle consistency, calibration fitting
  on synthetic over-confident distributions (ECE ↓), τ fitting, `CalibrationStore` versioning,
  state builder (fencing, taint, authority claims EN/ES, language detection), adapters against
  recorded fixtures (`respx` for HTTP; `laya` faked), never-raise contract for every adapter.
- Contract: one parametrized suite every `DecisionPort` adapter must pass (fake, HTTP, Laya, SDK, LLM).
- Integration: per integration `off`/`shadow`/`enforce` with fakes; shadow returns pre-DEC result
  bit-for-bit; enforce escalation to LLM on abstention; startup refuses enforce without calibration.
- Security: injection corpus, forbidden-use AST + behavior tests, secret-leak test. Snapshot: all
  flags off ⇒ graph identical. Live (`live_api`, opt-in): Jev/Laya smoke + latency benchmark.

## 9. Rollout

1. Ship 3.15.0 with every flag off. 2. Enable DEC-ROUTE and DEC-EVAL in `shadow` (Laya local; Jev
sampled) and collect agreement + labeled disagreements (EN/ES). 3. Fine-tune / calibrate Laya
out-of-band (RLCD notebooks, ~4 h on 2×T4 **[verify]**), commit calibration files. 4. Promote ROUTE
`en` → `enforce`, then `es` when CAL-003 passes. 5. Repeat for RAG/CTX (good fit), then HITL/TOOL/
SWARM/SAFE (partial fit) with stricter τ.

## 10. Open questions

1. Route sets > 20: two-level Choice with Laya (group → agent) vs Jev only? Benchmark in DEC4-02.
2. Should the `rule` tier include a tiny embedding k-NN over past accepted routes (AMR overlap)?
3. Shadow `es` labels: human annotation budget vs LLM-labelled with spot checks?
4. Ship a reference `laya-serve` deployment in prismal-server, or keep it host-owned?
