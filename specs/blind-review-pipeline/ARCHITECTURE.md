# Prismal Blind Review Pipeline — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-10 |
| **Phase** | BRP |
| **Target package version** | `3.11.0` |
| **PLAN** | `specs/blind-review-pipeline/PLAN.md` |
| **SPEC** | `specs/blind-review-pipeline/SPEC.md` |
| **TASKS** | `specs/blind-review-pipeline/TASKS.md` |

---

## 1. Context

BRP adds a **spec → implement → blind dual review → correct** subgraph. It
composes primitives Prismal already ships — `SubgraphFactory`,
`agents/subgraphs/gates.py`, `ProviderRegistry`, and the injected
`ToolProviderPort` (Phase Y) — so no new LangGraph capability is required
(see `PLAN.md §2`). The one new invariant is **blindness**, addressed in
§5 (DD-BRP-001).

## 2. Feasibility with LangGraph (confirmed)

A LangGraph node with two static outgoing edges to two different node names
runs both targets as part of the same superstep; a downstream node with
incoming edges from both waits for both predecessors to complete before it
runs (standard LangGraph join semantics — the same mechanism
`dev_pipeline` relies on when `dev_unit_tester` funnels into
`dev_test_aggregator`). Because BRP's panel size is **fixed at exactly two**
by design, this static join is sufficient — dynamic `Send`-based fan-out
(as `skynet-swarm` uses for a *variable* N) is unnecessary complexity here
(DD-BRP-005).

## 3. Proposed Architecture

### 3.1 New Modules

```
prismal/
├── agents/
│   └── subgraphs/
│       └── blind_review_pipeline/        ← NEW subgraph package
│           ├── __init__.py
│           ├── spec_agent.py             ← make_spec_agent_node() factory (produces spec_agent_node)
│           ├── implementer_agent.py      ← make_implementer_agent_node() factory (produces implementer_agent_node)
│           ├── reviewer_node.py          ← make_reviewer_node() factory + BlindnessGuard (produces reviewer_a / reviewer_b)
│           ├── synthesis.py              ← synthesize_verdicts() (deterministic merge)
│           └── builder.py                ← build_blind_review_pipeline_subgraph / register_blind_review_pipeline
├── core/
│   ├── config.py                         ← blind_review_* settings (extension, not new file)
│   └── exceptions.py                     ← BlindReviewPipelineError hierarchy (extension)
```

Reused (unchanged): `agents/subgraphs/factory.py::SubgraphFactory`,
`agents/subgraphs/gates.py` (`score_gate`, `seed_hitl_metadata`,
`human_approval_node`, `hitl_gate`), `agents/subgraphs/code_review/types.py`
(`CodeIssue`, `CodeReviewReport` — imported, not duplicated),
`agents/extension/providers.py` (`ToolProviderPort`/`CompositeToolProvider`),
`providers/registry.py::ProviderRegistry`, `security/prompt_builder.py`,
`security/action_interceptor.py`, `security/audit.py::AuditLogger`,
`agents/subgraphs/registry.py::SubgraphRegistry`, `agents/intent_router.py`.

### 3.2 Subgraph Topology

```
                 ┌─────────────┐
          START →│  spec_agent │  spec_agent_node(state) → spec_artifact
                 └──────┬──────┘  writes metadata.blind_review.spec_artifact
                        ▼
                 ┌──────────────┐
                 │ implementer  │  implementer_agent_node(state) → implementation_artifact
                 └──────┬───────┘  reads ONLY spec_artifact (+ synthesis.issues on retry)
                        │          writes metadata.blind_review.implementation_artifact
             ┌──────────┴───────────┐   static two-way fan-out (DD-BRP-005)
             ▼                      ▼
      ┌─────────────┐        ┌─────────────┐
      │ reviewer_a  │        │ reviewer_b  │   each reads ONLY spec_artifact +
      └──────┬──────┘        └──────┬──────┘   implementation_artifact — NEVER
             │                      │           state["messages"] (DD-BRP-001)
             │   ProviderRegistry(model_C)      ProviderRegistry(model_D)
             │   ToolProviderPort(caps_a)       ToolProviderPort(caps_b)
             └───────────┬──────────┘
                         ▼
                 ┌───────────────┐
                 │  synthesis    │  synthesize_verdicts(verdict_a, verdict_b)
                 └───────┬───────┘  deterministic merge → CodeReviewReport-shaped score
                         │
                (score_gate: score >= blind_review_approval_threshold
                            OR iteration_count >= blind_review_max_iterations)
             ┌───────────┴────────────┐
      pass   ▼                        ▼  fail
    ┌──────────────────┐    back to implementer_agent
    │  approval_seed    │    (iteration_count += 1, synthesis.issues carried forward)
    │  human_approval   │  hitl_gate — bypassed when settings.hitl_enabled is False
    └─────────┬─────────┘  (same seed_hitl_metadata/human_approval_node/hitl_gate as dev_pipeline)
              ▼
             END
```

