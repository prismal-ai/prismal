# Node I/O Type-Safety (Phase NTS)

`AgentState` is a 21-field `TypedDict` that LangGraph merges across every
specialist node. That gives the graph its reducer semantics but **no guarantee**
about what an individual node reads from or writes to state — a node that means
to write `current_agent` but writes `current_agend`, or returns the wrong type,
fails silently.

Phase NTS adds an **opt-in, per-node** contract layer: a node may declare a
narrow Pydantic `input_model`/`output_model` describing the subset of
`AgentState` it consumes/produces. Validation runs as the innermost stage of the
existing `@prismal_node` middleware chain. With the master flag
`node_typesafety_enabled=False` (the default), the compiled supervisor graph is
byte-for-byte unchanged and the layer is a pure no-op.

This is a **correctness aid, not a security control** — it never replaces any of
the five security layers. A permissive schema simply provides no signal.

| Setting | Default | Meaning |
|---|---|---|
| `node_typesafety_enabled` | `False` | Master opt-in. When `False`, the middleware is a pure passthrough. |
| `node_typesafety_mode` | `warn` | `off` \| `warn` \| `enforce` (see below). |

## Modes

- **`off`** — validation is skipped entirely, even when the feature is enabled.
- **`warn`** (default when enabled) — a failure is logged and counted
  (`prismal.node_io_validation_failures_total`) but the original state /
  state_update passes through **unmodified**. Never raises.
- **`enforce`** — a failure raises `NodeValidationError`, which the existing
  `error_mapping_middleware` maps to a `metadata.error` update (graceful
  degradation) — unless the node was declared `@prismal_node(raise_on_error=True)`,
  in which case it propagates.

`warn` is the only default when the feature is on: a brand-new contract layer
that defaulted to fail-closed would risk breaking working nodes on rollout day.

## Declaring a contract

A model's field names are a literal 1:1 subset of `AgentState`'s keys — a
**narrow projection**, not an exhaustive mirror. Declare only the fields the node
actually cares about; keys the state has that the model does not declare are
never inspected.

```python
from pydantic import BaseModel
from prismal.agents.extension import prismal_node


class FileManagerInput(BaseModel):
    messages: list      # narrowed; full BaseMessage typing is out of scope
    session_id: str


class FileManagerOutput(BaseModel):
    current_agent: str
    messages: list


@prismal_node(
    name="file_manager",
    input_model=FileManagerInput,
    output_model=FileManagerOutput,
)
async def file_manager_node(state):
    ...
    return {"current_agent": "file_manager", "messages": [response]}
```

Through the builder (auto-wraps unless the function is already decorated):

```python
builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn, input_model=ClassifyInput, output_model=ClassifyOutput)
```

## Recommended adoption path

1. Ship with the feature **off** (the default) — invisible to every deployment.
2. Annotate high-value / security-sensitive nodes first. The in-repo pilots are
   `file_manager`, `cron_manager`, and `skill_manager` — nodes whose writes have
   cross-cutting consequences if silently wrong.
3. Turn the feature on in **`warn`** and observe
   `prismal.node_io_validation_failures_total{node,direction}` for a period.
4. Promote individual nodes to **`enforce`** only after a clean `warn` window.

```bash
export PRISMAL_NODE_TYPESAFETY_ENABLED=true
export PRISMAL_NODE_TYPESAFETY_MODE=warn
```

## Observability

| Metric | Labels | Meaning |
|---|---|---|
| `prismal.node_io_validated_total` | `node`, `direction` | Validations attempted (success or failure). |
| `prismal.node_io_validation_failures_total` | `node`, `direction` | Validation failures only. |

The ratio of the two is the per-node validation pass rate. Error messages and
audit records carry field **names** and pydantic's own type/constraint
description only — never field **values** — mirroring the `state_keys`-not-values
convention of `NodeExecutionError`.

## Scope & non-goals

- `AgentState` itself is **unchanged** — the models are boundary projections, not
  the graph's state schema (LangGraph's reducer machinery stays on the
  `TypedDict`). See `specs/node-io-typesafety/ARCHITECTURE.md` DD-NTS-003.
- Only the three pilot nodes are annotated in this phase; broad migration of the
  remaining specialist nodes is a follow-up.
- A pluggable non-Pydantic validation engine (`NodeIOValidatorPort`) is designed
  but deferred (DD-NTS-004).

See `examples/node_typesafety.py` for a runnable end-to-end demo.
