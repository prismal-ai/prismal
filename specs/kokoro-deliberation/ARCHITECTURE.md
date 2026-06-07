# Prismal Kokoro Deliberation — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/kokoro-deliberation/PLAN.md` |
| **SPEC** | `specs/kokoro-deliberation/SPEC.md` |
| **TASKS** | `specs/kokoro-deliberation/TASKS.md` |

---

## 1. Context

Prismal already has the building blocks for multi-voice reasoning: `debate.py`
(N-agent rounds + `pairwise_jaccard` agreement), the `debate_consensus` subgraph,
and a three-tier `skills/` system that injects behavior from Markdown. Kokoro
composes these into a **persona-driven deliberation-and-decision** component:
three Markdown-authored personalities (souls) argue toward agreement, and a single
judge ("the whole") renders and optionally executes the decision.

The naming and structure follow the *kokoro* (心) concept — heart, mind, and
spirit as one — and the canonical three-part structure of the AI **Kokoro** in
*Terminator Zero* (Spirit / Mind / Heart deliberating before acting with free
will). See `PLAN.md §1`.

## 2. Technical Objectives

1. Add a `souls/` tier mirroring `skills/`, with Markdown-injected personas.
2. Two new agent types — `SoulAgent` and `KokoroJudgeAgent` — built on
   callable-injection so they test without an LLM.
3. A LangGraph subgraph that reuses `debate` primitives for deliberation.
4. Opt-in supervisor wiring with zero behavior change when disabled.
5. Security-by-construction: soul text isolated; judge actions gated and audited.

## 3. Proposed Architecture

### 3.1 High-Level Diagram — New Modules

```
prismal/
├── souls/                         ← NEW tier (mirrors skills/)
│   ├── base.py                    ← SoulMetadata, Soul, parse_soul_md, _soul_md_body
│   ├── manager.py                 ← SoulsManager (list/load/load_triad/validate)
│   ├── available/                 ← committed source souls
│   │   ├── spirit/SOUL.md         ← 魂 tamashii — values/vision
│   │   ├── mind/SOUL.md           ← 知 chi — logic/analysis
│   │   └── heart/SOUL.md          ← 情 jō — empathy/human impact
│   ├── active/                    ← runtime-enabled (gitignored)
│   └── custom/                    ← AI-generated (gitignored)
├── agents/
│   ├── kokoro/                    ← NEW agent package
│   │   ├── soul_agent.py          ← SoulAgent (persona position generator)
│   │   ├── deliberation.py        ← deliberate() — bounded multi-soul rounds
│   │   └── judge.py               ← KokoroJudgeAgent (Verdict + KokoroAction)
│   └── subgraphs/
│       └── kokoro/                ← NEW subgraph
│           ├── builder.py         ← build_kokoro_subgraph() / register_kokoro()
│           ├── load_souls_node.py
│           ├── deliberate_node.py
│           ├── judge_node.py
│           ├── act_node.py
│           └── output_node.py
```

Reused (unchanged): `agents/patterns/debate.py` (`DebatePosition`,
`pairwise_jaccard`), `security/secure_prompt.py` (`SecurePromptBuilder`),
`security/action_interceptor.py`, `security/audit.py`, `agents/subgraphs/registry.py`,
`agents/intent_router.py`, `providers/` (`ProviderRegistry`).

### 3.2 Subgraph Topology

```
        ┌──────────────┐
 START →│ load_souls   │  SoulsManager.load_triad([spirit, mind, heart])
        └──────┬───────┘
               ▼
        ┌──────────────┐   round 1: 3 independent positions (concurrent)
        │ deliberate   │   round r>1: each soul revises vs. others
        └──────┬───────┘   stop when agreement_score ≥ threshold or max_rounds
               ▼
        ┌──────────────┐
        │ judge        │   Verdict(decision, rationale, lens_summaries, dissent)
        └──────┬───────┘
               ▼
        ┌──────────────┐   if kokoro_execute_actions and verdict.action:
        │ act          │     ActionInterceptor.check → tool_executor → audit
        └──────┬───────┘   else: pass-through
               ▼
        ┌──────────────┐
        │ output       │  append assistant message (decision + rationale [+ result])
        └──────┬───────┘
               ▼
              END
```