### 3.3 Data Flow

1. `spec_agent_node` turns the incoming goal into `spec_artifact` (a string
   or structured spec document) and writes it to
   `state["metadata"]["blind_review"]["spec_artifact"]`.
2. `implementer_agent_node` reads only that field (plus, on a retry pass,
   `state["metadata"]["blind_review"]["synthesis"]["issues"]` — the
   structured correction list, never raw reviewer prose per `PLAN.md §6.2`)
   and produces `implementation_artifact`.
3. Both reviewer nodes run off the same two inputs
   (`spec_artifact`, `implementation_artifact`) and never read
   `state["messages"]`. Each writes its own verdict to
   `metadata.blind_review.reviewer_a_verdict` /
   `...reviewer_b_verdict` — disjoint keys, so no channel merge conflict
   even though both write to the same `metadata` dict in the same
   superstep (mirrors how `dev_unit_tester` writes are merged before
   `dev_test_aggregator` runs).
4. `synthesis` merges both `CodeReviewReport`s deterministically (§5,
   DD-BRP-003) into one score + a de-duplicated issue list.
5. `score_gate` (reused from `gates.py`, unmodified) routes to the HITL
   approval seed on pass, or back to `implementer_agent` on fail, bounded by
   the existing top-level `state["iteration_count"]`.
6. On approval, the existing `seed_hitl_metadata` → `human_approval_node` →
   `hitl_gate` chain runs exactly as it does in `dev_pipeline`, including the
   `settings.hitl_enabled` bypass for CI/CD.

All BRP-specific state is namespaced under `state["metadata"]["blind_review"]`.

## 4. Blindness Mechanism (the one new invariant)

Prismal's `AgentState` is a single shared `TypedDict`; there is no per-node
state partitioning in LangGraph itself. "Blindness" is therefore a
**discipline enforced by what a node's prompt-construction code is allowed
to read**, not a framework feature. BRP enforces it two ways:

1. **Structural (input contract).** `make_reviewer_node()` builds each
   reviewer's node function to accept a narrow, pre-extracted
   `(spec_artifact, implementation_artifact)` pair — the node function itself
   never receives the full `state["messages"]` list as an argument, mirroring
   exactly how `code_review`'s `reviewer_fn: Callable[[str, str], ...]`
   receives only `(code, file)` today (see `PLAN.md §2`).
2. **Static + runtime guard (defense in depth).**
   - A new AST-based test (`tests/unit/agents/subgraphs/blind_review_pipeline/
     test_reviewer_blindness_guard.py`, mirroring the existing
     `tests/unit/agents/extension/test_no_mcp_skills_imports.py`) parses
     `reviewer_node.py` and fails CI if any subscript/attribute access
     matching `state["messages"]` / `state.get("messages"` appears in the
     module.
   - A runtime `BlindnessGuard.assert_no_message_leak(prompt_text)` helper
     (best-effort substring/heuristic check) is called immediately before
     each reviewer's `SecurePromptBuilder.build()` call, raising
     `BlindReviewBlindnessViolationError` if the constructed prompt appears
     to embed serialized message history. This is a defensive backstop, not
     the primary control — the primary control is (1).

## 5. Design Decisions

### DD-BRP-001: Blindness is a prompt-construction discipline, not a state-partitioning feature

LangGraph/`AgentState` do not support per-node state hiding. Rather than
attempting to fork the state model, BRP narrows *what each reviewer node
function is given* and backs that with a static AST guard + a runtime
assertion. **Alternative rejected:** a parallel "sandboxed state" channel
per reviewer — would require forking `AgentState`'s reducer model repo-wide
for one feature.

### DD-BRP-002: Reuse `ToolProviderPort` capability filtering for "own MCP per agent"

`CompositeToolProvider.get_tools(agent_name=..., capabilities=[...])`
already exists and is exactly the mechanism needed to give
`spec_agent`/`implementer_agent`/`reviewer_a`/`reviewer_b` distinct tool
sets. No new tool-provider code is needed — only four new capability lists
in settings/config. **Alternative rejected:** four separate
`ToolProviderPort` instances — unnecessary; the existing single composite
provider already differentiates by `agent_name`/`capabilities`.

