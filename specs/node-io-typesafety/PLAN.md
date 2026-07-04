# Prismal — Node I/O Type-Safety (Per-Node `AgentState` Contracts)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | NTS (Node I/O Type-Safety) |
| **Target package version** | `3.8.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect |
| **Priority** | P3 (polish / developer-experience & correctness, not security-critical) |
| **Related** | `README.md` — Roadmap item 8 ("Polish — no spec yet": per-node type safety); `docs/gap-analysis-loops-harness-guardrails-2026-07.md` — item #5 ("Type-safety por nodo (`AgentState` con validación Pydantic de I/O)"); `specs/extension-surface/` (Phase X — the `@prismal_node` decorator and middleware chain this feature slots into); `specs/runtime-hardening/` (the `mode ∈ {off, warn, enforce}` convention this feature reuses) |

---

## 1. Executive Summary

`AgentState` (`prismal/agents/state.py`) is a 21-field `TypedDict` shared by all 26+ specialist agent nodes wired into the LangGraph `StateGraph[AgentState]` supervisor. `TypedDict` gives LangGraph the merge/reducer semantics it needs (`add_messages` on `messages`; `operator.add` on `retrieved_docs`, `tool_results`, `parallel_results`), but it gives the framework **zero runtime or static guarantee** about what any individual node actually reads or writes. Every node function returns a bare `dict[str, object]` built by convention (e.g. `{"current_agent": "coder", "messages": [response]}`); nothing checks that the keys are spelled correctly, that the value types match `AgentState`'s declared types, or that a node hasn't silently clobbered a field it was never meant to touch. This gap is already self-identified in two places: `README.md`'s Roadmap (item 8, "Polish — no spec yet") and `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (item #5), both flagging it as a known, unaddressed, internal-only gap.

This document proposes **Phase NTS — Node I/O Type-Safety**: an **opt-in** contract layer that lets any node (whether a plain `@prismal_node`-wrapped callable or one added through `PrismalStateGraphBuilder.add_node()`) declare an `input_model: type[BaseModel] | None` and an `output_model: type[BaseModel] | None` describing the narrow subset of `AgentState` it consumes/produces. Validation is wired as a new innermost stage of the existing `@prismal_node` middleware chain — mirroring exactly how Runtime Hardening (Phase H) added `hardening_middleware` — and is governed by a `node_typesafety_mode ∈ {off, warn, enforce}` setting, the same convention already used by `hardening_mode` and `identity_mode`. With the master flag `node_typesafety_enabled=False` (the default), the compiled supervisor graph is byte-for-byte unchanged.

Critically, this is **not** a proposal to rewrite `AgentState` or migrate all 26+ nodes at once. Adoption is per-node and voluntary: a node with no declared models behaves exactly as it does today. The deliverable is the contract mechanism, its wiring, a worked incremental-adoption path starting with the highest-value (security-sensitive / cross-cutting) nodes, and a design decision on how far `AgentState` itself can evolve toward narrower per-family types without breaking LangGraph's single-`TypedDict`-per-graph requirement.

---

## 2. Context and Problem

### 2.1 Current situation

- **`AgentState` is a flat, ungoverned `TypedDict`.** `prismal/agents/state.py` defines 21 fields (`messages`, `current_agent`, `next_agent`, `task_plan`, `completed_tasks`, `pending_tasks`, `retrieved_docs`, `doc_grades`, `tool_results`, `tool_errors`, `parallel_results`, `dev_pipeline_modules`, `skynet_orders`, `risk_score`, `permissions_granted`, `security_flags`, `session_id`, `created_at`, `token_count`, `estimated_cost_usd`, `iteration_count`, `metadata`, `channel_context`). Only four fields have a custom reducer (`messages` via `add_messages`; `retrieved_docs`, `tool_results`, `parallel_results` via `operator.add`); everything else is plain merge/overwrite. `metadata: dict[str, Any]` is the deliberate escape hatch every opt-in phase (Hardening, Budget, Skynet, Kokoro, multimodal) already uses to avoid schema churn — this precedent matters for NTS's own design (see §3, NTS3).
- **Nodes read/write by convention, not by contract.** Representative specialist nodes (`prismal/agents/coder.py::coder_node`, `prismal/agents/researcher.py::researcher_node`, `prismal/agents/critic.py::critic_node`) all follow the same shape: read `state["session_id"]`, `state["messages"]` (sometimes `state["iteration_count"]`), call `react_loop(...)`, and return a small dict touching only `current_agent`, `messages`, and occasionally one scalar counter. Nothing declares this shape in code — it is tribal knowledge, verifiable only by reading each node's body.
- **`@prismal_node` (Phase X) already has the right seam.** The decorator's middleware chain (`error_mapping → otel → logger → security → audit → retry → timeout → user fn`, `prismal/agents/extension/_middleware.py::DEFAULT_MIDDLEWARE_STACK`) already has a precedent for an opt-in, flag-gated, innermost stage: `hardening_middleware`, added in Phase H as a complete passthrough when `hardening_enabled=False`. `core/exceptions.py` already contains a `NodeValidationError(NodeExecutionError)` stub — "Raised when the `state_update` returned by a node is not valid" — created but never populated with real validation logic.
- **`CLAUDE.md` already documents a related-but-distinct fact:** the extension-surface SPEC's original 8th middleware ("validation") was folded into `error_mapping` — that fold was about generic structural validation (is `state_update` a `dict`?), not about per-node Pydantic schema checking. NTS does not reopen that decision; it adds a **new**, separate, opt-in validation stage on top of it.

### 2.2 Problem

Without a declared I/O contract per node:

1. **Typos and drift are invisible.** A node that means to write `current_agent` but writes `current_agend` (or writes to `metadata["coder"]` when the reader expects `metadata["Coder"]`) fails silently — LangGraph merges whatever keys are present; nothing flags an unexpected or missing key.
2. **Refactors are unsafe.** Renaming or repurposing an `AgentState` field (e.g. changing `risk_score` semantics) has no mechanism to surface which of the 26+ nodes are affected short of a full-text grep and manual read of each node body.
3. **Plugin authors (Phase X ecosystem) have no contract to conform to.** `specs/extension-surface/` lets third parties register custom nodes and subgraphs, but a plugin node can return arbitrary garbage into `AgentState` with no feedback beyond "did the graph crash."
4. **The gap is explicitly tracked and unaddressed.** Both `README.md` (Roadmap item 8) and the 2026-07 gap-analysis report call this out as a known, low-urgency-but-real correctness gap with "no spec yet" — this document is that spec.

### 2.3 Opportunity

The necessary primitives already exist in the repo and require no new dependencies:

- `pydantic` is already a first-class dependency used extensively for contract types (`a2a/types.py`, `budget/types.py`, `Settings` itself) — declaring `BaseModel` subclasses as node I/O contracts is idiomatic, not novel.
- The `@prismal_node` middleware chain and `PrismalStateGraphBuilder.add_node()` (Phase X) are exactly the two seams where node metadata is already declared and where a new validation stage slots in without touching `agents/graph.py` or any of the 26+ node implementations that choose not to opt in.
- `NodeValidationError` already exists as an empty stub in `core/exceptions.py` — this feature gives it a real implementation instead of introducing a parallel exception hierarchy.
- The `mode ∈ {off, warn, enforce}` settings idiom (`hardening_mode`, `identity_mode`, each with a `model_validator(mode="after")` that rejects an unknown value) is an established, copy-pasteable pattern.

---

## 3. Target Users

### Persona 1: Framework Maintainer
- **Description:** Owns the 26+ built-in specialist nodes and the supervisor graph; needs confidence that a refactor of one node's return shape doesn't silently break a downstream reader.
- **Main need:** A way to declare "this node reads X, writes Y" that fails fast (in `warn` during rollout, `enforce` once trusted) instead of failing silently in production.

### Persona 2: Plugin Author (Phase X ecosystem)
- **Description:** Maintains a `prismal-x-<domain>` package contributing custom nodes via `@prismal_node` or `PrismalStateGraphBuilder`.
- **Main need:** A documented, structural way to declare their node's contract so it is self-checking, without being forced to understand all 21 `AgentState` fields.

### Persona 3: QA / Test Engineer
- **Description:** Writes unit and integration tests for individual nodes and subgraphs.
- **Main need:** A `BaseModel`-backed contract they can construct fixtures against and assert conformance of, instead of hand-rolling ad hoc dict assertions per node.

### Persona 4: Security / Hardening Reviewer
- **Description:** Already owns Runtime Hardening (Phase H) and Agent Identity Governance (Phase IDN); cares that security-sensitive nodes (`file_manager`, `cron_manager`, `skill_manager`, `codeact_agent`, `cua_agent`) cannot silently write outside their expected `state["metadata"]` namespace.
- **Main need:** The highest-value, highest-blast-radius nodes annotated first (NTS3's incremental adoption ordering), so a wrong write is caught in `enforce` mode before it reaches a downstream security gate.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Close the README/gap-analysis "per-node type safety" gap | `specs/node-io-typesafety/` exists with PLAN/ARCHITECTURE/SPEC/TASKS | This document set |
| Zero behavior change when disabled | `node_typesafety_enabled=False` ⇒ compiled graph byte-for-byte unchanged | Snapshot-tested (mirrors `H6-06`) |
| Opt-in, incremental adoption | A node with no declared `input_model`/`output_model` behaves identically to today | 100% backward compatible |
| Fail-open by default when enabled | `node_typesafety_mode` defaults to `warn` (log + metric, no state mutation) | Matches `hardening_mode` default |
| Fail-closed available | `node_typesafety_mode="enforce"` raises `NodeValidationError`, mapped by `error_mapping_middleware` | Consistent with existing error-mapping contract |
| Pilot coverage on highest-value nodes | ≥ 3 security-sensitive/cross-cutting nodes annotated as a worked example | `file_manager`, `cron_manager`, one more (see NTS3) |
| No new mandatory dependencies | `pydantic` only (already a core dependency) | 0 new deps |

---

## 5. Scope

### 5.1 In Scope (Phase NTS)

**NTS1 — `NodeIOSchema` contract types:**
- [ ] A way for a node to optionally declare `input_model: type[BaseModel] | None` and `output_model: type[BaseModel] | None`, added as new fields on `NodeMetadata` (with `None` defaults, so existing `@prismal_node` call sites are unaffected) and as new keyword-only parameters on the `prismal_node()` decorator.
- [ ] A validation helper module (`prismal/agents/extension/node_schema.py`) that validates a *narrow slice* of `AgentState`/`state_update` against a declared model — field names correspond 1:1 to `AgentState` keys; a model only needs to declare the fields it actually cares about (extra keys ignored, not required).
- [ ] Explicitly does **not** replace or subclass `AgentState`'s `TypedDict`, and does **not** require every node to declare a model (opt-in, per-node).

**NTS2 — Validation wiring in the middleware chain:**
- [ ] A new `node_io_validation_middleware` appended as the new innermost layer of `DEFAULT_MIDDLEWARE_STACK` (mirroring how `hardening_middleware` was added in Phase H), validating input before `user_fn` and output after.
- [ ] Equivalent wiring in `PrismalStateGraphBuilder.add_node()` via new `input_model`/`output_model` kwargs, following the same "auto-wrap unless already `@prismal_node`-decorated" idiom already used for `security`/`audit`/`timeout_s`/`retry`.
- [ ] `node_typesafety_mode ∈ {off, warn, enforce}` controls the failure behavior: `warn` logs + increments an OTel counter and passes the (possibly non-conforming) data through unchanged; `enforce` raises `NodeValidationError`, which `error_mapping_middleware` already knows how to map to a `state_update` with `metadata["error"]`.

**NTS3 — Incremental adoption path + `AgentState` evolution:**
- [ ] A documented order of adoption starting with security-sensitive/cross-cutting nodes (`file_manager`, `cron_manager`, `skill_manager`, `codeact_agent`, `cua_agent`) before the general 26-node population, since these carry the highest blast radius if a wrong write skips a downstream security gate.
- [ ] A design decision (`DD-NTS-003`) on whether/how `AgentState` could eventually gain narrower per-node-family `TypedDict` subtypes, while keeping the single shared `TypedDict` LangGraph's `StateGraph` constructor requires. The chosen approach for v1: keep `AgentState` unchanged; `input_model`/`output_model` are **boundary-validation Pydantic projections**, not the graph's state schema.
- [ ] An explicit open question (flagged, not resolved) on whether a future LangGraph version's per-node `input`/`output` schema support (distinct from the graph-level state schema) should be investigated as a v2 alternative.

**NTS4 — Integration, settings, OTel counters, tests, docs, packaging:**
- [ ] Settings: `node_typesafety_enabled: bool = False`, `node_typesafety_mode: str = "warn"`, validated by a new `_validate_node_typesafety` model validator (mirrors `_validate_hardening`).
- [ ] Exceptions: extend the existing `NodeValidationError` stub (already present in `core/exceptions.py` since Phase X) with real fields (`schema_errors: list[str]`, `direction: Literal["input", "output"]`) instead of introducing a parallel hierarchy.
- [ ] OTel counters: `prismal.node_io_validation_failures_total{node,direction}`, `prismal.node_io_validated_total{node,direction}`.
- [ ] Unit tests (schema helper, middleware in all 3 modes, settings validator) + integration tests (snapshot proving `node_typesafety_enabled=False` ⇒ unchanged compiled graph; end-to-end `warn`/`enforce` behavior on a pilot node).
- [ ] `docs/node-typesafety.md` (quickstart + adoption guide) and `examples/node_typesafety.py` (runnable, annotates a toy node).
- [ ] `README.md` Roadmap and `CHANGELOG.md` entries marked as **planned** for `3.8.0` (not shipped).

### 5.2 Out of Scope (Excluded from Phase NTS)

- **Migrating all 26+ built-in nodes to declare schemas.** Only the NTS3 pilot set is annotated as part of this phase; broad migration is a follow-up, node-by-node, driven by whoever owns each node.
- **Replacing `AgentState`'s `TypedDict` with a Pydantic model.** LangGraph's `StateGraph` reducer machinery (`add_messages`, `operator.add`) is built and tested against `TypedDict`; a full migration is a distinct, much larger effort explicitly deferred (see DD-NTS-003 and the Open Questions in `ARCHITECTURE.md`).
- **Per-node LangGraph `input_schema`/`output_schema` node-level typing** (if/when the installed LangGraph version supports it as a first-class graph-construction feature) — noted as a v2 candidate, not built here.
- **A pluggable, non-Pydantic validation engine** (e.g. `jsonschema`, `cerberus`) as a hard requirement for v1 — see the `NodeIOValidatorPort` Protocol in `ARCHITECTURE.md`/`SPEC.md`, included as a design option but flagged as an open question on whether it belongs in v1 or is YAGNI.
- **Sampling / partial validation for performance** (e.g. validating 1-in-N invocations in production) — noted as a Future Consideration, not required for the opt-in default (`warn`/`enforce` validate every call by design; `off` is the zero-overhead escape hatch).

### 5.3 Future Considerations (Phase NTS.1+)

- Broad, node-by-node migration of the remaining specialist nodes once the pilot set proves the pattern in production.
- Investigation of native LangGraph per-node schema support as a v2 alternative to the current boundary-validation-only approach.
- Sampling/rate-limited validation for very high-throughput deployments where per-call Pydantic validation overhead becomes measurable.
- A `NodeIOValidatorPort` hexagonal port (mirroring Phase Y/Z/W) if a real user need for pluggable non-Pydantic validation engines emerges.
- CI lint that fails a PR introducing a new node without at least a `warn`-mode schema, once adoption is broad enough to make this reasonable.

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-NTS-001 | `NodeMetadata` gains optional `input_model`/`output_model: type[BaseModel] \| None` fields, defaulting to `None` | `MUST` |
| RF-NTS-002 | `prismal_node(input_model=, output_model=)` accepts the two new keyword-only parameters | `MUST` |
| RF-NTS-003 | `node_schema.py` provides `validate_node_input()`/`validate_node_output()` operating on a narrow field subset (extra keys ignored) | `MUST` |
| RF-NTS-004 | A new `node_io_validation_middleware` is appended as the innermost layer of `DEFAULT_MIDDLEWARE_STACK`, gated on `settings.node_typesafety_enabled` | `MUST` |
| RF-NTS-005 | `PrismalStateGraphBuilder.add_node()` accepts `input_model`/`output_model` kwargs, forwarded when auto-wrapping | `MUST` |
| RF-NTS-006 | `node_typesafety_mode ∈ {off, warn, enforce}`; `warn` logs + counts + passes through; `enforce` raises `NodeValidationError` | `MUST` |
| RF-NTS-007 | `NodeValidationError` (existing stub) gains `schema_errors`/`direction` fields; no new exception hierarchy introduced | `MUST` |
| RF-NTS-008 | `node_typesafety_enabled=False` ⇒ compiled supervisor graph is byte-for-byte unchanged (snapshot-tested) | `MUST` |
| RF-NTS-009 | OTel counters `prismal.node_io_validation_failures_total{node,direction}` and `prismal.node_io_validated_total{node,direction}` registered | `SHOULD` |
| RF-NTS-010 | ≥ 3 pilot nodes (security-sensitive/cross-cutting) annotated with real `input_model`/`output_model` as a worked example | `SHOULD` |
| RF-NTS-011 | Runnable example (`examples/node_typesafety.py`) + `docs/node-typesafety.md` | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- Validation overhead per invocation must stay in the same order of magnitude as the existing `@prismal_node` overhead budget (≤ 5 ms, per `specs/extension-surface/PLAN.md` §7 Performance) when a model is declared; **zero** overhead (no-op passthrough) when `node_typesafety_enabled=False` or a node declares no models.

### Security
- Declared models must never be constructed from or echo secret material; validation errors logged/audited must include field *names*, not field *values* (consistent with `NodeExecutionError`'s existing convention of never storing state values, only `state_keys`).
- `enforce` mode on a security-sensitive node (e.g. `file_manager`) must fail closed (raise, not silently pass) — this is the primary security value of the feature for the NTS3 pilot set.

### Availability
- A validation failure in `warn` mode must never abort the node or the graph — pure passthrough plus observability.
- A validation failure in `enforce` mode must be captured by the existing `error_mapping_middleware` (via `NodeValidationError` inheriting `NodeExecutionError`) so the graph degrades gracefully instead of crashing, exactly like any other `NodeExecutionError` today.

### Scalability
- The mechanism must not require touching `agents/graph.py`, the 26+ node registrations, or any subgraph factory — annotation is additive, at the individual node call site only.

### Observability
- OTel counters per RF-NTS-009; structured log entries on both `warn` and `enforce` paths with `node_name`, `direction`, and the list of failing field names (not values).

### Maintainability
- No new mandatory dependency (`pydantic` is already core).
- `ruff` + `mypy --strict` + `bandit` clean on all new/modified modules.
- Coverage ≥ project target (`fail_under=80`) on new modules; `node_schema.py` itself targeted at ≥ 85% given it is a boundary-validation module.

### Documentation
- Quickstart in `docs/node-typesafety.md` showing a before/after of one pilot node.
- Explicit migration guidance: how to add a schema to an existing, unannotated node without breaking it (start in `warn`, promote to `enforce` once clean in staging).

---

## 8. Constraints and Dependencies

### Technical Constraints
- Python 3.13+, `uv` as the manager (unchanged).
- `prismal/` stays a PEP 420 namespace package — no `__init__.py` added.
- `AgentState` remains a single, LangGraph-compatible `TypedDict`; NTS must not require LangGraph to accept a Pydantic model as the graph-level state schema.
- Must slot into the **existing** `@prismal_node` middleware chain and `PrismalStateGraphBuilder`, not introduce a parallel node-wrapping mechanism.

### External Dependencies

| Dependency | Type | Use | Status |
|---|---|---|---|
| `pydantic` | Existing | `BaseModel` subclasses as node I/O contracts | ✅ Already a core dependency |
| `langgraph` | Existing | Unaffected — validation happens at the `@prismal_node` boundary, not inside `StateGraph` | ✅ Already included |
| `opentelemetry-api` | Existing | New counters | ✅ Already included |
| `structlog` | Existing | Warn-mode logging | ✅ Already included |

**No new dependencies.**

---

## 9. User Stories

### Epic NTS: Declare a Node's Contract

**US-NTS-001:** As a Framework Maintainer, I want to declare what a node reads and writes so a typo in a returned key is caught in `warn` mode during development instead of silently corrupting state in production.
```python
from pydantic import BaseModel
from prismal.agents.extension import prismal_node

class FileManagerOutput(BaseModel):
    current_agent: str
    metadata: dict

@prismal_node(name="file_manager", output_model=FileManagerOutput)
async def file_manager_node(state):
    ...
    return {"current_agent": "file_manager", "metadata": {"file_manager": {...}}}
```
- [ ] With `node_typesafety_enabled=False`, this node behaves identically to an undecorated one.
- [ ] With `node_typesafety_enabled=True` and `node_typesafety_mode="warn"`, a malformed return logs + increments a counter but does not raise.
- [ ] With `node_typesafety_mode="enforce"`, a malformed return raises `NodeValidationError`, mapped by `error_mapping_middleware`.

### Epic NTS: Adopt Incrementally via the Builder

**US-NTS-002:** As a Plugin Author, I want to declare my custom node's I/O contract through `PrismalStateGraphBuilder` without hand-wrapping it in `@prismal_node` myself.
```python
builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn, input_model=ClassifyInput, output_model=ClassifyOutput)
```
- [ ] `classify_fn` is auto-wrapped with `@prismal_node(input_model=..., output_model=...)` exactly as `security`/`audit`/`timeout_s` are today.

### Epic NTS: Prove the Flag Is Truly Opt-In

**US-NTS-003:** As a Framework Maintainer, I want a test proving that turning the whole feature off leaves the compiled supervisor graph unchanged, so I can ship this without re-certifying the other 25 nodes.
- [ ] A snapshot/structural-equality test (mirroring `H6-06` in Runtime Hardening) asserts `node_typesafety_enabled=False` ⇒ identical compiled graph.

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Validation overhead becomes noticeable on hot-path nodes (e.g. `react_loop`-heavy agents) | Low | Medium | `warn`/`enforce` validate only nodes with a declared model; `off` (default) is a pure no-op; overhead budget documented and bench-tested |
| Teams over-annotate too fast, generating `warn` noise that gets ignored | Medium | Low | NTS3's ordered pilot list keeps initial adoption small and deliberate; docs recommend `warn`-first rollout per node |
| `enforce` mode on a widely-used node causes unexpected production breakage | Low | High | `enforce` is never the default; `warn` is the only default when the flag is on; promotion to `enforce` is an explicit, node-by-node operator decision |
| Confusion between this feature's "validation" and the extension-surface's already-folded "validation into error_mapping" | Medium | Low | Explicitly reconciled in `ARCHITECTURE.md`/`SPEC.md`: the two are different concerns (generic dict-shape check vs. per-node Pydantic schema) |
| `AgentState` TypedDict/Pydantic model field-name drift (a model references a field that no longer exists in `AgentState`) | Medium | Medium | A dedicated unit test asserts every declared model's field names are a subset of `AgentState`'s `TypedDict` keys via `get_type_hints()` |
| Scope creep into a full `AgentState` rewrite | Low | High | Explicitly out of scope (§5.2); DD-NTS-003 documents why the boundary-projection approach is chosen for v1 |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| NTS1 — `NodeIOSchema` contract types | 0.8 week | `node_schema.py`, `NodeMetadata`/`prismal_node()` extensions |
| NTS2 — Middleware + builder wiring | 0.8 week | `node_io_validation_middleware`, `add_node()` kwargs, mode dispatch |
| NTS3 — Incremental adoption + `AgentState` design decision | 0.6 week | Pilot annotations on ≥ 3 nodes, `DD-NTS-003` |
| NTS4 — Settings, exceptions, OTel, tests, docs, packaging | 1.2 week | Settings, `NodeValidationError` extension, counters, tests, `docs/node-typesafety.md`, example |
| **Total** | **~3.4 weeks** | Complete, opt-in per-node type-safety layer |

---

## 12. Definition of Done (Phase NTS Global — target, not yet started)

- [ ] `NodeMetadata` and `prismal_node()` accept `input_model`/`output_model` with `None` defaults.
- [ ] `node_schema.py` implements `validate_node_input()`/`validate_node_output()` with unit tests.
- [ ] `node_io_validation_middleware` wired as the new innermost layer, gated on `node_typesafety_enabled`.
- [ ] `PrismalStateGraphBuilder.add_node()` accepts and forwards the two new kwargs.
- [ ] `node_typesafety_mode ∈ {off, warn, enforce}` implemented with the same validator idiom as `hardening_mode`.
- [ ] `NodeValidationError` extended with `schema_errors`/`direction`; no parallel exception hierarchy.
- [ ] Snapshot test proves `node_typesafety_enabled=False` ⇒ unchanged compiled graph.
- [ ] OTel counters registered and exercised by tests.
- [ ] ≥ 3 pilot nodes annotated as a worked example.
- [ ] `docs/node-typesafety.md` + `examples/node_typesafety.py` published.
- [ ] `README.md` Roadmap and `CHANGELOG.md` updated to reflect the *planned* `3.8.0` scope.
- [ ] `uv run pytest -m "not live_api"` passes at 100%; `ruff` + `mypy --strict` + `bandit` clean.
- [ ] PR merged to `main` with code review approved.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #5) and README Roadmap item 8 |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