### 3.3 Data Flow

1. `load_souls` reads three `SOUL.md` files, validates and parses them, builds
   three `SoulAgent`s, and stores handles under `state["metadata"]["kokoro"]`.
2. `deliberate` calls `SoulAgent.position()` per soul, concurrently per round; the
   agreement score is `pairwise_jaccard(final_positions)`. Early stop on threshold.
3. `judge` summarises the deliberation into a `Verdict` via `judge_fn`.
4. `act` (action mode only) checks and executes `verdict.action`, recording audit.
5. `output` renders the final assistant message.

All Kokoro state is namespaced under `state["metadata"]["kokoro"]` to isolate it
from `AgentState` (same pattern as the multimodal layer's `metadata["mm"]`).

## 4. Design Decisions

### DD-KOK-001: Souls as a Markdown tier mirroring `skills/`

Souls reuse the proven three-tier layout (`available`/`active`/`custom`) and the
`SKILL.md` frontmatter+body parsing approach. This gives versioning, discovery,
and authoring-without-code for free, and keeps the mental model consistent.
**Alternative rejected:** loose `config/souls/*.md` — simpler but no tiering,
no per-soul packaging, diverges from `skills/`.

### DD-KOK-002: Reuse `debate` primitives, do not fork

`deliberate()` produces `DebatePosition`s and scores agreement with
`pairwise_jaccard` — the same value objects and metric as `debate.py`. This avoids
a second, drifting debate implementation. **Alternative rejected:** a bespoke
round engine.

### DD-KOK-003: Judge separate from the debaters

The judge is a distinct agent ("the whole") that never debates; it only weighs and
decides. This matches the *Terminator Zero* framing (the three parts inform Kokoro;
Kokoro decides) and gives a single accountable authority for the action.

### DD-KOK-004: Callable injection everywhere

`SoulAgent(generate_fn=...)`, `deliberate(agreement_fn=...)`,
`KokoroJudgeAgent(judge_fn=..., tool_executor=...)`. Defaults lazily wire
`ProviderRegistry().get_llm()`. Tests run with deterministic fakes and **no**
provider import. Consistent with the advanced-architectures pattern.

### DD-KOK-005: Soul text is user-controlled content

A `SOUL.md` body is authored by users and may contain adversarial instructions.
It MUST reach a model only via `SecurePromptBuilder` (canary-token isolation) and
be length-capped by `InputSanitizer` (`soul_max_body_chars`). It is **never**
f-stringed into a prompt. This is the same rule the framework applies to STT
transcripts and OCR text.

### DD-KOK-006: Judge actions gated and audited

Execution is off by default (`kokoro_execute_actions=False`). When on, the action
passes `ActionInterceptor.check()` and the guardrails gateway before
`tool_executor` runs; `AuditLogger` records the verdict and action hash-first
(never the full soul body). A denied action returns a `Verdict` with
`action.blocked_reason` set — no exception, graceful degradation.

### DD-KOK-007: Opt-in subgraph + supervisor route

`register_kokoro()` is idempotent (mirrors `register_debate_consensus`). The
supervisor route and intent match are gated on `settings.kokoro_enabled`; with the
flag off the compiled graph is byte-for-byte unchanged (snapshot test).

### DD-KOK-008: Bounded deliberation with early stop

A hard `kokoro_max_rounds` cap guarantees termination; an `agreement_threshold`
allows early convergence. If souls never converge, the judge still decides and
records dissent — deliberation informs but never blocks the decision.

## 5. Code Structure & Patterns

- `from __future__ import annotations` in every module; heavy/optional imports lazy.
- Value objects: `SoulMetadata` (Pydantic), `Soul` / `DeliberationResult` /
  `Verdict` / `KokoroAction` (frozen dataclasses).
- Node factories (`make_*_node`) return `async (state) -> state_update`, matching
  the existing subgraph node style; the builder assembles a `SubgraphDefinition`.
- Errors extend `PrismalError` via the `KokoroError` hierarchy (SPEC-KOK-ERR-001).

### Error Handling

| Failure | Behavior |
|---|---|
| Missing/invalid `SOUL.md` | `SoulValidationError` at load (fail fast, before any LLM call) |
| Resolved souls ≠ 3 | `KokoroConfigError` |
| `generate_fn` / `judge_fn` raises | wrapped as `DeliberationError` / `JudgeError`; node returns an error state, no crash |
| Action denied by interceptor | `Verdict.action.blocked_reason` set; `executed=False` |

## 6. Security

### 6.1 Attack Surface — New Layer

| Vector | Control |
|---|---|
| Prompt injection via `SOUL.md` body | `SecurePromptBuilder` + `InputSanitizer` length cap; never raw-concatenated |
| Oversized / malicious soul files | `soul_max_body_chars`; schema validation; `filesystem_guard` path confinement |
| Unsafe judge action | `ActionInterceptor.check()` + guardrails gateway; off by default |
| Sensitive content in logs | `AuditLogger` hash-first; soul body never logged |

### 6.2 Cross-Cutting Rules

- No provider SDK imports outside `prismal/providers/`.
- Soul loading is path-confined under `settings.souls_dir`.
- The judge is the only component allowed to request an action, and only through
  `ActionInterceptor`.

## 7. Observability

### 7.1 OTel Spans per Stage

`kokoro.load_souls`, `kokoro.deliberate` (attributes: `rounds`, `agreement_score`,
`converged`), `kokoro.judge`, `kokoro.act` (attributes: `tool_name`, `executed`,
`blocked`).

### 7.2 Key Metrics

```
# Counters / histograms
prismal.kokoro_runs_total
prismal.kokoro_rounds            (histogram)
prismal.kokoro_agreement_score   (histogram)
prismal.kokoro_actions_total{executed|blocked}
```

## 8. Integration With the Main Graph

- `intent_router.match_intent()` → `kokoro` for deliberation phrases (gated on flag).
- `get_async_compiled_graph()` wires the `kokoro` route only when
  `settings.kokoro_enabled` is `True`; `effective_valid_routes` and
  `build_system_prompt` gate on the same flag.
- `DEFAULT_CAPABILITY_MAP` gains a `kokoro` entry so the judge's optional tool set
  is resolved through the injected `ToolProviderPort` (Phase Y) — Kokoro itself
  does **not** import `prismal.mcp` / `prismal.skills`.

## 9. Testing Strategy (summary; detail in `TASKS.md`)

- **Souls**: parse/validate `SOUL.md`, tier discovery, `load_triad` arity errors.
- **SoulAgent**: position generation routes through `SecurePromptBuilder` (spy);
  injected `generate_fn` returns deterministic text.
- **Deliberation**: early-stop on threshold; never exceeds `max_rounds`; arity guard.
- **Judge**: verdict has one lens summary per soul + dissent; action off vs. on;
  interceptor-deny path sets `blocked_reason`.
- **Subgraph**: end-to-end with fakes; no provider import (AST guard reused).
- **Integration**: with `kokoro_enabled=False`, compiled-graph snapshot is unchanged.

## 10. Rollout

1. Land `souls/` + the three default souls + `SoulsManager` (no graph wiring).
2. Land `SoulAgent` + `deliberate` + `KokoroJudgeAgent` (pure, injected).
3. Land the `kokoro/` subgraph + `register_kokoro` (still off by default).
4. Wire the opt-in supervisor route + intent match behind `kokoro_enabled`.
5. Docs + examples (`examples/kokoro_deliberation.py`).
