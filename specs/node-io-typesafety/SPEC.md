# Prismal Node I/O Type-Safety — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | NTS |
| **Target package version** | `3.8.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/node-io-typesafety/PLAN.md` |
| **Architecture** | `specs/node-io-typesafety/ARCHITECTURE.md` |
| **TASKS** | `specs/node-io-typesafety/TASKS.md` |

---

## Conventions

- `from __future__ import annotations` in all modules.
- Async where the surrounding middleware is async; the validation helpers themselves are sync, pure, and never raise (mirrors `agents/extension/ports.py::conforms_to`'s "never raise" convention).
- `frozen` dataclasses for value objects (`NodeIOValidationResult`), matching `budget/types.py` and `agents/extension/decorators.py::NodeMetadata`.
- No new mandatory dependencies — `pydantic` is already a core dependency.
- Custom exceptions extend the **existing** `NodeValidationError(NodeExecutionError)` stub in `core/exceptions.py`; no new exception root is introduced.
- Declared `input_model`/`output_model` field names are a literal 1:1 subset of `AgentState`'s `TypedDict` keys (no separate field-mapping layer).
- Every new setting follows the `mode ∈ {off, warn, enforce}` idiom already used by `hardening_mode`/`identity_mode`.

---

## Module Summary

| SPEC | File | Components |
|---|---|---|
| SPEC-NTS-TYP-001 | `prismal/agents/extension/node_schema.py` | `NodeIOMode`, `NodeIOValidationResult`, `validate_node_input()`, `validate_node_output()` |
| SPEC-NTS-TYP-002 | `prismal/agents/extension/decorators.py` | `NodeMetadata.input_model` / `.output_model`, `prismal_node(input_model=, output_model=)` |
| SPEC-NTS-MDW-001 | `prismal/agents/extension/_middleware.py` | `node_io_validation_middleware` |
| SPEC-NTS-BLD-001 | `prismal/agents/extension/builder.py` | `PrismalStateGraphBuilder.add_node(input_model=, output_model=)` |
| SPEC-NTS-PRT-001 | `prismal/agents/extension/ports.py` | `NodeIOValidatorPort` (designed, **deferred** — see `ARCHITECTURE.md` DD-NTS-004) |
| SPEC-NTS-CFG-001 | `prismal/core/config.py` | `node_typesafety_enabled`, `node_typesafety_mode`, `_validate_node_typesafety` |
| SPEC-NTS-ERR-001 | `prismal/core/exceptions.py` | `NodeValidationError` extension (`direction`, `schema_errors`) |
| SPEC-NTS-OTEL-001 | `prismal/monitoring/otel.py` | `node_io_validation_failures_total`, `node_io_validated_total` |

---

## SPEC-NTS-TYP-001: Node I/O Schema Validation Helpers

**File:** `prismal/agents/extension/node_schema.py` (new)

### Types

```python
from typing import Literal
from dataclasses import dataclass

NodeIOMode = Literal["off", "warn", "enforce"]
"""
'off'     — feature disabled for this call; validate_node_input/output are not invoked at all
            by node_io_validation_middleware (equivalent to no model being declared).
'warn'    — validation runs; a failure is logged + counted (prismal.node_io_validation_failures_total)
            but the original state / state_update passes through unmodified.
'enforce' — validation runs; a failure raises NodeValidationError, which error_mapping_middleware
            (existing, unmodified) maps to a state_update with metadata["error"], unless the node
            was declared with @prismal_node(raise_on_error=True).
"""

NodeIODirection = Literal["input", "output"]


@dataclass(frozen=True)
class NodeIOValidationResult:
    """Outcome of validating one side of a node's declared I/O contract.

    Attributes:
        ok: True if validation passed or no model was declared (trivial success).
        node_name: Name of the node being validated (for logging/audit correlation).
        direction: Which side of the contract was checked.
        errors: Human-readable, field-level messages. MUST NOT contain field *values* —
            only field names and pydantic's own type/constraint description — to avoid
            leaking potentially sensitive state content into logs/metrics/audit.
    """
    ok: bool
    node_name: str
    direction: NodeIODirection
    errors: list[str]
```

### Main Functions

```python
def validate_node_input(
    state: "AgentState",
    model: type[BaseModel] | None,
    *,
    node_name: str,
) -> NodeIOValidationResult:
    """Validate the subset of `state` a node's declared `input_model` describes.

    Never raises. If `model` is None, returns a trivial `ok=True` result — this is what
    makes the whole feature a true no-op for nodes that declare no contract.

    Only the keys present in `model`'s fields are read from `state` and passed to
    `model.model_validate(...)`; keys `state` has that the model does not declare are
    ignored (a node's `input_model` is a NARROW PROJECTION, not an exhaustive mirror
    of AgentState — see the Conventions section).

    Args:
        state: The AgentState the node is about to receive.
        model: The node's declared input model, or None.
        node_name: Name of the node (for the result's correlation and for logs).

    Returns:
        NodeIOValidationResult. `errors` is empty iff `ok` is True.

    Example::

        class CriticInput(BaseModel):
            iteration_count: int
            messages: list  # narrowed; full BaseMessage typing is the caller's concern

        result = validate_node_input(state, CriticInput, node_name="critic")
        if not result.ok:
            ...
    """
    ...


def validate_node_output(
    state_update: dict[str, object],
    model: type[BaseModel] | None,
    *,
    node_name: str,
) -> NodeIOValidationResult:
    """Validate the subset of a node's returned `state_update` its `output_model` describes.

    Same no-raise, no-op-when-`None`, narrow-projection semantics as
    :func:`validate_node_input`, applied to the dict a node returns rather than the
    state it received.

    Args:
        state_update: The dict returned by the node's user function.
        model: The node's declared output model, or None.
        node_name: Name of the node.

    Returns:
        NodeIOValidationResult.

    Example::

        class CriticOutput(BaseModel):
            current_agent: str
            messages: list
            iteration_count: int

        result = validate_node_output(state_update, CriticOutput, node_name="critic")
    """
    ...
```

**Field-name convention (normative):** for both functions, if `model` declares a field `f`, the helper reads `mapping.get(f, <MISSING>)` from `state`/`state_update` (a plain `dict`/`Mapping` access — `AgentState` is itself a `TypedDict`, i.e. a `dict` at runtime) and includes it in the payload passed to `model.model_validate(...)`. Fields declared by the model but absent from the mapping are surfaced as ordinary pydantic "field required" errors (unless the model marks them optional). Keys present in the mapping but *not* declared by the model are never inspected or included — a node's contract only ever describes what it explicitly cares about.

---

## SPEC-NTS-TYP-002: `@prismal_node` and `NodeMetadata` Extensions

**File:** `prismal/agents/extension/decorators.py` (modified)

### `NodeMetadata` — new fields (defaults preserve backward compatibility)

```python
@dataclass(frozen=True)
class NodeMetadata:
    """Metadata of a node registered via @prismal_node (existing dataclass, extended)."""
    name: str
    capabilities: tuple[str, ...]
    security: SecurityLevel
    audit: bool
    retry: RetryPolicy | None
    timeout_s: float | None
    raise_on_error: bool
    registered_at: str
    source_module: str
    extra: dict[str, Any] = field(default_factory=dict)

    # [NEW — Phase NTS, SPEC-NTS-TYP-002]
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
```

Both new fields default to `None`; every `NodeMetadata(...)` construction site that predates this phase remains valid without modification (the dataclass is only ever constructed internally by `prismal_node()`, so no external code is affected).

### `prismal_node()` — two new keyword-only parameters

```python
def prismal_node(
    *,
    name: str | None = None,
    capabilities: list[str] | None = None,
    security: SecurityLevel = "standard",
    audit: bool = True,
    retry: RetryPolicy | None = None,
    timeout_s: float | None = None,
    raise_on_error: bool = False,
    input_model: type[BaseModel] | None = None,      # [NEW]
    output_model: type[BaseModel] | None = None,      # [NEW]
) -> Callable[[NodeFn], NodeFn]:
    """Decorator that wraps a LangGraph node with prismal cross-cutting concerns.

    Extends the existing middleware chain (see CLAUDE.md: error_mapping → otel → logger
    → security → audit → retry → timeout → user fn) with a NEW innermost stage,
    node_io_validation, active only when settings.node_typesafety_enabled=True:

        ... → timeout → hardening → [NEW] node_io_validation → user fn

    Args:
        (existing args unchanged)
        input_model: Optional Pydantic model describing the subset of AgentState this
            node reads. None (default) disables input validation for this node —
            existing nodes are unaffected unless they opt in.
        output_model: Optional Pydantic model describing the subset of the returned
            state_update this node produces. None (default) disables output validation.

    Behavior when settings.node_typesafety_enabled=False (the global default):
        input_model/output_model are stored on NodeMetadata but never consulted —
        node_io_validation_middleware short-circuits to a pure passthrough. This is
        what guarantees a byte-for-byte-unchanged compiled graph when the feature
        is off (SPEC-NTS-MDW-001, RF-NTS-008).

    Example::

        from pydantic import BaseModel
        from prismal.agents.extension import prismal_node

        class FileManagerOutput(BaseModel):
            current_agent: str
            metadata: dict

        @prismal_node(name="file_manager", output_model=FileManagerOutput)
        async def file_manager_node(state):
            ...
            return {"current_agent": "file_manager", "metadata": {"file_manager": {...}}}
    """
    ...
```

---

## SPEC-NTS-MDW-001: `node_io_validation_middleware`

**File:** `prismal/agents/extension/_middleware.py` (modified)

```python
# DEFAULT_MIDDLEWARE_STACK, updated (outermost → innermost; unchanged entries omitted for brevity):
DEFAULT_MIDDLEWARE_STACK: list[Middleware] = [
    error_mapping_middleware,
    otel_middleware,
    logger_middleware,
    security_middleware,
    audit_middleware,
    retry_middleware,
    timeout_middleware,
    hardening_middleware,
    node_io_validation_middleware,   # [NEW — Phase NTS] innermost; wraps user_fn directly
]


async def node_io_validation_middleware(
    next_fn: NodeFn,
    state: "AgentState",
    metadata: NodeMetadata,
) -> dict[str, object]:
    """Validate a node's declared input_model/output_model around its invocation.

    Complete passthrough (no-op) when settings.node_typesafety_enabled is False — this
    is the byte-for-byte-unchanged guarantee (RF-NTS-008). When enabled:

        mode = settings.node_typesafety_mode   # "off" | "warn" | "enforce"

        if mode != "off" and metadata.input_model is not None:
            result = validate_node_input(state, metadata.input_model, node_name=metadata.name)
            _observe(result)                        # OTel counter + structured log, always
            if not result.ok and mode == "enforce":
                raise NodeValidationError(
                    metadata.name, list(state.keys()), cause=None,
                    direction="input", schema_errors=result.errors,
                )

        state_update = await next_fn(state)

        if mode != "off" and metadata.output_model is not None:
            result = validate_node_output(state_update, metadata.output_model, node_name=metadata.name)
            _observe(result)
            if not result.ok and mode == "enforce":
                raise NodeValidationError(
                    metadata.name, list(state_update.keys()), cause=None,
                    direction="output", schema_errors=result.errors,
                )

        return state_update

    Raises:
        NodeValidationError: When mode == "enforce" and either side of the declared
            contract fails validation. Caught by error_mapping_middleware (outermost,
            unmodified by this phase) exactly like any other NodeExecutionError.
    """
    ...


def _observe(result: NodeIOValidationResult) -> None:
    """Log (on failure) + increment the appropriate OTel counter. Never raises."""
    ...
```

**Ordering contract (normative):** `node_io_validation_middleware` MUST be the innermost entry of `DEFAULT_MIDDLEWARE_STACK` — i.e. it wraps `user_fn` directly, with no other middleware between it and the user's function. This guarantees that input validation observes exactly the `state` the user function will receive, and output validation observes exactly what the user function returned, before any other middleware (audit, retry, etc.) has a chance to see or alter it.

---

## SPEC-NTS-BLD-001: `PrismalStateGraphBuilder.add_node()` Extension

**File:** `prismal/agents/extension/builder.py` (modified)

```python
def add_node(
    self,
    name: str,
    fn: NodeFn,
    *,
    capabilities: list[str] | None = None,
    security: SecurityLevel | None = None,
    audit: bool | None = None,
    timeout_s: float | None = None,
    retry: RetryPolicy | None = None,
    input_model: type[BaseModel] | None = None,      # [NEW]
    output_model: type[BaseModel] | None = None,      # [NEW]
) -> PrismalStateGraphBuilder:
    """Add a node, auto-wrapping with @prismal_node when needed (existing method, extended).

    input_model/output_model follow the same "forwarded only if fn is not already
    @prismal_node-decorated" rule as every other kwarg here: if `fn` already carries
    __prismal_node__, ALL kwargs to this call — including the two new ones — are
    ignored, consistent with existing behavior for security/audit/timeout_s/retry.

    Args:
        (existing args unchanged)
        input_model: Forwarded to prismal_node(input_model=...) when auto-wrapping.
        output_model: Forwarded to prismal_node(output_model=...) when auto-wrapping.

    Raises:
        ValueError: If ``name`` is already registered in this builder (unchanged).

    Example::

        builder = PrismalStateGraphBuilder("my_pipeline")
        builder.add_node(
            "classify", classify_fn,
            capabilities=["general"],
            input_model=ClassifyInput, output_model=ClassifyOutput,
        )
    """
    ...
```

`BuilderDefaults` is **not** extended with I/O-model fields — there is no meaningful subgraph-wide default schema, only a per-node one (see `ARCHITECTURE.md` §3.3, NTS2).

---

## SPEC-NTS-PRT-001: `NodeIOValidatorPort` (designed, deferred)

**File:** `prismal/agents/extension/ports.py` (not modified in v1 — see `ARCHITECTURE.md` DD-NTS-004)

This Protocol is specified here for completeness and future reference, but is **not implemented in Phase NTS**. It is documented so a future phase can add it additively without redesigning the seam.

```python
@runtime_checkable
class NodeIOValidatorPort(Protocol):
    """Pluggable node I/O validation engine (NOT built in Phase NTS — see DD-NTS-004).

    The default (and only, for v1) implementation is the pydantic-based
    node_schema.validate_node_input/validate_node_output pair, used directly as free
    functions rather than through this Protocol. If a real need for a non-Pydantic
    validation engine (jsonschema, cerberus, a custom rule engine) emerges, this
    Protocol formalizes the substitution point without touching
    node_io_validation_middleware's call sites.
    """
    def validate_input(
        self, state: "AgentState", model: Any, *, node_name: str
    ) -> "NodeIOValidationResult": ...

    def validate_output(
        self, state_update: dict[str, object], model: Any, *, node_name: str
    ) -> "NodeIOValidationResult": ...
```

> **Open question (carried from `ARCHITECTURE.md` DD-NTS-004):** should this Protocol be built proactively in v1 for consistency with the `ToolProviderPort`/`VectorStorePort` pattern, or deferred until a concrete second implementation is needed? Not resolved by this SPEC.

---

## Exceptions

**File:** `prismal/core/exceptions.py` (extension of the existing stub)

```python
class NodeExecutionError(ExtensionError):
    """Existing, unmodified. Error captured during execution of a decorated node."""
    node_name: str
    state_keys: list[str]
    cause: BaseException


class NodeValidationError(NodeExecutionError):
    """Raised when a node's declared input_model/output_model rejects the state
    (SPEC-NTS-ERR-001). Existing stub, extended this phase with real fields.

    Args:
        node_name: Name of the node whose contract failed.
        state_keys: Keys present in the state/state_update at the time of failure
            (never values — see the "never leak values" convention).
        cause: The underlying pydantic ValidationError, or None if none applies.
        direction: "input" | "output" — which side of the contract failed.
        schema_errors: Field-level messages (field names + pydantic's own
            type/constraint description; never field values).
    """
    direction: Literal["input", "output"]
    schema_errors: list[str]

    def __init__(
        self,
        node_name: str,
        state_keys: list[str],
        cause: BaseException | None,
        *,
        direction: Literal["input", "output"],
        schema_errors: list[str],
    ) -> None:
        self.direction = direction
        self.schema_errors = schema_errors
        super().__init__(node_name, state_keys, cause)
```

No new exception root is introduced; `NodeValidationError` remains a `NodeExecutionError`, so `error_mapping_middleware`'s existing `except NodeExecutionError` clause requires **zero modification** to catch it.

---

## Settings (Node I/O Type-Safety)

**File:** `prismal/core/config.py`

```python
node_typesafety_enabled: bool = Field(
    default=False,
    description="Master opt-in for per-node I/O schema validation (Phase NTS). "
                "When False, node_io_validation_middleware is a pure passthrough and "
                "the compiled supervisor graph is byte-for-byte unchanged.",
)
node_typesafety_mode: str = Field(
    default="warn",
    description="Global default control mode: off | warn | enforce.",
)
```

Validator, mirroring `_validate_hardening`:

```python
@model_validator(mode="after")
def _validate_node_typesafety(self) -> "Settings":
    """Reject an unknown node_typesafety_mode at load time (SPEC-NTS-CFG-001)."""
    valid_modes = {"off", "warn", "enforce"}
    if self.node_typesafety_mode not in valid_modes:
        raise ValueError(
            f"PRISMAL_NODE_TYPESAFETY_MODE={self.node_typesafety_mode!r} is invalid; "
            f"expected one of {sorted(valid_modes)}."
        )
    return self
```

Env vars:

```
PRISMAL_NODE_TYPESAFETY_ENABLED=true
PRISMAL_NODE_TYPESAFETY_MODE=warn
```

---

## OTel Counters

**File:** `prismal/monitoring/otel.py::_register_standard_metrics()`

```python
# Node I/O Type-Safety (Phase NTS — SPEC-NTS-OTEL-001)
self._counters["node_io_validation_failures"] = self._meter.create_counter(
    "prismal.node_io_validation_failures_total",
    description="Node I/O schema validation failures, labelled by node and direction",
)
self._counters["node_io_validated"] = self._meter.create_counter(
    "prismal.node_io_validated_total",
    description="Node I/O schema validations attempted (success or failure), "
                 "labelled by node and direction",
)
```

Both counters are incremented from `_observe()` (SPEC-NTS-MDW-001) with labels `{node=<metadata.name>, direction=<"input"|"output">}`; `node_io_validated_total` increments on every attempted validation (success or failure), `node_io_validation_failures_total` only on failure — the ratio of the two is the per-node validation pass rate.

---

## Worked Example: Annotating a Pilot Node

Per `PLAN.md` §5 (NTS3) and `ARCHITECTURE.md` §3.3, `file_manager` is one of the pilot nodes. A representative (illustrative, not yet implemented) annotation:

```python
# prismal/agents/file_manager.py (planned change, not yet implemented)
from pydantic import BaseModel
from prismal.agents.extension import prismal_node


class FileManagerInput(BaseModel):
    session_id: str
    messages: list  # narrowed; BaseMessage list typing is out of this model's concern


class FileManagerOutput(BaseModel):
    current_agent: str
    messages: list
    metadata: dict


@prismal_node(
    name="file_manager",
    capabilities=["filesystem"],
    input_model=FileManagerInput,
    output_model=FileManagerOutput,
)
async def file_manager_node(state: AgentState) -> dict[str, object]:
    ...
    return {"current_agent": "file_manager", "messages": [response], "metadata": {...}}
```

With `node_typesafety_enabled=False` (default), this is byte-for-byte identical in behavior to the undecorated version. With `node_typesafety_enabled=True, node_typesafety_mode="warn"`, a future refactor that accidentally omits `current_agent` from the return dict is caught and logged instead of silently breaking the supervisor's routing.

---

## Compatibility and Versioning

- `input_model`/`output_model` are additive, optional parameters on already-public API (`@prismal_node`, `PrismalStateGraphBuilder.add_node()`) — their introduction is a **minor** SemVer bump (`3.8.0`), following the same commitment documented in `specs/extension-surface/SPEC.md`.
- `NodeValidationError`'s extension only adds keyword-only fields to an existing, previously-unraised stub class — no known call site catches or constructs it today, so this is non-breaking by construction.
- No deprecation is required — nothing existing is removed or renamed.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #5) and README Roadmap item 8 |
