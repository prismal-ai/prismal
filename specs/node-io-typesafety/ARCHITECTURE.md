# Prismal Node I/O Type-Safety — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | NTS |
| **Target package version** | `3.8.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Related PLAN** | `specs/node-io-typesafety/PLAN.md` |
| **Related SPEC** | `specs/node-io-typesafety/SPEC.md` |
| **TASKS** | `specs/node-io-typesafety/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect |

---

## 1. Context

`AgentState` (`prismal/agents/state.py`) is a 21-field `TypedDict` LangGraph merges across 26+ specialist agent nodes in `prismal/agents/graph.py`'s `StateGraph[AgentState]`. Only `messages` (`add_messages`), `retrieved_docs`, `tool_results`, and `parallel_results` (all `operator.add`) carry custom reducers; every other field is a plain overwrite. Nodes are async callables `(state: AgentState) -> dict[str, Any]` wrapped (optionally) by `@prismal_node` (`prismal/agents/extension/decorators.py`, Phase X) or added via `PrismalStateGraphBuilder.add_node()` (`prismal/agents/extension/builder.py`). Neither wrapper today validates the *shape* of what a node reads from or writes to `state` — only that the returned value is a `dict` (folded into `error_mapping_middleware`, per `CLAUDE.md`'s note on the extension-surface SPEC's original 8th "validation" middleware).

This document describes **Phase NTS — Node I/O Type-Safety**: an additive, opt-in Pydantic-backed contract layer at the `@prismal_node` boundary. It reuses the exact `@prismal_node` middleware seam that Phase X built and that Phase H (Runtime Hardening) already extended once (`hardening_middleware`), so this is the *second* precedent-following extension of that chain, not a new mechanism.

Guiding principle, mirrored from `specs/extension-surface/ARCHITECTURE.md`: **prismal adds a contract layer, it does not reinvent LangGraph's state model.** `AgentState` stays a single `TypedDict`; the new models are boundary projections validated at node entry/exit, never the graph's state schema.

---

## 2. Technical Objectives

- **Zero behavior change when off:** `node_typesafety_enabled=False` (default) leaves the compiled supervisor graph byte-for-byte identical — snapshot-tested, exactly like `hardening_enabled=False` (Phase H, `H6-06`).
- **Opt-in per node, not a hard requirement:** a node with no `input_model`/`output_model` behaves exactly as it does today, forever — no migration deadline, no big-bang rewrite of the 26+ existing nodes.
- **Reuse the existing middleware chain and builder**, not a parallel node-wrapping mechanism — `node_io_validation_middleware` slots in as the new innermost layer, mirroring `hardening_middleware`'s insertion point.
- **Reuse the existing `NodeValidationError` stub** (`core/exceptions.py`, present since Phase X) rather than inventing a new exception hierarchy.
- **`mode ∈ {off, warn, enforce}`**, mirroring `hardening_mode`/`identity_mode` exactly, including the same settings-validator idiom.
- **No new mandatory dependency** — `pydantic` is already core.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram — New / Modified Modules

```
prismal/
├── agents/
│   ├── state.py                        ← [UNCHANGED] AgentState TypedDict stays as-is
│   │
│   └── extension/
│       ├── node_schema.py              ← [NEW] NodeIOMode, NodeIOValidationResult,
│       │                                        validate_node_input(), validate_node_output()
│       ├── decorators.py               ← [MODIFIED] NodeMetadata gains input_model/output_model;
│       │                                        prismal_node() gains the two kwargs
│       ├── builder.py                  ← [MODIFIED] add_node() gains input_model/output_model kwargs
│       ├── _middleware.py              ← [MODIFIED] + node_io_validation_middleware
│       │                                        (new innermost layer, after hardening_middleware)
│       └── ports.py                    ← [MODIFIED, optional] + NodeIOValidatorPort (see DD-NTS-004,
│                                                 flagged as an open question for v1 inclusion)
│
└── core/
    ├── config.py                       ← [MODIFIED] node_typesafety_enabled, node_typesafety_mode
    │                                            + _validate_node_typesafety
    └── exceptions.py                   ← [MODIFIED] NodeValidationError gains schema_errors/direction

prismal/monitoring/otel.py              ← [MODIFIED] + 2 counters (SPEC-NTS-OTEL-001)

examples/
└── node_typesafety.py                  ← [NEW] annotates a toy node end to end

docs/
└── node-typesafety.md                  ← [NEW] quickstart + adoption guide

tests/
├── unit/agents/extension/
│   ├── test_node_schema.py             ← [NEW]
│   ├── test_decorators_io_models.py    ← [NEW]
│   └── test_builder_io_models.py       ← [NEW]
├── unit/core/
│   └── test_node_typesafety_settings.py ← [NEW]
└── integration/
    ├── test_node_typesafety_disabled_snapshot.py  ← [NEW] byte-for-byte graph unchanged
    └── test_node_typesafety_e2e.py                ← [NEW] warn vs enforce on a pilot node
```

