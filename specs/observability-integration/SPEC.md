# Prismal Observability Integration — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | OBS |
| **Target package version** | `3.9.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/observability-integration/PLAN.md` |
| **Architecture** | `specs/observability-integration/ARCHITECTURE.md` |
| **TASKS** | `specs/observability-integration/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Frozen dataclasses for value objects (mirrors `budget/types.py`, `eval/types.py`).
- Constructors accept `settings: Settings | None = None`.
- `ObservabilityPort` methods that sit on the hot node/tool-call path (`record_node`, `record_score`) are **sync and must not raise** (mirrors `ToolProviderPort.get_tools` and `AuditPort`'s methods) — a failing/unreachable backend degrades to a logged warning, never an exception into the graph.
- `get_run_summary` and `export_dataset` may do best-effort backend lookups but must also never raise; unknown input returns `None` / an empty list respectively.
- No provider SDK import outside `prismal/providers/`; no `prismal.mcp` / `prismal.skills` import inside `prismal/agents/**`.
- All checkpoint-safe observability state lives under `state["metadata"]["observability"]` as a serializable marker only (`{"enabled": bool, "run_id": str}`); live provider/registry state is never checkpointed (mirrors Budget/Skynet/Kokoro/Hardening).
- `observability_enabled=False` ⇒ zero wiring observable (`RuntimeContext.observability is None`; no new call reaches `OTelManager`/`LangfuseManager`).

---

## Module Summary

| Module | Status | Purpose |
|---|---|---|
| `prismal/monitoring/observability_types.py` | NEW | `RunSummary`, `SpanRecord`, `ToolCallRecord`, `ScoreAnnotation`, `DatasetFormat` |
| `prismal/monitoring/observability.py` | NEW | `DefaultObservabilityProvider`, `FakeObservabilityProvider`, `run_name_for`, `trace_tags_for` |
| `prismal/monitoring/observability_resolve.py` | NEW | `seed_observability_run`, `get_observability_provider`, `clear_observability_run` |
| `prismal/agents/extension/ports.py` | MODIFIED | `+ ObservabilityPort` |
| `prismal/composition/runtime.py` | MODIFIED | `+ RuntimeContext.observability`; `build_runtime()` composition step |
| `prismal/core/config.py` | MODIFIED | `observability_*` settings |
| `prismal/core/exceptions.py` | MODIFIED | `ObservabilityError` hierarchy |
| `prismal/monitoring/otel.py` | MODIFIED | OBS counters |

---

## SPEC-OBS-TYP-001: Value objects (`monitoring/observability_types.py`)

```python
class DatasetFormat(StrEnum):
    LANGSMITH = "langsmith"
    LANGFUSE = "langfuse"


@dataclass(frozen=True)
class SpanRecord:
    """One recorded unit of work within a run (a node visit or an LLM call)."""
    name: str                      # e.g. "coder" (node) or "coder.llm_call"
    node: str
    duration_ms: float
    status: Literal["ok", "error"] = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallRecord:
    """One recorded tool invocation within a run."""
    tool_name: str
    node: str
    ok: bool
    duration_ms: float = 0.0


@dataclass(frozen=True)
class RunSummary:
    """Queryable snapshot of one run's telemetry (SPEC-OBS-PRT-001).

    Deliberately shape-parallel to (but independent of) ``prismal.eval.types.Trajectory``
    — see ARCHITECTURE.md DD-OBS-001 for why the two are not unified in this phase.
    """
    run_id: str
    session_id: str
    agent_name: str
    visited_nodes: list[str]
    spans: list[SpanRecord]
    tool_calls: list[ToolCallRecord]
    usage: Usage                      # reused from prismal.budget.types
    latency_ms: float
    scores: list[ScoreAnnotation] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float | None = None


@dataclass(frozen=True)
class ScoreAnnotation:
    """A feedback/score attached to a run (SPEC-OBS-PAR-002)."""
    name: str
    value: float
    comment: str = ""
    source: Literal["human", "llm_judge", "system"] = "system"
```

**Acceptance:** all five types are frozen dataclasses; `RunSummary.usage` round-trips through `Usage.__add__` (reused, not reimplemented) when a run accumulates multiple LLM calls.

---

## SPEC-OBS-PRT-001: `ObservabilityPort` (`agents/extension/ports.py`)

```python
@runtime_checkable
class ObservabilityPort(Protocol):
    """Queryable surface over a run's telemetry — backend-agnostic (SPEC-OBS-PRT-001).

    Conforming implementations: ``DefaultObservabilityProvider`` (thin wrapper over
    the existing ``OTelManager``/``LangfuseManager`` singletons), ``FakeObservabilityProvider``
    (tests), and any future first-party store or host-supplied adapter (e.g. one
    ``prismal-dashboard`` implements directly over its own persistence). The core
    only calls these methods; it never renders them (no web UI belongs here — see
    PLAN.md's scope boundary).

    ``record_node`` and ``record_score`` are sync and must not raise on the hot
    path (fail-open: a backend error is logged, never propagated into the graph).
    """

    def record_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord] = (),
        usage: Usage | None = None,
    ) -> None:
        """Record one node's execution (span + its tool calls + LLM usage, if any)."""
        ...

    def record_score(
        self,
        *,
        run_id: str,
        name: str,
        value: float,
        comment: str = "",
        source: Literal["human", "llm_judge", "system"] = "system",
    ) -> None:
        """Attach a named score/feedback annotation to *run_id* (SPEC-OBS-PAR-002).

        Usable by a human reviewer or the eval-harness's LLM-judge
        (``prismal.eval.judges``) to close the "feedback/score annotation" gap.
        Never raises; an unknown ``run_id`` is a no-op (logged at debug level).
        """
        ...

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        """Return the queryable snapshot for *run_id*, or ``None`` if unknown/evicted.

        Best-effort by contract (SPEC-OBS-PRT-001 / ARCHITECTURE.md DD-OBS-006) —
        implementations are not required to persist beyond process lifetime.
        """
        ...

    def export_dataset(
        self,
        run_ids: Sequence[str],
        *,
        fmt: DatasetFormat,
    ) -> list[dict[str, Any]]:
        """Export the given runs as evaluation-dataset records for *fmt*.

        Never raises; runs with no summary are skipped. See SPEC-OBS-PAR-003 for
        the per-format record shape.
        """
        ...
```

**Acceptance:** any object satisfying this shape passes `conforms_to(obj, ObservabilityPort)` (reuses the existing `conforms_to()` helper in `ports.py`); `record_node`/`record_score` never raise even when the underlying `OTelManager`/`LangfuseManager` is disabled or the backend is unreachable.

---

## SPEC-OBS-PAR-001: Naming convention (`monitoring/observability.py`)

```python
def run_name_for(*, agent_name: str, session_id: str, turn: int) -> str:
    """Return the canonical run/trace name: ``f"{agent_name}.{session_id}.turn{turn}"``.

    Single source of truth consumed by DefaultObservabilityProvider,
    LangfuseManager.create_trace(name=...), and any LangSmith-side integration a
    host wires. Both dashboards group/filter by this string in their default
    views — it is stable, deterministic, and free of PII (session_id is already
    an opaque identifier elsewhere in the codebase).
    """


def trace_tags_for(*, agent_name: str, node: str | None = None, org_id: str | None = None) -> list[str]:
    """Return the canonical tag set: ``["agent:<agent_name>"]`` plus optional
    ``"node:<node>"`` / ``"org:<org_id>"``. Used for Langfuse ``tags`` and as
    LangSmith run metadata/tags.
    """
```

**Acceptance:** `run_name_for`/`trace_tags_for` are pure functions (no I/O); the same `(agent_name, session_id, turn)` always yields the same name; `DefaultObservabilityProvider` and (per DD-OBS-004) any future `LangfuseManager.create_trace` call site derive their `name`/`tags` exclusively from these two functions.

## SPEC-OBS-PAR-002: Score/feedback hook

Covered by `ObservabilityPort.record_score` above (SPEC-OBS-PRT-001). Integration points:
- **Human reviewer:** a host (`prismal-server`) exposes an endpoint that calls `ctx.observability.record_score(run_id=..., name="human_review", value=..., source="human")`.
- **Eval-harness LLM-judge:** `prismal.eval.judges` (Phase V, unmodified in this phase) can — as a *documented integration pattern*, not a hard-wired change — call `record_score(run_id=..., name=f"llm_judge:{rubric}", value=score, source="llm_judge")` when a `RunSummary`'s `run_id` is available. This spec documents the pattern in `docs/observability-integration.md`; it does not modify `prismal/eval/` (see PLAN § 5.2, ARCHITECTURE DD-OBS-001 open question).

## SPEC-OBS-PAR-003: Dataset export (`export_dataset`)

Per-format record shape, both derived from the same internal `RunSummary`/`ScoreAnnotation` data:

```python
# fmt = DatasetFormat.LANGSMITH — matches LangSmith's example/dataset import shape
{
    "inputs": {"question": <first user-turn content or None>},
    "outputs": {"answer": <final assistant content or None>},
    "reference_outputs": {},          # populated when an eval-harness EvalCase.expected is available
    "metadata": {"run_id": ..., "agent_name": ..., "session_id": ...},
}

# fmt = DatasetFormat.LANGFUSE — matches Langfuse's dataset-item import shape
{
    "input": <first user-turn content or None>,
    "expectedOutput": <eval-harness expected value, if available, else None>,
    "metadata": {"runId": ..., "agentName": ..., "sessionId": ...},
}
```

**Acceptance:** `export_dataset([run_id], fmt=DatasetFormat.LANGSMITH)` returns exactly one record per known `run_id` with the `LANGSMITH` shape above; the `LANGFUSE` shape uses Langfuse's camelCase metadata convention (consistent with the `a2a/types.py` precedent of matching a vendor's wire convention exactly, e.g. `to_camel` aliasing).

---

## SPEC-OBS-CMP-001: Composition (`composition/runtime.py`)

```python
@dataclass
class RuntimeContext:
    ...  # existing fields unchanged
    observability: ObservabilityPort | None = None   # NEW — None unless observability_enabled
```

`build_runtime()` gains one more opt-in composition step, inserted alongside the existing `identity_enabled`/`a2a_enabled` blocks:

```python
observability: ObservabilityPort | None = None
if eff.observability_enabled:
    try:
        from prismal.monitoring.observability import DefaultObservabilityProvider
        observability = DefaultObservabilityProvider(settings=eff)
    except Exception as exc:
        raise RuntimeCompositionError("observability", str(exc)) from exc
```

**Acceptance:** `build_runtime(settings)` with `observability_enabled=False` (default) produces a `RuntimeContext` with `observability is None` and does not import `prismal.monitoring.observability` at all (deferred import inside the `if`); with `observability_enabled=True` it produces a working `DefaultObservabilityProvider` without raising, exactly mirroring the existing `identity_enabled` block's error-handling shape (`RuntimeCompositionError("observability", ...)` on failure).

---

## SPEC-OBS-RES-001: Per-run registry (`monitoring/observability_resolve.py`)

```python
def seed_observability_run(
    session_id: str, provider: ObservabilityPort, *, agent_name: str, turn: int
) -> str:
    """Install *provider* in the in-process registry keyed by session_id; return
    the derived run_id (run_name_for(...)). Idempotent per user-turn — mirrors
    budget/resolve.py::seed_budget_run."""

def get_observability_provider(session_id: str) -> ObservabilityPort | None: ...

def clear_observability_run(session_id: str) -> None: ...
```

**Acceptance:** calling `seed_observability_run` twice with the same `(session_id, turn)` returns the same `run_id` and does not create a second registry entry (idempotency, matching Budget's turn-scope default).

---

## SPEC-OBS-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `observability_enabled` | `bool` | `False` | Master opt-in toggle |
| `observability_run_buffer_size` | `int` | `200` | Max spans/tool-calls retained per run in the default adapter's ring buffer |
| `observability_max_runs` | `int` | `500` | Max concurrent run entries retained by the default adapter before LRU eviction |
| `observability_score_source_default` | `str` | `"system"` | Default `source` for `record_score` when the caller omits it |
| `observability_dataset_export_format` | `str` | `"langsmith"` | Default `fmt` for `export_dataset` when the caller omits it |

Env prefix `PRISMAL_` (e.g. `PRISMAL_OBSERVABILITY_ENABLED`). `_validate_observability` rejects an unknown `observability_dataset_export_format`/`observability_score_source_default` at load time (mirrors `_validate_hardening`/`_validate_budget`).

## SPEC-OBS-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class ObservabilityError(PrismalError): ...
class ObservabilityConfigError(ObservabilityError): ...   # bad settings value
class RunNotFoundError(ObservabilityError): ...           # raised only by callers that opt into strict lookup;
                                                           # get_run_summary itself returns None, never raises
```

## SPEC-OBS-OTEL-001: Counters (`monitoring/otel.py` extension)

`prismal.observability_runs_total{result}`, `prismal.observability_scores_total{name}`, `prismal.observability_dataset_exports_total{fmt}`.

---

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-OBS-001 | `get_run_summary(run_id)` returns a `RunSummary` with non-empty `visited_nodes`/`tool_calls` for a run that recorded at least one node/tool call |
| RF-OBS-002 | `run_name_for`/`trace_tags_for` are the exclusive naming/tagging path for `DefaultObservabilityProvider` and `LangfuseManager.create_trace` call sites touched by this phase |
| RF-OBS-003 | `record_score(run_id=..., name="llm_judge:groundedness", value=0.8, source="llm_judge")` appears in the run's `RunSummary.scores` |
| RF-OBS-004 | `export_dataset([run_id], fmt=DatasetFormat.LANGSMITH)` and `..., fmt=DatasetFormat.LANGFUSE)` each return the documented per-format shape |
| RF-OBS-005 | `build_runtime(settings)` with `observability_enabled=True` yields a non-`None` `RuntimeContext.observability` |
| RF-OBS-006 | `DefaultObservabilityProvider` calls only `OTelManager`/`LangfuseManager` public methods — no new backend SDK is introduced |
| RF-OBS-007 | `FakeObservabilityProvider` round-trips `record_node`/`record_score`/`get_run_summary` with zero I/O |
| RF-OBS-008 | `observability_enabled=False` ⇒ `RuntimeContext.observability is None`; a test diff shows zero new calls to `OTelManager`/`LangfuseManager` versus the pre-OBS baseline |
| RF-OBS-009 | AST guard: no provider import outside `providers/`; no `mcp`/`skills` import in `agents/**` |
| RF-OBS-010 | `docs/observability-integration.md` contains an explicit "Framework vs. host" section naming `prismal-dashboard` as the UI consumer |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #6) and README Roadmap item 8 |
