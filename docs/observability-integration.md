# Observability Integration (`ObservabilityPort`)

Prismal already **emits** telemetry — `OTelManager` pushes OpenTelemetry
spans/counters to a collector, `LangfuseManager` pushes traces to a Langfuse
project (`prismal/monitoring/`). What this layer adds is a **queryable,
backend-agnostic contract** over one run's telemetry: recent spans, cost/latency,
tool-call history, and the node-visit sequence — plus the concrete LangSmith /
Langfuse *parity* closes (consistent run/trace naming, a score/feedback hook, and
evaluation-dataset export).

> Specs: [`specs/observability-integration/`](../specs/observability-integration/) ·
> Example: [`examples/observability_integration.py`](../examples/observability_integration.py)

The whole layer is **opt-in**. With `observability_enabled=False` (the default)
`RuntimeContext.observability` is `None`, the compiled supervisor graph is
byte-for-byte unchanged, and **not a single existing OTel/Langfuse call site
changes** — the default adapter is additive glue *around* those singletons, never
a replacement.

## Framework vs. host — what this ships (and what it doesn't)

`prismal` is an embeddable engine with **no web server, dashboard, or CLI**
(`CLAUDE.md`, line 1). This layer ships the **framework side** only:

- the `ObservabilityPort` contract,
- the value objects it returns (`RunSummary`, `SpanRecord`, `ToolCallRecord`,
  `ScoreAnnotation`),
- a default adapter (`DefaultObservabilityProvider`) that wraps the *existing*
  OTel/Langfuse emission, and
- the parity helpers (naming, scoring, dataset export).

A literal **observability UI** — charts, a run timeline, an admin panel — belongs
to the separate **`prismal-dashboard`** repository (planned; dashed-outline in
the README architecture diagram). This repo *renders nothing*: the port returns
data, and `prismal-dashboard` (or `prismal-server`) is the consumer that builds a
UI or an API against the stable contract — exactly the way Phase R documented the
`prismal-server` lifespan before that repo existed.

## Quick start

```bash
export PRISMAL_OBSERVABILITY_ENABLED=true
export PRISMAL_OBSERVABILITY_RUN_BUFFER_SIZE=200   # spans/tool-calls kept per run
export PRISMAL_OBSERVABILITY_MAX_RUNS=500          # runs kept before LRU eviction
```

```python
from prismal.composition import build_runtime

ctx = await build_runtime(settings)          # observability composed when enabled
run_id = ctx.observability.get_run_summary(run_id)   # query a run
```

## Concepts

| Piece | Role |
|---|---|
| `ObservabilityPort` | The backend-agnostic contract: `record_node`, `record_score`, `get_run_summary`, `export_dataset`. `record_node`/`record_score` are sync and **never raise** (fail-open). |
| `RunSummary` | Queryable snapshot: `visited_nodes`, `spans`, `tool_calls`, `usage` (reused `budget.types.Usage`), `latency_ms`, `scores`. Structural data only — never raw prompt/response content. |
| `DefaultObservabilityProvider` | Glue over `OTelManager` + `LangfuseManager` with a bounded in-memory ring buffer per run. Ships useful *before* any dashboard exists. |
| `FakeObservabilityProvider` | Deterministic, I/O-free test double. |
| `observability_resolve` | Per-run registry (`seed_/get_/clear_observability_run`) — the live provider stays **out of checkpointed state**, exactly like the Budget per-run registry. |

### Best-effort, non-durable by contract

`get_run_summary(run_id)` returns `None` for an unknown or evicted run. The
default adapter keeps a **bounded, in-memory** ring buffer (`run_buffer_size`
spans/tool-calls per run; `max_runs` runs with LRU eviction) — a process restart
loses it, exactly like an unflushed OTel batch. A durable, indexed store (paging,
tenant filtering) is deliberately left to whichever component needs it — most
naturally `prismal-dashboard`, or a future adapter implementing the **same port**
over a real database. The contract stays stable regardless of the backend.

## Naming convention (single source of truth)

Both LangSmith and Langfuse dashboards group/filter by run name and tags in their
default views. One place derives them:

```python
from prismal.monitoring.observability import run_name_for, trace_tags_for

run_name_for(agent_name="coder", session_id="sess-1", turn=0)   # "coder.sess-1.turn0"
trace_tags_for(agent_name="coder", node="planner", org_id="acme")
# ["agent:coder", "node:planner", "org:acme"]
```

Every call site — the default adapter, `LangfuseManager.create_trace(name=...)`,
and any LangSmith-side integration a host wires — derives its name/tags from these
two functions, never ad hoc.

## Score / feedback hook

`record_score` attaches a named score to a specific `run_id`. It stores a local
`ScoreAnnotation` **and** forwards to `LangfuseManager.score_trace` (keyed by the
canonical `run_id` as the trace id). It never re-injects its `comment` into a live
prompt — it is a terminal, one-way write.

### Eval-harness LLM-judge integration pattern

The Phase V eval harness (`prismal/eval/`) is **not modified** by this layer, but
its LLM-judge output plugs into `record_score` cleanly. When a run's `run_id` is
available, forward each judge verdict as a score:

```python
# host / eval glue — NOT part of prismal/eval/ itself
from prismal.eval.judges import GroundednessJudge   # Phase V, unchanged

verdict = GroundednessJudge().score(trajectory)      # -> 0..1
ctx.observability.record_score(
    run_id=run_id,
    name="llm_judge:groundedness",
    value=verdict,
    source="llm_judge",
)
```

A human reviewer uses the same hook with `source="human"` (e.g. a
`prismal-server` endpoint calling `ctx.observability.record_score(...)`).

## Dataset export

`export_dataset(run_ids, fmt=...)` serializes runs into each vendor's
evaluation-dataset import shape (LangSmith snake_case `inputs`/`outputs`;
Langfuse camelCase `input`/`expectedOutput`). It operates on **already-captured**
data — it never re-executes the graph or makes new LLM calls. The default adapter
carries structural data only, so `inputs`/`outputs` are `None` unless a richer
adapter attaches content.

```python
from prismal.monitoring.observability_types import DatasetFormat

ctx.observability.export_dataset([run_id], fmt=DatasetFormat.LANGSMITH)
ctx.observability.export_dataset([run_id], fmt=DatasetFormat.LANGFUSE)
```

## Observability of observability (OTel counters)

Registered in `OTelManager._register_standard_metrics()`:

- `prismal.observability_runs_total{result}` — `result ∈ completed|evicted`
- `prismal.observability_scores_total{name}`
- `prismal.observability_dataset_exports_total{fmt}` — `fmt ∈ langsmith|langfuse`

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `observability_enabled` | `False` | Master opt-in toggle. |
| `observability_run_buffer_size` | `200` | Max spans/tool-calls retained per run. |
| `observability_max_runs` | `500` | Max concurrent runs before LRU eviction. |
| `observability_score_source_default` | `"system"` | Default `source` for `record_score`. |
| `observability_dataset_export_format` | `"langsmith"` | Default `fmt` for `export_dataset`. |

Env prefix `PRISMAL_` (e.g. `PRISMAL_OBSERVABILITY_ENABLED`). An unknown
`observability_dataset_export_format` / `observability_score_source_default`
fails fast at load time.