### 3.2 Layer Diagram

```
                       ┌──────────────────────────────────────┐
                       │   NODE AUTHOR (core team or plugin)   │
                       │  @prismal_node(input_model=...,       │
                       │                output_model=...)      │
                       │  builder.add_node(..., input_model=,  │
                       │                       output_model=)  │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────────────┐
                       │  _middleware.py DEFAULT_MIDDLEWARE_STACK       │
                       │  error_mapping → otel → logger →              │
                       │  security → audit → retry → timeout →         │
                       │  hardening → [NEW] node_io_validation →        │
                       │  user_fn                                       │
                       └──────────────┬───────────────────────┘
                                      │ validate_node_input() / validate_node_output()
                                      ▼
                       ┌──────────────────────────────────────┐
                       │   node_schema.py (new, this phase)    │
                       │  BaseModel.model_validate(subset,      │
                       │    strict=False, extra ignored)        │
                       └──────────────┬───────────────────────┘
                                      │ on failure
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
              mode="warn"                        mode="enforce"
        log + OTel counter                  raise NodeValidationError
        + pass through unchanged            (caught by error_mapping_middleware,
                                             which already knows NodeExecutionError)
```

### 3.3 Components by Module

#### NTS1 — `NodeIOSchema` contract types (`node_schema.py`)

New module, mirroring the style of `agents/extension/ports.py` (module docstring establishing the pattern) and `budget/types.py` (frozen dataclasses as value objects):

```python
NodeIODirection = Literal["input", "output"]

@dataclass(frozen=True)
class NodeIOValidationResult:
    """Outcome of validating one side of a node's I/O contract."""
    ok: bool
    node_name: str
    direction: NodeIODirection
    errors: list[str]          # human-readable field-level messages; never field *values*


def validate_node_input(state: AgentState, model: type[BaseModel] | None, *, node_name: str) -> NodeIOValidationResult: ...
def validate_node_output(state_update: dict[str, Any], model: type[BaseModel] | None, *, node_name: str) -> NodeIOValidationResult: ...
```

Both helpers are pure and never raise — a `None` model is a trivial `ok=True` shortcut (this is what makes the whole feature a true no-op for un-annotated nodes). When a model is given, the helper narrows the relevant mapping (`state` for input, `state_update` for output) down to the keys the model declares, then calls `model.model_validate(narrowed, ...)`; `pydantic`'s own field-level errors become `NodeIOValidationResult.errors` (field name + message only — see §6 on never leaking values).

**Convention:** a declared model's field names are a literal 1:1 subset of `AgentState`'s keys (no separate mapping layer). E.g. an output model for `coder_node` would declare `current_agent: str` and `messages: list[BaseMessage]` — nothing else — matching exactly the two keys `coder_node` actually returns today. This keeps the contract legible without requiring anyone to mirror all 21 `AgentState` fields.

#### NTS2 — Middleware + builder wiring

`_middleware.py`: `node_io_validation_middleware` is appended as the **new innermost** entry of `DEFAULT_MIDDLEWARE_STACK`, i.e. it wraps `user_fn` directly, one layer inside `hardening_middleware` — the same insertion point `hardening_middleware` itself used when Phase H extended the Phase X chain:

