# Prismal Kokoro Deliberation — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/kokoro-deliberation/PLAN.md` |
| **SPEC** | `specs/kokoro-deliberation/SPEC.md` |
| **Architecture** | `specs/kokoro-deliberation/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Kokoro is delivered in seven phases (K1–K7), each independently testable and
landing behind `settings.kokoro_enabled` (default `False`) so `main` stays green
and the existing 26 agents are unaffected until the final wiring phase. Every
component uses callable injection, so all unit tests run without an LLM backend.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Branch `feature/new_agents` (current).
- Reuse, do not modify: `agents/patterns/debate.py`, `security/secure_prompt.py`,
  `security/action_interceptor.py`, `security/audit.py`,
  `agents/subgraphs/registry.py`, `agents/intent_router.py`, `providers/`.
- Confirm the `skills/base.py` parsing helpers can be shared/adapted for souls.

## 3. Implementation Phases

### PHASE K1 — Souls tier (`prismal/souls/`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K1-01 | `souls/base.py`: `SoulMetadata` (Pydantic) + `Soul` (frozen dataclass) | 0.5 d | — | TODO |
| K1-02 | `parse_soul_md` / `_soul_md_body` / `_find_soul_md` (case-insensitive dir+file) | 0.5 d | K1-01 | TODO |
| K1-03 | `load_soul()` with validation (size cap, schema, path confinement) | 0.5 d | K1-02 | TODO |
| K1-04 | `souls/manager.py`: `SoulsManager` (`list_souls`/`load`/`load_triad`) | 0.5 d | K1-03 | TODO |
| K1-05 | `souls/available/{spirit,mind,heart}/SOUL.md` (EN id + JP alias) | 0.5 d | K1-01 | TODO |
| K1-06 | `.gitignore` `souls/active/` and `souls/custom/` | 0.1 d | — | TODO |

**Done when:** a soul loads from a `SOUL.md` alone; `load_triad` returns exactly
three souls or raises `KokoroConfigError`; invalid/oversized souls raise
`SoulValidationError`.

### PHASE K2 — Exceptions + Settings

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K2-01 | `core/exceptions.py`: `KokoroError` hierarchy (SPEC-KOK-ERR-001) | 0.2 d | — | TODO |
| K2-02 | `core/config.py`: `kokoro_*` + `souls_dir` + `soul_max_body_chars` settings | 0.3 d | — | TODO |
| K2-03 | Settings validation (threshold ∈ [0,1]; `kokoro_souls` length 3) | 0.2 d | K2-02 | TODO |

**Done when:** settings parse from `PRISMAL_*` env; defaults match SPEC-KOK-CFG-001.

### PHASE K3 — SoulAgent (`agents/kokoro/soul_agent.py`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K3-01 | `SoulAgent.__init__` with injected `generate_fn` / `prompt_builder` | 0.5 d | K1, K2 | TODO |
| K3-02 | `position()` builds a secure prompt (soul body via `SecurePromptBuilder`) | 0.5 d | K3-01 | TODO |
| K3-03 | Default `generate_fn` lazily wires `ProviderRegistry().get_llm()` | 0.3 d | K3-01 | TODO |

**Done when:** `position()` returns a `DebatePosition`; a spy proves the soul body
passed through `SecurePromptBuilder` and was never raw-concatenated.

### PHASE K4 — Deliberation (`agents/kokoro/deliberation.py`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K4-01 | `DeliberationResult` dataclass | 0.2 d | — | TODO |
| K4-02 | `deliberate()` round loop (concurrent per round; revise vs. others) | 0.7 d | K3 | TODO |
| K4-03 | Agreement via `pairwise_jaccard`; early-stop at threshold | 0.3 d | K4-02 | TODO |
| K4-04 | Arity guard (exactly 3 souls) → `KokoroConfigError` | 0.1 d | K4-02 | TODO |

**Done when:** deliberation stops at the first round ≥ threshold and never exceeds
`max_rounds`; `final_positions` has one entry per soul.

### PHASE K5 — Judge (`agents/kokoro/judge.py`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K5-01 | `Verdict` + `KokoroAction` dataclasses | 0.2 d | — | TODO |
| K5-02 | `KokoroJudgeAgent.judge()` (secure prompt → parse `Verdict`) | 0.7 d | K4 | TODO |
| K5-03 | `act()` gated by `kokoro_execute_actions`; `ActionInterceptor.check()` first | 0.5 d | K5-02 | TODO |
| K5-04 | Audit (hash-first) of verdict + action via `AuditLogger` | 0.3 d | K5-03 | TODO |

**Done when:** verdict cites all three lenses + dissent; with execution off
`tool_executor` is never called; a denied action sets `blocked_reason`.

### PHASE K6 — Subgraph (`agents/subgraphs/kokoro/`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K6-01 | Node factories: `load_souls`, `deliberate`, `judge`, `act`, `output` | 0.7 d | K5 | TODO |
| K6-02 | `builder.py`: `build_kokoro_subgraph()` → `SubgraphDefinition` | 0.4 d | K6-01 | TODO |
| K6-03 | `register_kokoro()` (idempotent, mirrors `register_debate_consensus`) | 0.2 d | K6-02 | TODO |
| K6-04 | All Kokoro state under `state["metadata"]["kokoro"]` | 0.2 d | K6-01 | TODO |

**Done when:** the subgraph runs end-to-end with injected fakes and no provider
import (AST guard `test_no_mcp_skills_imports` style reused).

### PHASE K7 — Supervisor + intent integration (the only behavior-changing phase)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K7-01 | `intent_router.match_intent()` returns `kokoro` for deliberation intents | 0.3 d | K6 | TODO |
| K7-02 | `get_async_compiled_graph()` wires `kokoro` route when `kokoro_enabled` | 0.4 d | K6 | TODO |
| K7-03 | `effective_valid_routes` / `build_system_prompt` gate on the flag | 0.3 d | K7-02 | TODO |
| K7-04 | `DEFAULT_CAPABILITY_MAP["kokoro"]` (tools via injected `ToolProviderPort`) | 0.2 d | K7-02 | TODO |

**Done when:** with `kokoro_enabled=False` the compiled-graph snapshot is unchanged;
with `True` a deliberation intent routes to `kokoro` end-to-end.

### PHASE K8 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| K8-01 | Unit tests: souls (parse/validate/tier/triad) | 0.5 d | K1 | TODO |
| K8-02 | Unit tests: `SoulAgent` (secure-prompt spy, deterministic fake) | 0.4 d | K3 | TODO |
| K8-03 | Unit tests: `deliberate` (early-stop, max-rounds, arity) | 0.4 d | K4 | TODO |
| K8-04 | Unit tests: judge (lens summaries, dissent, action on/off, deny path) | 0.5 d | K5 | TODO |
| K8-05 | Unit tests: subgraph end-to-end with fakes + no-provider-import guard | 0.5 d | K6 | TODO |
| K8-06 | Integration test: graph snapshot unchanged when `kokoro_enabled=False` | 0.3 d | K7 | TODO |
| K8-07 | `docs/kokoro.md` + `examples/kokoro_deliberation.py` | 0.5 d | K7 | TODO |
| K8-08 | `README.md` + `CHANGELOG.md` entries | 0.2 d | K7 | TODO |

**Done when:** `uv run pytest -m unit` green; `ruff`, `mypy`, `bandit` clean;
coverage ≥ project target on new modules.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Soul parsing diverges from skills | Share/adapt `skills/base.py` helpers; same frontmatter rules |
| `filterwarnings=error` trips on new `DeprecationWarning`s | Keep imports current; no deprecated APIs |
| Behavior leak when disabled | Gate every wiring point on `kokoro_enabled`; snapshot test (K8-06) |
| Action execution risk | Off by default; `ActionInterceptor` + guardrails; audited |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-KOK-01…RF-KOK-12) implemented and tested.
- [ ] Three default souls load and deliberate to a judged verdict end-to-end.
- [ ] With `kokoro_enabled=False`, zero behavior change (snapshot proven).
- [ ] No provider SDK or `prismal.mcp`/`prismal.skills` import inside `agents/kokoro/`.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green.
- [ ] `PLAN`/`SPEC`/`ARCHITECTURE` marked `IMPLEMENTED`; `README`/`CHANGELOG` updated.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| K1 | Souls tier | ~2.6 d |
| K2 | Exceptions + settings | ~0.7 d |
| K3 | SoulAgent | ~1.3 d |
| K4 | Deliberation | ~1.3 d |
| K5 | Judge + action | ~1.7 d |
| K6 | Subgraph | ~1.5 d |
| K7 | Supervisor integration | ~1.2 d |
| K8 | Tests + docs + packaging | ~3.3 d |
| **Total** | | **~13.6 d** |
