# Prismal — Agent Evaluation & Reliability Harness

## Strategic Plan / Product Requirements Document (PLAN) — *seed PRD*

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` (seed PRD; ARCHITECTURE/SPEC/TASKS missing) |
| **Version** | 0.1 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, QA Lead |
| **Priority** | P2 (reliability) |
| **Related** | `skill-creator` (skill evals), `monitoring/`, `agents/graph.py` |

---

## 1. Executive Summary

2026 research identifies the **"scaffold gap"**: evaluating the model in isolation (direct API) **does not predict** the behavior of the composed agentic system (with tools, RAG, memory, multi-turn). Prismal has evals at the *skill* level (`skill-creator`) but **no system-level evaluation harness** for the full graph: trajectories, tool usage, RAG fidelity, adversarial robustness, and **regression** between versions. This feature adds a reproducible evaluation harness to measure and protect the reliability of the agent as a system.

---

## 2. Context and Problem

- **No system measurement:** there is no standard way to run a set of cases against the graph and obtain metrics (per-task success rate, steps, tool-error rate, RAG groundedness, cost/latency).
- **No regression:** a change (prompt, model, pattern, dependency) can degrade quality without any test detecting it (current tests are unit/structural, not agent behavior).
- **No system-level adversarial evaluation:** L1–L5 security is tested at the unit level, but there is no automated *red-team* of injection/tool-abuse over real flows.
- **No quality traceability by version:** there are no *scorecards* per release.

---

## 3. Target Users

- **AI Engineer:** run an eval-set and see metrics/failures per case; compare two versions.
- **QA / Release Manager:** release gate by quality/regression threshold.
- **Security Lead:** adversarial suite (injection, exfiltration, tool-abuse) over real flows.
- **Maintainer:** scorecards per release; drift detection when bumping deps/models.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| System eval | Run an eval-set against the graph and produce metrics | Implemented |
| Trajectory metrics | success rate, steps, tool-error rate, RAG groundedness, cost/latency | Reported per case |
| Regression | Compare run vs baseline; gate by threshold | CI-integrable |
| Adversarial suite | automated security red-team over flows | ≥ N scenarios |
| Reproducibility | seeds + fakes; no avoidable non-determinism | Deterministic where applicable |
| Backward-compat | additive; does not touch agent runtime | 100% |

---

## 5. Scope (proposed)

### In Scope
- **`EvalCase` / `EvalSet`** (input, success criteria, asserts: exact/semantic/LLM-judge/tool-usage/groundedness).
- **`EvalRunner`** that executes the graph (or subgraph) per case, captures the **trajectory** (messages, tool-calls, visited nodes, cost/latency via `monitoring/`) and evaluates.
- **Metrics and scorecard** (JSON + Markdown); comparison against a **baseline** (regression) with a threshold gate.
- **Adversarial suite**: a catalog of scenarios (prompt injection, tool-abuse, exfiltration, jailbreak) executed against real flows; assert that L1–L5 contains them.
- **CI integration** (`pytest -m eval` and/or CLI `prismal eval`), with fakes (no `live_api`) and an optional `live_api` mode.
- LLM-as-judge with rubrics; reuses `providers/` (model-agnostic).

### Out of Scope
- Human annotation platform / eval UI (future; or integration with LangSmith/Langfuse evals).
- An in-house public benchmark (existing datasets can be imported).
- Fine-tuning from results.

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-EVL-001 | `EvalCase`/`EvalSet` model with composable criteria | `MUST` |
| RF-EVL-002 | `EvalRunner` executes the graph and captures trajectory + cost/latency | `MUST` |
| RF-EVL-003 | Asserts: exact, semantic, LLM-judge, tool-usage, RAG groundedness | `MUST` |
| RF-EVL-004 | Scorecard (JSON+MD) + comparison vs baseline (regression) with gate | `MUST` |
| RF-EVL-005 | Adversarial security suite over real flows | `SHOULD` |
| RF-EVL-006 | CLI `prismal eval` + `pytest -m eval` marker; fakes by default | `MUST` |
| RF-EVL-007 | Reproducibility (seeds, fakes); optional `live_api` mode | `SHOULD` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| LLM non-determinism falsifies regression | Seeds + fakes + thresholds with tolerance; LLM-judge with a stable rubric |
| Cost of running evals with real models | Default fakes; `live_api` opt-in; sampling |
| Evals that do not represent production (scaffold gap) | Run against the **real graph**, not the direct API |
| Keeping eval-sets up to date | Version eval-sets alongside the code; gate in CI |

---

## 8. Dependencies

- `agents/graph.py` (execute the graph), `monitoring/` (cost/latency/traces), `providers/` (model-agnostic judge), `security/` (adversarial containment assert).
- Optional: integration with Langfuse/LangSmith evals.

---

## 9. Next Steps

Expand to the full SDD set: design of `EvalRunner` and trajectory capture from the LangGraph *stream*, eval-set format, LLM-judge rubrics, regression gate in CI, and adversarial catalog.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | Seed PRD — system evaluation harness |