### DD-BRP-003: Deterministic synthesis, no LLM arbiter, in Phase BRP

`synthesize_verdicts()` merges two `CodeReviewReport`s with plain Python
(e.g. `score = min(a.score, b.score)`, `issues = dedupe(a.issues + b.issues)`,
`approved = score >= threshold`) — no LLM call. This keeps the pattern to
exactly the four agents the user specified and avoids adding a fifth
LLM-backed role. **Alternative rejected (deferred to BRP+):** an LLM judge
node (à la `KokoroJudgeAgent` or `debate_round`'s moderator) for
disagreement — adds cost/latency and a fifth agent; only worth it if
deterministic merges prove insufficient in practice.

### DD-BRP-004: Reuse `dev_pipeline`'s HITL wiring verbatim

`seed_hitl_metadata`, `human_approval_node`, and `hitl_gate` from
`agents/subgraphs/gates.py` are used exactly as `dev_pipeline` uses them —
same artifact-field/risk-level parameterization, same
`settings.hitl_enabled` bypass. **Alternative rejected:** a bespoke approval
node — would duplicate battle-tested `interrupt()`/`Command(resume=...)`
handling for no benefit.

### DD-BRP-005: Static two-way fan-out, not dynamic `Send`

The reviewer panel is fixed at exactly 2 by the user's specification ("otros
dos agentes"). `skynet-swarm`'s `make_parallel_dispatcher` exists precisely
for *variable*-N fan-out and would be over-engineering here — two plain
edges from `implementer_agent` to `reviewer_a` and `reviewer_b`, converging
at `synthesis`, are simpler, easier to test, and sufficient.
**Alternative rejected:** `Send()`-based fan-out over a 2-element list —
adds the dynamic-dispatch machinery of `skynet-swarm` for a size that never
varies.

### DD-BRP-006: Reuse `CodeIssue`/`CodeReviewReport`, do not duplicate types

`agents/subgraphs/code_review/types.py` already defines exactly the shape a
reviewer verdict needs (`severity`, `category`, `description`, `file`,
`line`, `suggestion`, plus a `score`/`approved` report wrapper). BRP imports
these rather than defining parallel dataclasses. **Alternative rejected:** a
new `ReviewVerdict` dataclass — would fork a type that already has tests and
a consumer (`report_generator_node`) elsewhere in the codebase.

### DD-BRP-007: Per-role LLM resolution via `ProviderRegistry`, no new abstraction

Each node resolves `ProviderRegistry(settings).get_llm(model_id)` where
`model_id` is `None` (→ registry default) or a per-role settings field
(`blind_review_spec_model`, `..._implementer_model`,
`..._reviewer_a_model`, `..._reviewer_b_model`) — the exact pattern
`MixtureOfAgents.__init__(proposer_models=[...], aggregator_model=...)` and
`SkynetSupervisor`'s lazy `ProviderRegistry().get_llm()` default already
establish. **Alternative rejected:** a dedicated `BrpModelRouter` class —
unnecessary indirection over an already-simple call.

## 6. Sequence — one correction iteration

```
User goal
   │
   ▼
spec_agent ──spec_artifact──▶ implementer_agent ──artifact──┬──▶ reviewer_a ──verdict_a──┐
                                                              └──▶ reviewer_b ──verdict_b──┤
                                                                                            ▼
                                                                                       synthesis
                                                                                            │
                                                                        score >= threshold? │
                                                              ┌─────────────────────────────┴────┐
                                                        yes   ▼                                   ▼ no
                                                  approval_seed → human_approval → hitl_gate   implementer_agent
                                                        │                                     (iteration_count += 1)
                                                        ▼
                                                       END
```

## 7. Non-Functional Design Notes

- **Concurrency.** `reviewer_a`/`reviewer_b` have no data dependency on each
  other and can be awaited concurrently by the graph runtime; their outputs
  are written to disjoint metadata keys, so no lock/merge conflict exists.
- **Idempotent registration.** `register_blind_review_pipeline()` follows
  the exact `if registry.get(_NAME) is not None: return` guard used by
  `register_dev_pipeline`/`register_code_review`.
- **Checkpointing.** Isolated per-subgraph, `data/db/checkpoints_subgraph_
  blind_review_pipeline.db` in production, `:memory:` in tests — same
  convention as every other `SubgraphFactory.build()` caller.
