# Prismal — Kokoro Deliberation Agents

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Executive Summary

**Kokoro** (心 — "heart-mind-spirit") is a new opt-in deliberation layer for Prismal.
It introduces **two new agent types**:

1. **`SoulAgent`** — a reusable persona sub-agent whose personality, role, and
   temperament are injected from a Markdown file (a "soul"), mirroring how
   `MarkdownSkill` loads a `SKILL.md`.
2. **`KokoroJudgeAgent`** — the orchestrator and final judge ("the whole, more
   than the sum of its parts"). It convenes three `SoulAgent`s with distinct
   personalities, has them deliberate until they seek agreement, and then renders
   the final verdict and — when enabled — executes the resulting action through
   the existing security gates.

The design is grounded in two references the term evokes: the Japanese concept of
*kokoro* (the unity of thought, emotion, and will), and the AI **Kokoro** of
*Terminator Zero*, which is canonically structured in three interconnected parts —
**Spirit, Mind, Heart** — that weigh a decision through spiritual, intellectual,
and emotional lenses before acting. Prismal adopts the same triad as its three
default souls.

Kokoro is implemented as a LangGraph subgraph and reuses Prismal's existing
multi-agent debate primitives (`agents/patterns/debate.py`,
`agents/subgraphs/debate_consensus/`). It is **opt-in**: gated by
`settings.kokoro_enabled` (default `False`); when off, the 26 existing agents
behave identically.

## 2. Context and Problem

### 2.1 Current Situation

Prismal already ships multi-agent reasoning patterns — `debate` (N-agent
multi-round debate + Jaccard agreement), `mixture_of_agents`, and the
`debate_consensus` subgraph (proponent → opponent → moderator → consensus).
These are powerful but **anonymous**: each debating agent is a role label, not a
persistent, user-authored persona, and there is no first-class "judge that owns
the final action".

### 2.2 Problem

Teams want decisions that are deliberated from **stable, distinct points of
view** (e.g. a values lens, an analytical lens, a human-impact lens) and then
resolved by a single accountable authority that can also *act* on the verdict.
Today this requires hand-wiring prompts per call, with no reusable way to author,
version, and inject a personality, and no standard place for the judge to execute
the chosen action under the security stack.

### 2.3 Opportunity

Introduce **souls** (Markdown-authored personas, versioned in the repo like
skills) and a **judge agent** that orchestrates a bounded deliberation among
three souls and owns the final decision/action. This turns "debate" into a
reusable, auditable, personality-driven decision component.

## 3. Target Users

### Persona 1: Applied AI Engineer

Wants to compose a decision step where three named viewpoints argue and a judge
decides, without re-writing prompts each time. Authors souls as Markdown and
registers the Kokoro subgraph.

### Persona 2: Product / Policy Owner

Defines the three "voices" relevant to their domain (e.g. *risk*, *growth*,
*customer*) by editing Markdown soul files — no code — and relies on the judge's
auditable rationale citing each lens.

### Persona 3: Agent Platform Operator

Enables/disables Kokoro per deployment via settings, controls whether the judge
may execute actions, and audits every verdict and action through the existing
`AuditLogger`.

## 4. Objectives and Success Metrics

### 4.1 Business Objectives

- Provide a reusable, personality-driven deliberation-and-decision component.
- Keep the base framework unchanged when the feature is off (zero regression).
- Reuse existing debate/security primitives rather than re-implementing them.

### 4.2 User Objectives

| Objective | Success Metric |
|---|---|
| Author a persona without code | A `SOUL.md` file alone yields a working `SoulAgent` |
| Deliberate from 3 distinct voices | Each persona's position is traceable to its soul |
| Reach agreement when possible | Deliberation stops early when `agreement_score ≥ threshold` |
| Accountable final decision | Judge emits a verdict citing all three lenses + dissent |
| Optionally act | Judge can execute one tool action via `ActionInterceptor` |
| Safe by construction | Soul text never f-stringed into prompts; actions gated |

## 5. Scope

### 5.1 In Scope (Phase K)

- A new `souls/` tier (`available/` committed, `active/` runtime, `custom/`
  AI-generated) mirroring `skills/`, with a `SoulMetadata` model, a `SoulLoader`
  (parse `SOUL.md` frontmatter + body), and a `SoulsManager`.
- Three default souls shipped under `souls/available/`: **spirit** (魂 *tamashii*),
  **mind** (知 *chi*), **heart** (情 *jō*) — English ids with Japanese aliases.
- `SoulAgent` — a persona sub-agent that builds a persona-conditioned position via
  `SecurePromptBuilder`, callable-injected (`generate_fn`) for tests.
- `KokoroJudgeAgent` — convenes 3 souls, runs a bounded deliberation, renders a
  verdict, and (when enabled) executes one action through `ActionInterceptor`.
- A LangGraph subgraph `kokoro/`:
  `load_souls → deliberate (spirit | mind | heart, N rounds) → judge → act | output`,
  exporting `build_kokoro_subgraph()` and idempotent `register_kokoro()`.
- Opt-in supervisor wiring: `settings.kokoro_enabled` gates a single `kokoro`
  route; `intent_router.match_intent()` returns `kokoro` for deliberation intents.
- Settings, exceptions, and packaging hooks.
- Unit tests with injected fakes (no LLM backend required).

### 5.2 Out of Scope (Excluded)

- Replacing or modifying the existing `debate`/`debate_consensus` components.
- More than three souls per Kokoro run (the triad is fixed for Phase K; N-soul is
  a future consideration).
- A UI for authoring souls (Markdown files only).
- Long-term "memory" of past verdicts beyond the existing memory subsystem.
- Multi-judge or hierarchical Kokoro (single judge per run).

### 5.3 Future Considerations (Phase K+)

- Configurable N souls and weighted judge voting.
- Soul marketplaces / plugin entry points (`prismal.souls`) reusing the Phase X
  extension surface.
- Per-soul tool subsets so a persona can run its own tools during deliberation.

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-KOK-01 | Parse a `SOUL.md` (YAML frontmatter + Markdown body) into `SoulMetadata` + persona body | MUST |
| RF-KOK-02 | `SoulsManager` discovers/loads souls from the `souls/` tiers | MUST |
| RF-KOK-03 | Ship three default souls: spirit, mind, heart (with JP aliases) | MUST |
| RF-KOK-04 | `SoulAgent` produces a position conditioned on its soul, via `SecurePromptBuilder` | MUST |
| RF-KOK-05 | Bounded deliberation among 3 souls reusing `debate_round`/`pairwise_jaccard` | MUST |
| RF-KOK-06 | Stop early when `agreement_score ≥ kokoro_agreement_threshold` | MUST |
| RF-KOK-07 | Judge renders a verdict citing all three lenses + dissent | MUST |
| RF-KOK-08 | Judge optionally executes one action through `ActionInterceptor` (gated by `kokoro_execute_actions`) | MUST |
| RF-KOK-09 | Expose as subgraph `build_kokoro_subgraph()` + `register_kokoro()` | MUST |
| RF-KOK-10 | Opt-in supervisor route + intent routing, gated by `kokoro_enabled` | MUST |
| RF-KOK-11 | Callable injection (`generate_fn`, `judge_fn`, `tool_executor`, `agreement_fn`) for tests | MUST |
| RF-KOK-12 | All Kokoro state under `state["metadata"]["kokoro"]` | MUST |
| RF-KOK-13 | Audit every verdict and action (hash-first, never raw soul content) | SHOULD |
| RF-KOK-14 | Soul files validated (size cap, path confinement, schema) before use | SHOULD |

## 7. Non-Functional Requirements

### Security

- Soul Markdown is **user-controlled content**: it MUST be isolated with
  `SecurePromptBuilder` (canary tokens) and length-capped by `InputSanitizer`;
  never f-stringed into a prompt template.
- The judge's action MUST pass `ActionInterceptor.check()` and the guardrails
  gateway before execution; `AuditLogger` records the decision and action (hash +
  metadata, never the full soul body).
- Soul file loading MUST be path-confined (`filesystem_guard`) and size-limited.

### Performance

- Deliberation cost is bounded by `kokoro_max_rounds` (default 2) and early-stop
  on agreement; persona calls within a round run concurrently.

### Maintainability

- No provider SDK imports outside `prismal/providers/`.
- Business logic accepts callables so tests run without an LLM backend.
- Kokoro state is namespaced under `metadata["kokoro"]` to isolate it from
  `AgentState`.

### Observability

- OTel spans per stage (`kokoro.load_souls`, `kokoro.deliberate`, `kokoro.judge`,
  `kokoro.act`) and counters for rounds, agreement, and actions executed.

## 8. Constraints and Dependencies

### Technical Constraints

- Python 3.13+, LangGraph `StateGraph[AgentState]`, PEP 420 namespace package.
- Must reuse `agents/patterns/debate.py` primitives, not fork them.
- Async entry via `get_async_compiled_graph()`.

### Dependencies

- Existing: `debate`, `SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`,
  `SubgraphRegistry`, `intent_router`, `ProviderRegistry`.
- New, additive: `prismal/souls/`, `prismal/agents/kokoro/`,
  `prismal/agents/subgraphs/kokoro/`, settings + exceptions extensions.

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Prompt injection via soul Markdown | High | `SecurePromptBuilder` + `InputSanitizer` + size cap; never f-string |
| Judge executes an unsafe action | High | `ActionInterceptor.check()` + guardrails; `kokoro_execute_actions` default `False` |
| Deliberation never converges | Medium | Hard `kokoro_max_rounds` cap; judge decides regardless on dissent |
| Souls drift from `skills/` conventions | Low | Mirror the skills tier layout and metadata model deliberately |
| Feature leaks behavior when off | Medium | Gate every wiring point on `kokoro_enabled`; byte-for-byte unchanged off |

## 10. Open Questions

- Should `active/` souls be required, or can `available/` souls be used directly?
  (Phase K: load from `available/` by id; `active/` is an opt-in allow-list.)
- Default judge synthesis when souls fully disagree? (Phase K: judge picks with an
  explicit "dissent retained" note; no forced tie-break heuristic.)
