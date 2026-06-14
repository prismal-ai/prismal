# Prismal Agent Evaluation & Reliability Harness — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Phase** | V (Verification / Evaluation) |
| **Target package version** | `3.3.0` (SemVer minor) |
| **PLAN** | `specs/agent-eval-harness/PLAN.md` |
| **SPEC** | `specs/agent-eval-harness/SPEC.md` |
| **TASKS** | `specs/agent-eval-harness/TASKS.md` |

---

## 1. Context

Unit tests prove *structure*; they do not prove that the **composed agentic system** (supervisor + 26 agents + tools + RAG + memory, multi-turn) behaves. The 2026 "scaffold gap" is exactly this: model-in-isolation evals do not predict harness behavior. This phase adds a reproducible **evaluation harness** that runs eval-sets against the **real compiled graph**, captures the **trajectory**, scores it, compares against a baseline (regression gate), and runs an **adversarial suite** that proves the security/hardening controls contain real attacks. It is **additive**: it never touches the agent runtime; it *observes* it.

## 2. Feasibility with the existing core (confirmed)

- `get_async_compiled_graph()` exposes `.astream(...)` → the harness captures the full event stream (messages, tool-calls, visited nodes) without instrumenting agents.
- `build_test_runtime(...)` + `FakeToolProvider` + `FakeVectorStore` + `FakeConfigSource` give deterministic, I/O-free runs — the default eval mode.
- The checkpointer enables **replay** from a saved state → golden-transcript regression.
- `providers/` gives a model-agnostic **LLM-as-judge**; `monitoring/` gives cost/latency/traces and Langfuse export.
- `prismal/budget/` meters eval cost; `prismal/security/` (+ Phase H controls) is what the adversarial suite asserts against.

No agent code changes are required.

## 3. Proposed Architecture

### 3.1 New package `prismal/eval/`

| Module | Purpose |
|---|---|
| `eval/types.py` | `EvalCase`, `EvalSet`, `Assertion`, `Trajectory`, `CaseResult`, `Scorecard` |
| `eval/runner.py` | `EvalRunner` — runs a case against the graph, captures the trajectory |
| `eval/trajectory.py` | trajectory capture from `astream` + trajectory metrics |
| `eval/assertions.py` | exact / semantic / llm_judge / tool_usage / groundedness asserts |
| `eval/judges.py` | LLM-as-judge + RAG faithfulness (reuses `providers/`) |
| `eval/regression.py` | baseline compare + threshold gate |
| `eval/redteam/` | adversarial scenario catalog + loader (corpus example shipped) |
| `eval/report.py` | Scorecard → JSON + Markdown; optional Langfuse export |
| `eval/__main__.py` | `python -m prismal.eval run ...` CLI |
| `core/config.py` | `eval_*` settings |

`prismal/eval/` is a **sibling** of the agents, not a node — it imports the public graph entry point and the public ports; it never lives inside `agents/**`.

### 3.2 Eval-set format

Eval-sets are versioned YAML/JSON committed next to the code (`tests/eval/sets/*.yaml`):

```yaml
suite: rag_groundedness
cases:
  - id: rag-001
    input: "What does the budget hard cap do?"
    setup: { tool_provider: fake, vector_store: fake, seed: 7 }
    assertions:
      - type: tool_usage         # which agent/tool must (not) be used
        must_call: ["rag_agent"]
        max_steps: 6
      - type: groundedness       # answer supported by retrieved context
        min_score: 0.8
      - type: llm_judge
        rubric: "Answer is correct and cites the budget layer"
        min_score: 0.7
```

### 3.3 Data flow

```
EvalSet ──► EvalRunner.run_case ──► get_async_compiled_graph().astream()
                  │                        │
                  │                   Trajectory (messages, tool_calls, nodes, cost, latency)
                  ▼                        │
            Assertions ◄───────────────────┘
                  │  (exact | semantic | llm_judge | tool_usage | groundedness)
                  ▼
             CaseResult ──► Scorecard ──► regression.compare(baseline) ──► gate (pass/fail)
                                   │
                                   └► report.to_json / to_markdown / to_langfuse
```

