# Prismal — Blind Review Development Pipeline

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-10 |
| **Phase** | BRP (Blind Review Pipeline) |
| **Target package version** | `3.11.0` (SemVer minor — new opt-in functionality; current `3.10.2`, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Priority** | P2 (developer-facing quality pattern; additive, opt-in) |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md`, `specs/tool-provider-injection/` (Phase Y — the `ToolProviderPort` seam this reuses), `specs/advanced-architectures/` (`dev_pipeline`, `code_review`, `mixture_of_agents`, the patterns this composes/differs from), `specs/kokoro-deliberation/` (nearest precedent for a multi-perspective judge), `specs/cost-budget-governance/` (`budget_guard_fn` this wires) |

---

## 1. Executive Summary

**Blind Review Pipeline (BRP)** is a new opt-in LangGraph subgraph that implements
a 4-agent software-development pattern requested directly by the user: a
**spec agent** turns a goal into a specification, an **implementer agent**
builds an artifact from that specification alone, and two **independent
reviewer agents** — blind to the implementer's reasoning, tool calls, and
message history, seeing only the specification and the final artifact —
critique and correct the work in parallel. A deterministic synthesis step
merges both verdicts and either approves (optionally through Human-in-the-Loop)
or routes back to the implementer with the merged corrections.

Each of the four roles may run on a **different LLM** (via the existing
`ProviderRegistry`) and resolve **different tools/skills** (via the existing
`ToolProviderPort`, Phase Y) — no new plumbing is required for either of
those two properties. The one genuinely new mechanism this phase introduces
is **blindness**: a discipline, enforced by both a static AST guard and a
runtime check, that the two reviewer nodes' prompts are built exclusively
from `state["metadata"]["blind_review"]` (spec + artifact) and never from
`state["messages"]`.

BRP is **opt-in**: gated by `settings.blind_review_pipeline_enabled`
(default `False`). When off, the compiled supervisor graph is byte-for-byte
unchanged (snapshot-tested, mirroring every other opt-in phase in this repo).

## 2. Feasibility — is this possible with the existing framework?

**Yes, entirely with existing primitives.** This was confirmed by direct
inspection of the current codebase (2026-07-10) rather than assumption:

- `agents/subgraphs/dev_pipeline/builder.py` already assembles a
  `SubgraphDefinition` with a developer/reviewer loop gated by `score_gate`
  and a post-approval `hitl_gate` (`seed_hitl_metadata` → `human_approval_node`
  → `hitl_gate`) — BRP reuses this exact wiring rather than inventing a new
  approval flow.
- `agents/subgraphs/code_review/{linter,logic_reviewer,suggester}_node.py`
  already prove that an analyzer function can be *structurally* blind: each
  `reviewer_fn`/`scanner_fn` receives only `(code, file)` — never
  `state["messages"]` and never the other analyzers' findings during its own
  run (findings are merged only afterward, at the orchestration layer). BRP's
  reviewer nodes generalize this same input contract to `(spec, artifact)`.
- `agents/patterns/mixture_of_agents.py::MixtureOfAgents` already runs N
  models in parallel and independently (`asyncio.gather`), each resolved via
  `ProviderRegistry(settings).get_llm(model_id)` — proof that "different LLM
  per role, no shared context between them" needs no new engine capability.
- `agents/extension/providers.py::CompositeToolProvider` already resolves
  tools per `agent_name` + a `capabilities` filter (`_effective_capabilities`,
  SPEC-LH-GAT-002) — proof that "own skill / own MCP per agent" is already
  the standard mechanism every agent in this repo uses, not something new.
- `agents/subgraphs/gates.py::score_gate`/`failure_gate` already read
  `state["iteration_count"]` (top-level, not nested) to bound retry loops —
  BRP reuses this field rather than introducing a parallel counter.

No new LangGraph capability is required. BRP is a **composition** of
`SubgraphFactory`/`SubgraphDefinition`, `gates.py`, `ProviderRegistry`, and
`ToolProviderPort` — the same shape as `dev_pipeline` and `code_review`
themselves.

## 3. Context and Problem

### 3.1 Current situation

Prismal ships two development-oriented subgraphs. `dev_pipeline`
(PO → Architect → Developer → UnitTest → QA → Reviewer) is **sequential and
cumulative**: the `reviewer` node runs after `qa_agent` and, through the
shared `AgentState.messages` channel, has visibility into the developer's
full trajectory. `code_review` (linter → security_scanner → logic_reviewer →
suggester → report_generator) is also sequential, and while its individual
analyzer functions are already input-blind (`(code, file)` only), the
pipeline itself runs the analyzers one after another rather than as an
independent parallel panel, and never varies the LLM per analyzer today.
Neither pipeline expresses "two reviewers who must not see how the artifact
was produced, only what was produced."

### 3.2 Problem

A single reviewer (or a chain of reviewers that share context) can anchor on
the same blind spots as the implementer, or on each other's early framing
once a pipeline shares state cumulatively. There is no first-class pattern in
this repo for **independent, context-isolated, heterogeneous-model review**
of a produced artifact — the exact pattern the user described in the prior
analysis turn (spec agent → implementer agent → two blind reviewers, each
with its own skill/MCP/LLM).

### 3.3 Opportunity

Ship `blind_review_pipeline` as a new subgraph, following the exact
`build_<name>_subgraph()` / `register_<name>()` convention already used by
`code_review`/`dev_pipeline`/`skynet`/`kokoro`, opt-in via a settings flag,
reusing every existing seam (gates, tool provider, provider registry) and
adding exactly one new invariant (blindness) with both a static and a
runtime guard.

## 4. Target Users

### Persona 1: Applied AI Engineer building on Prismal

Wants a reusable "spec → implement → blind panel review → correct" component
without hand-wiring context isolation or juggling four separate LLM clients.

### Persona 2: Platform / Security Reviewer

Needs proof — not just a claim — that the two reviewer agents cannot see the
implementer's reasoning or tool-call trace. Wants a test that fails CI if
that invariant is ever violated by a future edit.

### Persona 3: Workflow Author

Wants to configure which model and which tool capabilities each of the four
roles gets, and what happens when the two reviewers disagree, without
touching graph internals.

## 5. Objectives and Success Metrics

### 5.1 Business Objectives

- A reusable, bounded "spec → implement → blind dual review → correct" subgraph.
- Zero behavior change when disabled.
- Reuse gates/tool-provider/provider-registry primitives rather than
  re-implementing them.

### 5.2 User Objectives

| Objective | Success Metric |
|---|---|
| Produce a spec before implementation | `spec_agent_node` writes a `spec_artifact` string consumed (and only consumed) by the implementer |
| Implement from spec alone | `implementer_agent_node` never reads `state["messages"]` beyond what seeded the spec step |
| Review blind, in parallel, independently | Both reviewer nodes run per turn; neither reads `state["messages"]`; AST guard proves it statically |
| Heterogeneous review | The two reviewers can be configured with different `model_id`s and different `capabilities` filters |
| Deterministic tie-break | `synthesize_verdicts()` merges two `CodeReviewReport`s into one score with no LLM call |
| Bounded correction loop | `score_gate` + `iteration_count` cap retries at `blind_review_max_iterations` |
| Optional human sign-off | Reuses `seed_hitl_metadata`/`human_approval_node`/`hitl_gate`, bypassable via `settings.hitl_enabled` |

## 6. Scope

### 6.1 In Scope (Phase BRP)

- `spec_agent_node`, `implementer_agent_node` — sequential, reusing
  `SecurePromptBuilder` + `ActionInterceptor` conventions already used by
  `dev_pipeline`'s `po_agent`/`architect_agent`/`developer_agent`.
- `make_reviewer_node(role, ...)` — a factory producing the two blind
  reviewer nodes (`reviewer_a`, `reviewer_b`), each independently configured
  (model, capabilities, prompt framing).
- Static (non-`Send`) two-way fan-out from `implementer_agent` to both
  reviewers, converging at a `synthesis` node (panel size is fixed at 2 by
  design — see DD-BRP-005 in `ARCHITECTURE.md`).
- `synthesize_verdicts()` — deterministic merge of the two `CodeReviewReport`s
  (reused from `agents/subgraphs/code_review/types.py`) into one score.
- `score_gate`-driven correction loop back to `implementer_agent`, bounded by
  `blind_review_max_iterations`.
- Reused HITL approval flow (`seed_hitl_metadata` → `human_approval_node` →
  `hitl_gate`) before `END`.
- `build_blind_review_pipeline_subgraph()` + idempotent
  `register_blind_review_pipeline()`.
- Opt-in supervisor route + intent routing, gated by
  `blind_review_pipeline_enabled`.
- A static AST guard test asserting the reviewer node modules never
  reference `state["messages"]`, plus a runtime `BlindnessGuard` check.
- Settings, exceptions, audit, observability, unit tests with injected fakes
  (no LLM backend required), written test-first (see `TASKS.md`).

### 6.2 Out of Scope (Excluded from Phase BRP)

- An LLM arbiter / 5th judge agent for reviewer disagreement — Phase BRP
  uses a deterministic merge only, to stay faithful to "4 agents" as
  specified by the user. An optional judge is a BRP+ follow-up (§6.4).
- Dynamic panel size (N reviewers instead of exactly 2) — out of scope; that
  is what `skynet-swarm` already provides for variable-N fan-out.
- Passing raw reviewer prose back to the implementer on retry — Phase BRP
  passes the synthesized, structured issue list only (mirrors how
  `dev_pipeline`'s `_TEST_GATE`/`_REVIEWER_GATE` route on structured fields,
  not free text).
- Remote/cross-process reviewers (A2A) — could reuse `A2AAgentNode` later;
  not in Phase BRP.
- New LLM-provider integrations — this phase only *selects* models already
  reachable through `ProviderRegistry`.

### 6.3 Future Considerations (Phase BRP+)

- Optional LLM arbiter node for persistent reviewer disagreement.
- N-reviewer panels (would likely compose `skynet-swarm`'s dynamic `Send`
  fan-out instead of the static two-way edges used here).
- Remote reviewers via `specs/a2a-interop/`.
- Feeding `blind_review` outcomes into `specs/agent-eval-harness/` as a
  regression signal.

## 7. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-BRP-01 | `spec_agent_node` produces `spec_artifact` from the incoming goal | MUST |
| RF-BRP-02 | `implementer_agent_node` consumes only `spec_artifact` (+ prior correction notes on retry), never raw `state["messages"]` | MUST |
| RF-BRP-03 | `reviewer_a`/`reviewer_b` run after `implementer_agent` and read only `state["metadata"]["blind_review"]["spec_artifact"]` + `["implementation_artifact"]` | MUST |
| RF-BRP-04 | Reviewer nodes MUST NEVER read `state["messages"]` — enforced by an AST guard test and a runtime `BlindnessGuard` | MUST |
| RF-BRP-05 | Each of the 4 roles resolves its own LLM via `ProviderRegistry(settings).get_llm(model_id)`, independently configurable | MUST |
| RF-BRP-06 | Each of the 4 roles resolves tools via the injected `ToolProviderPort`, filtered by a per-role `capabilities` list | MUST |
| RF-BRP-07 | Reviewer verdicts are typed `CodeReviewReport` (reused from `code_review/types.py`, not duplicated) | MUST |
| RF-BRP-08 | `synthesize_verdicts()` deterministically merges both verdicts into one score, no LLM call | MUST |
| RF-BRP-09 | `score_gate` routes to approval when `synthesis.score >= blind_review_approval_threshold`, else back to `implementer_agent` | MUST |
| RF-BRP-10 | Correction loop bounded by `blind_review_max_iterations`, reusing the existing top-level `state["iteration_count"]` field | MUST |
| RF-BRP-11 | Optional HITL approval before `END`, reusing `seed_hitl_metadata`/`human_approval_node`/`hitl_gate`, bypassable via `settings.hitl_enabled` | MUST |
| RF-BRP-12 | Exposed as `build_blind_review_pipeline_subgraph()` + `register_blind_review_pipeline()` | MUST |
| RF-BRP-13 | Opt-in supervisor route + intent routing gated by `blind_review_pipeline_enabled`; graph snapshot unchanged when disabled | MUST |
| RF-BRP-14 | All BRP state lives under `state["metadata"]["blind_review"]` | MUST |
| RF-BRP-15 | Each stage (spec, implementation, each reviewer, synthesis, HITL decision) is audited hash-first via `AuditLogger` | SHOULD |
| RF-BRP-16 | `budget_guard_fn` is honored at every LLM call site (implementer + 2 reviewers), mirroring `debate_round`/`MixtureOfAgents`/`reflection_loop` | SHOULD |

## 8. Non-Functional Requirements

### Security

- All four roles build prompts via `SecurePromptBuilder`; spec/artifact text
  is treated as user-derived content, never f-stringed into a template.
- `implementer_agent_node` calls `ActionInterceptor.check()` before any file
  write or code execution, exactly as `developer_agent_node` does today.
- Blindness (RF-BRP-04) is defense-in-depth: a static AST test at CI time
  plus a runtime `BlindnessGuard` assertion at execution time.
- No `prismal.mcp` / `prismal.skills` import inside the new package — tools
  reach every node only through the injected `ToolProviderPort` (Fase Y
  invariant, reusing the existing `test_no_mcp_skills_imports.py` AST guard
  pattern, extended to cover `agents/subgraphs/blind_review_pipeline/`).

### Performance / Cost

- Up to 4 LLM calls per iteration (implementer + 2 reviewers, synthesis is
  non-LLM) times up to `blind_review_max_iterations` retries — bounded and
  metered via the optional `budget_guard_fn` (Phase C, Cost & Budget
  Governance), same signature already used by `debate_round`/`MixtureOfAgents`.
- The two reviewers run independently and can run concurrently (no data
  dependency between them) — see DD-BRP-005 for the fan-out mechanics.

### Observability

- OTel spans: `blind_review.spec`, `blind_review.implement`,
  `blind_review.review_a`, `blind_review.review_b`, `blind_review.synthesis`.
- Counters for iterations used, synthesis score, and reviewer disagreement
  rate (fraction of runs where the two verdicts' approval differs).

### Maintainability

- No provider SDK imports outside `prismal/providers/`.
- Callable injection (`spec_fn`, `implementer_fn`, `reviewer_a_fn`,
  `reviewer_b_fn`, `synthesize_fn`) so the whole loop is unit-testable
  without an LLM backend, mirroring `SkynetSupervisor`'s `plan_fn`/`evaluate_fn`
  and `build_code_review_subgraph`'s `linter_fn`/`scanner_fn`/`reviewer_fn`.
- State namespaced under `metadata["blind_review"]`.

## 9. Constraints and Dependencies

- Python 3.13+, LangGraph `StateGraph[AgentState]`, async via
  `get_async_compiled_graph()`.
- Reuse (do not fork): `agents/subgraphs/factory.py::SubgraphFactory`,
  `agents/subgraphs/gates.py` (`score_gate`, `seed_hitl_metadata`,
  `human_approval_node`, `hitl_gate`), `agents/subgraphs/code_review/types.py`
  (`CodeIssue`, `CodeReviewReport`), `agents/extension/providers.py`
  (`ToolProviderPort` / `CompositeToolProvider`), `providers/registry.py`
  (`ProviderRegistry`), `security/prompt_builder.py::SecurePromptBuilder`,
  `security/action_interceptor.py::ActionInterceptor`, `security/audit.py`
  (`AuditLogger`), `agents/subgraphs/registry.py::SubgraphRegistry`,
  `agents/intent_router.py`.
- Depends on Phase Y (`tool-provider-injection`) — **already shipped**; no
  other pending-phase dependency (Loop Hardening's phase-capability-map
  mechanism, SPEC-LH-GAT-002, is already present in `CompositeToolProvider`
  and may optionally be used to scope reviewer tools by pipeline phase, but
  is not a hard prerequisite).

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Cost/latency multiplication (4 calls × N iterations, 2 possibly-heavy reviewer models) | Medium | `blind_review_max_iterations` cap; optional `budget_guard_fn` wiring (RF-BRP-16) |
| Reviewers configured to the same model/provider by mistake, defeating the purpose | Medium | Config-time `WARN` (not hard fail) when `blind_review_reviewer_a_model == blind_review_reviewer_b_model` |
| Non-terminating correction loop | High | `score_gate` + `iteration_count` cap (RF-BRP-10), same mechanism as `dev_pipeline`'s `_REVIEWER_GATE`/`_TEST_GATE` |
| Blindness silently broken by a future edit (someone adds `state["messages"]` to a reviewer prompt) | High | AST guard test (CI-time) + `BlindnessGuard` runtime assertion (execution-time) — defense in depth, not a single point of failure |
| Reviewer disagreement has no defined resolution | Medium | Deterministic `synthesize_verdicts()` merge in Phase BRP (min score, union of issues); LLM arbiter deferred to BRP+ |
| Behavior leak when disabled | Medium | Gate every wiring point on `blind_review_pipeline_enabled`; snapshot test (mirrors every other opt-in phase) |
| Implementer overfits to reviewer feedback style rather than fixing root cause | Low | Pass structured `CodeIssue` list back, not raw reviewer prose (§6.2) |

## 11. Open Questions

- Should persistent reviewer disagreement (e.g. 2 consecutive iterations with
  no score convergence) escalate to an LLM arbiter automatically, or always
  stay deterministic in Phase BRP? **Decision for Phase BRP: stays
  deterministic; escalation is a BRP+ follow-up**, to keep the four-agent
  shape the user asked for.
- Should `reviewer_a`/`reviewer_b` be allowed to request more code context
  (e.g. call a "read more of the file" tool) while still being blind to the
  implementer's *reasoning*? **Decision: yes** — blindness (RF-BRP-04) is
  about `state["messages"]`/tool-call history of the implementer, not about
  read-only access to the artifact's surrounding repository context via the
  reviewer's own `ToolProviderPort`-resolved tools.
- Should BRP become one of the fixed 26+ specialist nodes, or stay a
  supervisor-routed subgraph (like `kokoro`/`skynet`)? **Decision: the
  latter**, for consistency with the two nearest precedents.