```
DEFAULT_MIDDLEWARE_STACK: list[Middleware] = [
    error_mapping_middleware,
    otel_middleware,
    logger_middleware,
    security_middleware,
    audit_middleware,
    retry_middleware,
    timeout_middleware,
    hardening_middleware,
    node_io_validation_middleware,   # ← [NEW] innermost, wraps user_fn directly
]
```

```python
async def node_io_validation_middleware(next_fn, state, metadata):
    settings = get_settings()
    if not settings.node_typesafety_enabled:
        return await next_fn(state)              # complete passthrough — the disabled path

    mode = settings.node_typesafety_mode          # "off" | "warn" | "enforce" (per-call effective mode
                                                   # may later be overridden per-node via metadata.extra,
                                                   # see Open Questions)
    if mode != "off" and metadata.input_model is not None:
        result = validate_node_input(state, metadata.input_model, node_name=metadata.name)
        _handle(result, mode)                     # log + counter always; raise only if mode == "enforce"

    state_update = await next_fn(state)

    if mode != "off" and metadata.output_model is not None:
        result = validate_node_output(state_update, metadata.output_model, node_name=metadata.name)
        _handle(result, mode)

    return state_update
```

`_handle()` is the single choke point that (a) always logs + increments the OTel counter on failure, and (b) raises `NodeValidationError(node_name, state_keys, schema_errors=result.errors, direction=result.direction, cause=...)` only when `mode == "enforce"`. This mirrors `security_middleware`'s existing `if metadata.security == "off": ... elif == "strict": ...` dispatch idiom.