### 3.4 Trajectory model

```
Trajectory = {
  case_id, final_answer,
  steps: [ {node, role, content, tool_name?, tool_args?, tool_ok?} ],
  visited_nodes: [...], tool_calls: int, tool_errors: int,
  cost_usd, tokens, latency_ms, terminated: bool
}
```

Captured purely from the `astream` event stream + `monitoring/` cost; no agent instrumentation.

## 4. Design Decisions

### DD-EVL-001: Evaluate the real graph, not the model
Every case runs through `get_async_compiled_graph()` so the score reflects harness + model together (closes the scaffold gap). Direct-API eval is explicitly out of scope.

### DD-EVL-002: Deterministic by default, `live_api` opt-in
Default runs use `build_test_runtime` fakes + seeds → reproducible, CI-safe, free. LLM-judge / real-model runs are gated behind the existing `live_api`/`slow` markers and metered via Budget.

### DD-EVL-003: Composable assertions
Assertions are small, typed, and combine per case: `exact`, `semantic` (embedding cosine via the embeddings port), `llm_judge` (rubric), `tool_usage` (must/never call, max steps), `groundedness` (answer ↔ retrieved context). New assertion types are pluggable.

### DD-EVL-004: Regression is a baseline diff with tolerance
A scorecard is compared against a committed baseline; the gate fails if any metric regresses beyond a per-suite tolerance. Tolerance absorbs benign LLM non-determinism; fakes make most suites exactly reproducible.

### DD-EVL-005: Adversarial suite asserts containment, not just "no crash"
The red-team suite runs injection / tool-abuse / exfiltration / jailbreak scenarios against real flows and asserts the **security controls** (L1–L5 + Phase H taint/injection/policy) **block** them — e.g. an indirect-injection case must raise `injection_detected_total` and not execute the malicious tool. This is the executable counterpart to `specs/runtime-hardening/`.

### DD-EVL-006: Reuses ports, owns nothing of the runtime
The harness depends only on the public graph entry point, `ToolProviderPort`/`VectorStorePort`/`ConfigSourcePort` fakes, `providers/` (judge), `monitoring/` (cost). It is additive and removable.

## 5. Security & cost
- Adversarial corpus payloads reach a model only through the normal `SecurePromptBuilder` path of the agents under test (the harness does not bypass controls).
- LLM-judge calls are metered through Budget; default fakes keep CI cost at zero.

## 6. Observability
- Scorecards export to JSON + Markdown and optionally to **Langfuse evals**.
- Per-case OTel span `prismal.eval.case`; suite-level summary counters (`prismal.eval_cases_total{suite,outcome}`).

## 7. Relationship to existing specs
- **`runtime-hardening/` (H)** — the red-team suite is the proof harness for its controls.
- **`cost-budget-governance/` (C)** — meters eval cost; eval can assert budget cutoffs trigger.
- **`monitoring/`** — trajectory cost/latency + Langfuse export.
- **`skill-creator`** — existing skill-level evals; this phase is the system-level superset.

## 8. Testing strategy (summary; detail in `TASKS.md`)
- Unit: trajectory capture from a fake stream; each assertion type; regression gate math; corpus loader.
- Integration: run a small eval-set against the real graph with fakes end-to-end; adversarial case proves containment; CLI smoke (`python -m prismal.eval run --suite ...`).
- CI: new `eval`/`redteam` markers; a job that runs the fake-backed suites and fails on regression or a passed attack.

## 9. Rollout
1. Ship `prismal/eval/` + 1 trajectory suite + 1 groundedness suite (fakes).
2. Add the red-team suite asserting Phase H controls.
3. Wire the regression gate into CI; publish a per-release scorecard.