`builder.py`: `PrismalStateGraphBuilder.add_node()` gains `input_model: type[BaseModel] | None = None, output_model: type[BaseModel] | None = None`, forwarded to `prismal_node(...)` in the exact same `param if param is not None else self._defaults.X` fallback used today for `security`/`audit`/`timeout_s`/`retry`. `BuilderDefaults` is **not** extended with I/O-model defaults (a shared default model across an entire subgraph makes no sense — each node's contract is necessarily distinct), so these two kwargs have no builder-level default, only `None`.

Consistent with existing builder behavior: if `fn` already carries `__prismal_node__` (already decorated), `add_node()`'s kwargs — including the two new ones — are silently ignored, exactly as `security`/`audit`/`timeout_s`/`retry` are today. No special-casing needed.

#### NTS3 — Incremental adoption + `AgentState` evolution

**Adoption ordering (highest blast-radius first):** the pilot set for this phase is nodes whose writes have security or cross-cutting consequences if silently wrong:

1. `prismal/agents/file_manager.py` — writes files; a wrong `metadata` key could bypass `ActionInterceptor`/`filesystem_guard` review downstream.
2. `prismal/agents/cron_manager.py` — schedules recurring work; a malformed write could silently register or fail to register a job.
3. `prismal/agents/skill_manager.py` — loads dynamic skills; contract failure here is adjacent to code-execution risk.

(Optional 4th/5th if time allows: `codeact_agent`, `cua_agent` — both execute code/actions inside `SandboxExecutor`.)

The general 26-node population (e.g. `coder`, `researcher`, `critic` — the three grounded in this spec's research) is **not** migrated in Phase NTS; they remain unannotated, behaving identically to today, and are candidates for the NTS.1+ follow-up once the pattern is proven.

**`AgentState` evolution — DD-NTS-003 (see §4):** the chosen design for v1 is that `AgentState` itself is **not** modified or subclassed. `input_model`/`output_model` are independent `BaseModel` projections validated only at the `@prismal_node` boundary; they never become the graph's `StateGraph(...)` constructor argument. This avoids the much larger, out-of-scope effort of migrating `add_messages`/`operator.add` reducer semantics onto a Pydantic-native state object, which the existing `TypedDict`-based `StateGraph[AgentState]` (`agents/graph.py`) was not designed for and which every one of the 26+ existing nodes would need to be re-verified against.

#### NTS4 — Settings, exceptions, OTel, tests, docs

`core/config.py` — two new fields following the `hardening_mode`/`identity_mode` idiom exactly:

```python
node_typesafety_enabled: bool = Field(
    default=False,
    description="Master opt-in for per-node I/O schema validation (Phase NTS).",
)
node_typesafety_mode: str = Field(
    default="warn",
    description="Global default control mode: off | warn | enforce.",
)
```

with a new `_validate_node_typesafety` `model_validator(mode="after")` rejecting an unknown mode, copying `_validate_hardening`'s pattern (raise `ValueError` naming the exact `PRISMAL_NODE_TYPESAFETY_MODE=<value>` env var).

`core/exceptions.py` — extend the **existing** stub rather than add a new class:

```python
class NodeValidationError(NodeExecutionError):
    """Raised when a node's declared input_model/output_model rejects the state (SPEC-NTS-ERR-001).

    Args (in addition to NodeExecutionError's node_name/state_keys/cause):
        direction: "input" | "output" — which side of the contract failed.
        schema_errors: Field-level messages (never field values).
    """
    def __init__(self, node_name, state_keys, cause, *, direction, schema_errors) -> None:
        self.direction = direction
        self.schema_errors = schema_errors
        super().__init__(node_name, state_keys, cause)
```

`monitoring/otel.py::_register_standard_metrics()` — new block following the existing per-phase comment convention:

```python
# Node I/O Type-Safety (Phase NTS — SPEC-NTS-OTEL-001)
self._counters["node_io_validation_failures"] = self._meter.create_counter(
    "prismal.node_io_validation_failures_total",
    description="Node I/O schema validation failures, labelled by node and direction",
)
self._counters["node_io_validated"] = self._meter.create_counter(
    "prismal.node_io_validated_total",
    description="Node I/O schema validations attempted (success or failure), labelled by node and direction",
)
```

### 3.4 Detailed Data Flows

#### Flow NTS-A: Node invocation with a declared `input_model`/`output_model`, `mode="warn"`

```
LangGraph dispatcher ─▶ wrapper(state)
                     ─▶ [error_mapping_mw] (outermost, catches everything downstream)
                     ─▶ [otel_mw] open span
                     ─▶ [logger_mw] bind {node_name, session_id}
                     ─▶ [security_mw] / [audit_mw] / [retry_mw] / [timeout_mw]   (unchanged)
                     ─▶ [hardening_mw]  (passthrough if hardening_enabled=False)
                     ─▶ [node_io_validation_mw]
                          ├─ if node_typesafety_enabled=False: passthrough, no-op
                          ├─ validate_node_input(state, metadata.input_model)
                          │    └─ on failure (mode=warn): log + counter++, continue unchanged
                          ├─ user_fn(state) ─▶ state_update
                          └─ validate_node_output(state_update, metadata.output_model)
                               └─ on failure (mode=warn): log + counter++, continue unchanged
                     ─▶ return state_update  (bubbles back out through the chain, unchanged)
```

#### Flow NTS-B: Node invocation with a declared `output_model`, `mode="enforce"`, output fails validation

```
... same as Flow NTS-A down to node_io_validation_mw ...
                     ─▶ validate_node_output(state_update, metadata.output_model)
                          └─ on failure (mode=enforce):
                               raise NodeValidationError(node_name, state_keys, cause,
                                                          direction="output", schema_errors=[...])
                     ─▶ [error_mapping_mw] catches NodeValidationError (is-a NodeExecutionError)
                          └─ returns {"metadata": {"error": {"node": ..., "type": "NodeValidationError",
                                                              "message": ..., "timeout": False}}}
                          (unless metadata.raise_on_error=True, in which case it propagates)
```

#### Flow NTS-C: Builder path

```
builder.add_node("file_manager", file_manager_fn,
                  input_model=FileManagerInput, output_model=FileManagerOutput)
   ├─ fn already has __prismal_node__? → no
   └─ wrapped = prismal_node(name="file_manager",
                              security=..., audit=..., timeout_s=..., retry=...,   # builder defaults
                              input_model=FileManagerInput, output_model=FileManagerOutput)(fn)
   └─ self._nodes["file_manager"] = wrapped
   (identical Flow NTS-A/B behavior at runtime — the builder is purely a registration-time convenience)
```

---

## 4. Design Decisions

### DD-NTS-001: Boundary-validation middleware, not a schema-typed `StateGraph`

- **Decision:** Node I/O contracts are validated at the `@prismal_node` middleware boundary against narrow `BaseModel` projections; `AgentState`'s `TypedDict` and `StateGraph[AgentState]`'s construction are unchanged.
- **Context:** LangGraph's reducer machinery (`add_messages`, `operator.add`) is built and tested against a `TypedDict`; replacing it with a Pydantic-native state model is a much larger, higher-risk migration than this phase's scope justifies.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Boundary-validation middleware (chosen)** | Additive; zero risk to LangGraph reducer semantics; reuses the Phase X seam | Contracts describe a *projection*, not the full graph state — some drift between `AgentState` and declared models is still possible if `AgentState` changes without updating models |
| Pydantic-native `AgentState` (replace `TypedDict`) | Single source of truth; static typing throughout | Requires re-verifying `add_messages`/`operator.add` semantics on a Pydantic model; touches all 26+ nodes and `agents/graph.py` at once — a big-bang rewrite this phase explicitly avoids |
| Per-node narrower `TypedDict` subtypes as the actual graph state | Native LangGraph, no new dependency | LangGraph's `StateGraph` constructor takes one schema per graph; per-node narrower schemas would require per-node subgraphs, not a single supervisor graph — architecturally incompatible with the current 26-node single-graph design |

- **Justification:** The chosen approach is the only one that is additive, opt-in, and does not require re-certifying the 26 existing nodes or LangGraph's reducer behavior.

### DD-NTS-002: Extend the existing `NodeValidationError` stub, do not add a new exception root

- **Decision:** `NodeValidationError(NodeExecutionError)` — already present in `core/exceptions.py` since Phase X as an empty stub — gains real `direction`/`schema_errors` fields via a custom `__init__`. No new exception hierarchy (e.g. no `NodeIOTypeSafetyError`) is introduced.
- **Context:** The stub already exists, is already a `NodeExecutionError` (so `error_mapping_middleware` already catches it for free), and its docstring ("Raised when the state_update returned by a node is not valid") already describes exactly this feature's failure mode — just narrower than intended (input-side validation didn't exist yet).
- **Consequences:** No changes needed to `error_mapping_middleware`'s except clauses; `NodeTimeoutError`'s pattern (adding fields on top of the parent's three positional args) is the template followed.

### DD-NTS-003: `AgentState` stays a single, unmodified `TypedDict`; models are boundary projections

- **Decision:** This phase does not introduce narrower per-node-family `TypedDict` subtypes of `AgentState`, and does not change `AgentState` itself. `input_model`/`output_model` declare an independent `BaseModel` whose field names happen to coincide with a subset of `AgentState`'s keys, by convention, not by inheritance or structural typing.
- **Context:** `AgentState` is shared by the single `StateGraph[AgentState]` supervisor graph plus every registered subgraph (`SubgraphDefinition`); a schema that only some nodes populate cannot become the *graph's* schema without every other node conforming too.
- **Consequences:** A field-name drift test (unit test, per `TASKS.md` `NTS4`) is required to catch the case where `AgentState` changes but a declared model is not updated — this is the practical mitigation for the residual gap DD-NTS-001's alternatives table calls out.

> **Open question:** should a follow-up phase investigate LangGraph's per-node `input`/`output` schema support (if/when the installed version — resolved dynamically via `prismal.langgraph.VERSION`, per Phase X's `DD-EXT-007` — supports declaring a narrower schema per `add_node()` call distinct from the graph-level state schema) as a v2 alternative to boundary-validation-only? Not resolved here; flagged for `ARCHITECTURE.md` §10 and for whoever picks up NTS.1.

### DD-NTS-004: `NodeIOValidatorPort` — deferred, not built in v1

- **Decision:** A hexagonal `NodeIOValidatorPort` Protocol (mirroring `ToolProviderPort`/`VectorStorePort` from Phases Y/Z) that would let a user substitute a non-Pydantic validation engine (`jsonschema`, `cerberus`, a custom rule engine) is **designed but not implemented** in Phase NTS. The default (and only, for v1) implementation is the `pydantic`-based `node_schema.py` helpers described in §3.3.
- **Context:** Every other hexagonal port in the repo (Y tool provider, Z vector store, W config source) was introduced because a *real, demonstrated* need for pluggability existed (multi-backend RAG, multi-tenant config, MCP-vs-skills-vs-stub tool sourcing). No such demonstrated need exists yet for node I/O validation — introducing the port speculatively risks the same YAGNI trap `DD-EXT-005` (extension-surface) explicitly avoided for a full DI container.
- **Consequences:** If real demand emerges (e.g. a plugin author wants `jsonschema`-based contracts), the port can be added additively later without touching `node_io_validation_middleware`'s call sites — `validate_node_input`/`validate_node_output` would become the default `NodeIOValidatorPort` implementation rather than free functions.

> **Open question:** carried into `ARCHITECTURE.md` §10 — is `NodeIOValidatorPort` worth building proactively in v1 for consistency with the Y/Z/W port pattern, or genuinely deferred until a concrete second implementation is needed? Left unresolved by this document; the PLAN's Future Considerations (§5.3) lists it as a candidate, not a commitment.

### DD-NTS-005: `warn` is the only default when the feature is enabled

- **Decision:** `node_typesafety_mode` defaults to `"warn"`, never `"enforce"`, mirroring `hardening_mode`'s and `identity_mode`'s identical default-safety rationale (`DD-HRD-004`, referenced in `specs/runtime-hardening/ARCHITECTURE.md`).
- **Context:** A brand-new, unvalidated-in-production contract layer defaulting to fail-closed risks breaking working nodes on rollout day.
- **Consequences:** Operators explicitly opt a node into `enforce` only after a `warn`-mode observation period shows zero unexpected failures — this is the documented adoption path in `docs/node-typesafety.md`.

---

## 5. Code Structure

```
prismal/
├── agents/
│   ├── state.py                          ← unchanged
│   └── extension/
│       ├── node_schema.py                ← NodeIOValidationResult, validate_node_input/output
│       ├── decorators.py                 ← + input_model/output_model on NodeMetadata & prismal_node()
│       ├── builder.py                    ← + input_model/output_model on add_node()
│       ├── _middleware.py                ← + node_io_validation_middleware
│       └── ports.py                      ← (deferred) NodeIOValidatorPort — see DD-NTS-004
│
├── core/
│   ├── config.py                         ← + node_typesafety_enabled/mode + validator
│   └── exceptions.py                     ← NodeValidationError extended
│
└── monitoring/
    └── otel.py                           ← + 2 counters

tests/
├── unit/
│   ├── agents/extension/
│   │   ├── test_node_schema.py
│   │   ├── test_decorators_io_models.py
│   │   ├── test_middleware_node_io_validation.py
│   │   └── test_builder_io_models.py
│   └── core/
│       ├── test_node_typesafety_settings.py
│       └── test_node_io_schema_field_names.py   ← AgentState drift guard (DD-NTS-003)
└── integration/
    ├── test_node_typesafety_disabled_snapshot.py
    └── test_node_typesafety_e2e.py

examples/
└── node_typesafety.py

docs/
└── node-typesafety.md
```

### Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| **Middleware Chain (extension)** | `node_io_validation_middleware` appended innermost | Reuses Phase X's chain exactly as Phase H did |
| **Value Object** | `NodeIOValidationResult` (frozen dataclass) | Mirrors `budget/types.py`'s style; immutable, summarizable |
| **Strategy** | `node_typesafety_mode ∈ {off, warn, enforce}` | Configurable failure behavior, same idiom as `hardening_mode` |
| **Null Object / No-op default** | `input_model=None`/`output_model=None` ⇒ trivial `ok=True` | Guarantees opt-in, zero-cost-when-unused semantics |
| **Fluent Builder extension** | `PrismalStateGraphBuilder.add_node(input_model=, output_model=)` | Same fallback idiom as existing kwargs |
| **Ports & Adapters (deferred)** | `NodeIOValidatorPort` (not built, DD-NTS-004) | Consistent hexagonal seam *if* pluggability is ever needed |

### Error Handling

```python
class ExtensionError(PrismalError): ...           # existing, unchanged
class NodeExecutionError(ExtensionError): ...      # existing, unchanged
class NodeTimeoutError(NodeExecutionError): ...    # existing, unchanged
class NodeValidationError(NodeExecutionError):     # existing stub, EXTENDED this phase
    direction: Literal["input", "output"]          # [NEW]
    schema_errors: list[str]                       # [NEW]
```

Policy: on `enforce`-mode failure, `node_io_validation_middleware` raises `NodeValidationError`; `error_mapping_middleware` (outermost, already existing) catches it — being a `NodeExecutionError` subclass — with zero changes to `error_mapping_middleware` itself, and maps it to `{"metadata": {"error": {...}}}` unless the node was declared with `@prismal_node(raise_on_error=True)`.

---

## 6. Security

### 6.1 Attack Surface

| Vector | Mitigation |
|---|---|
| Validation error messages leak sensitive state values | `NodeIOValidationResult.errors` and `NodeValidationError.schema_errors` carry field *names* and pydantic's own type/constraint messages only — never the offending value; mirrors `NodeExecutionError`'s existing `state_keys`-not-`state_values` convention |
| A malicious plugin node declares a permissive `output_model` to dodge scrutiny | This feature does not replace `ActionInterceptor`/`GuardrailsEngine`; a permissive schema simply means NTS provides no signal for that node — it is not a security control by itself, only a correctness aid; documented explicitly in `docs/node-typesafety.md` |
| `enforce` mode DoSes a legitimate flow via false positives | `warn` is the only default (`DD-NTS-005`); promotion to `enforce` is an explicit, per-node operator decision made after a `warn`-mode observation period |
| A declared model silently drifts from `AgentState`'s real fields after a refactor | `test_node_io_schema_field_names.py` asserts every declared model's fields are a subset of `AgentState`'s `TypedDict` keys via `get_type_hints()` |

### 6.2 Cross-cutting Rules

1. **This feature is a correctness aid, not a security control** — it does not replace any of the 5 existing security layers; it must never be documented or relied upon as one.
2. **Fail-open (`warn`) is the safe default** — `enforce` is opt-in per node, after observation.
3. **Never log or audit field values, only field names/messages** — consistent with the existing `NodeExecutionError.state_keys` convention.
4. **Pilot nodes (NTS3) are chosen for blast radius, not for ease** — security-sensitive nodes go first precisely because a silent write-corruption there has the highest downstream risk.

---

## 7. Observability

### 7.1 OTel Spans

No new spans are introduced — validation happens *inside* the existing `prismal.ext.node.<name>` span (opened by `otel_mw`, per Phase X); a validation failure sets span attributes (`node_io_validation.direction`, `node_io_validation.mode`, `node_io_validation.ok=False`) rather than opening a dedicated child span, keeping trace volume unchanged.

### 7.2 Metrics

```
# Node I/O Type-Safety (Phase NTS — SPEC-NTS-OTEL-001)
prismal.node_io_validation_failures_total{node, direction="input|output"}
prismal.node_io_validated_total{node, direction="input|output"}
```

### 7.3 Startup / Adoption Report (illustrative — planned)

```
INFO prismal.node_typesafety.summary
  enabled=true mode=warn
  nodes_with_input_model=3 nodes_with_output_model=3
  (file_manager, cron_manager, skill_manager)
```

---

## 8. Testing Strategy

| Level | Coverage | Tools | What it covers |
|---|---|---|---|
| Unit | ≥ 85% on `node_schema.py` | pytest | `validate_node_input`/`validate_node_output` — model=None shortcut, subset narrowing, error shape |
| Unit | project target (≥ 80%) | pytest | Decorator/builder accept and forward the two new kwargs; settings validator rejects an unknown mode |
| Unit | — | pytest | `test_node_io_schema_field_names.py` — every declared pilot model's fields ⊆ `AgentState` keys |
| Integration | Critical flow | pytest | `node_typesafety_enabled=False` ⇒ compiled graph snapshot unchanged (mirrors `H6-06`) |
| Integration | Critical flow | pytest + `FakeToolProvider`-style doubles | `warn` passes through on a malformed pilot-node output; `enforce` raises and is mapped by `error_mapping_middleware` |

### Mock Strategy

- **Settings:** `Settings(node_typesafety_enabled=True, node_typesafety_mode="enforce")` constructed directly in tests (no env vars needed), following the existing `get_settings()`-override pattern used across the test suite.
- **Nodes:** simple `async def` fixtures returning deliberately malformed dicts, not real LLM-backed nodes — no `live_api` dependency.
- **OTel:** `OTelManager` mocked to assert the two new counters are incremented with the right `node`/`direction` labels.

---

## 9. Rollout Plan

### 9.1 Adoption Strategy

Phase NTS is **additive and opt-in**, following the exact rollout shape of Phase H (Runtime Hardening):

1. `node_typesafety_enabled=False` ships as the default — invisible to every current deployment.
2. The ≥ 3 pilot nodes (NTS3) are annotated as the worked, in-repo example — not a mandate for the other 23.
3. Operators who want the feature flip `node_typesafety_enabled=True` with `node_typesafety_mode="warn"` first, observe, then promote individual nodes to `enforce` (which is itself a `metadata.extra`-level per-node override candidate — flagged as an open question, not committed to v1's global-only mode).
4. Broad migration of the remaining specialist nodes is an explicit NTS.1+ follow-up, not part of this phase's Definition of Done.

### 9.2 Backward Compatibility

- Zero changes to `AgentState`, `agents/graph.py`, or any of the 26+ existing node function bodies.
- `NodeMetadata`'s two new fields default to `None` — every existing `@prismal_node(...)` call site is source- and behavior-compatible.
- `PrismalStateGraphBuilder.add_node()`'s two new kwargs default to `None` — every existing call site is unaffected.
- `NodeValidationError`'s extension only adds keyword fields with sensible construction; no existing `except NodeValidationError` (there are none yet, since the stub was never raised) is broken.

### 9.3 API Stability

Consistent with the extension surface's SemVer commitment (`specs/extension-surface/SPEC.md` "Compatibility and Versioning"): `input_model`/`output_model` are additive parameters on an already-public API (`@prismal_node`, `PrismalStateGraphBuilder.add_node()`); their introduction is a **minor** bump (`3.8.0`), not breaking.

---

## 10. Open Questions

- [ ] **`NodeIOValidatorPort` (DD-NTS-004):** build proactively in v1 for consistency with the Y/Z/W hexagonal-port pattern, or defer until a concrete second validation-engine need exists? — Owner: AI Architect, Deadline: start of NTS1.
- [ ] **Per-node mode override:** should an individual node be able to override the global `node_typesafety_mode` (e.g. via `@prismal_node(node_typesafety_mode="enforce")`, analogous to how `security`/`audit`/`timeout_s` are already per-node overridable), or is a single global mode sufficient for v1? — Owner: Tech Lead, Deadline: start of NTS2.
- [ ] **LangGraph native per-node schemas:** should a future phase investigate whether the installed LangGraph version supports declaring a narrower `input`/`output` schema per `add_node()` call (distinct from the graph-level state schema) as a v2 alternative to boundary-validation-only (DD-NTS-003)? — Owner: AI Architect, Deadline: NTS.1 kickoff.
- [ ] **Sampling for high-throughput deployments:** is per-call validation overhead (even in `warn` mode) acceptable at scale, or does a future phase need a `node_typesafety_sample_rate` to validate only 1-in-N invocations? — Owner: Tech Lead, Deadline: post-pilot review.
- [ ] **CI enforcement for new nodes:** once broad adoption is proven, should CI reject a new node PR that declares no schema at all? Deliberately not decided here — premature for a feature that hasn't shipped yet. — Owner: Tech Lead, Deadline: NTS.1+.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #5) and README Roadmap item 8 |
