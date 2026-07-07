# Prismal Observability Integration — Technical Design Document

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
| **SPEC** | `specs/observability-integration/SPEC.md` |
| **TASKS** | `specs/observability-integration/TASKS.md` |

---

## 1. Context

Prismal's monitoring stack is **emission-only**: `OTelManager` pushes spans/counters to an OTLP collector; `LangfuseManager` pushes traces to a Langfuse project. Both degrade gracefully to no-ops when disabled or unconfigured (`_NoOpSpan`, `_NoOpTrace`). Neither exposes a way to *ask*, in-process, "what happened during run X" — which is exactly the surface a future `prismal-dashboard`, a support/debugging workflow, or a deeper LangSmith/Langfuse integration needs. This phase adds that surface as a **hexagonal port** (`ObservabilityPort`), following the same pattern `agents/extension/ports.py` already establishes for `ToolProviderPort` (Phase Y), `VectorStorePort`/`VectorStoreProviderPort` (Phase Z), and `IdentityPort`/`CredentialVaultPort`/`PolicyPort` (Phase IDN) — plus the concrete parity closes the gap-analysis report calls out (naming, scoring, dataset export).

## 2. Feasibility with the existing core (confirmed)

- `agents/extension/ports.py` already hosts nine `Protocol`s in the same file, each `@runtime_checkable`, each with a short "conforming implementations" docstring — `ObservabilityPort` is a tenth, same shape, same conventions.
- `build_runtime()` (`prismal/composition/runtime.py`) already composes opt-in port *triples* gated by a settings flag (`identity_enabled` → `identity_provider`/`credential_vault`/`policy_engine`; `a2a_enabled` → tool-provider wrapping + `a2a_handler`). `observability_enabled` → `observability` slots into the same `if eff.<flag>_enabled: ...` pattern, appended to `RuntimeContext` as one more optional field defaulting to `None`.
- `OTelManager.start_span()` / `.increment_counter()` and `LangfuseManager.create_trace()` / `.score_trace()` are already the *only* sane entry points into their respective SDKs (both are process singletons per `CLAUDE.md`'s monitoring one-liner) — `DefaultObservabilityProvider` calls them, it does not reimplement span/trace plumbing.
- The Budget layer's per-run registry (`prismal/budget/resolve.py::seed_budget_run` / `get_budget_guard` / `clear_budget_run`, keyed by `session_id`, idempotent per user-turn) is a proven pattern for "a live, non-serializable object that must not land in checkpointed state" — `ObservabilityPort` instances (and any in-memory span buffers they own) follow the identical convention.
- `prismal/eval/trajectory.py::capture_trajectory()` already proves that a `(visited_nodes, tool_calls, tokens, cost, latency)` shape can be reconstructed purely from the graph's public `astream(..., stream_mode="updates")` event stream, with no agent instrumentation. `RunSummary` (this phase) reuses that *proof technique*, not that *type* (see DD-OBS-001).

No new LangGraph capability is required.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose |
|---|---|
| `prismal/monitoring/observability_types.py` | Value objects: `RunSummary`, `SpanRecord`, `ToolCallRecord`, `ScoreAnnotation`, `DatasetFormat` |
| `prismal/monitoring/observability.py` | `DefaultObservabilityProvider` (wraps `OTelManager` + `LangfuseManager`), `FakeObservabilityProvider` (tests), naming helpers (`run_name_for`, `trace_tags_for`) |
| `prismal/monitoring/observability_resolve.py` | Per-run registry: `seed_observability_run`, `get_observability_provider`, `clear_observability_run` (mirrors `budget/resolve.py`) |
| `prismal/agents/extension/ports.py` | *(extend)* `ObservabilityPort` `Protocol` |
| `prismal/composition/runtime.py` | *(extend)* `RuntimeContext.observability`; `build_runtime()` composition step |
| `prismal/core/config.py` | `observability_*` settings |
| `prismal/core/exceptions.py` | `ObservabilityError` hierarchy |
| `prismal/monitoring/otel.py` | *(extend)* OBS counters registered in `_register_standard_metrics()` |

All new runtime state that must be checkpoint-safe lives under `state["metadata"]["observability"]` as a **serializable marker only** (`{"enabled": True, "run_id": "..."}`), mirroring the Budget/Skynet/Kokoro/Hardening isolation convention. The live `ObservabilityPort` instance (and whatever in-memory span buffer the default adapter keeps) is **never** placed in checkpointed state — it is resolved per-run from the registry keyed by `session_id`, exactly like `budget/resolve.py`.

### 3.2 Data flow (with `observability_enabled=True`)

```
build_runtime(settings) ──► observability = DefaultObservabilityProvider(settings)  [or a host-supplied adapter]
        │                        (wraps OTelManager + LangfuseManager; no new backend)
        ▼
RuntimeContext.observability ──► handed to the host / seeded per-run via observability_resolve.py

each @prismal_node turn ──► record via the existing security→audit→otel middleware seam:
        node visit  ──► ObservabilityPort records a SpanRecord (node, duration, status)
        tool call   ──► ObservabilityPort records a ToolCallRecord (tool, node, ok, duration)
        LLM call    ──► usage extracted (reuses budget/usage.py::extract_token_usage) into the run's Usage

run ends ──► get_run_summary(run_id) -> RunSummary(visited_nodes, tool_calls, spans, usage, latency_ms)
                     │
      a human / eval-harness LLM-judge ──► record_score(run_id, name=..., value=..., comment=...)
                     │
      dashboard / CI / dataset build ──► export_dataset(run_ids, fmt="langsmith"|"langfuse")
```

### 3.3 Composition point (`build_runtime`, mirrors `identity_enabled`/`a2a_enabled`)

```python
observability: ObservabilityPort | None = None
if eff.observability_enabled:
    try:
        from prismal.monitoring.observability import DefaultObservabilityProvider
        observability = DefaultObservabilityProvider(settings=eff)
    except Exception as exc:
        raise RuntimeCompositionError("observability", str(exc)) from exc
```

`RuntimeContext` gains one more optional field (`observability: ObservabilityPort | None = None`), following the exact precedent of `identity_provider`/`credential_vault`/`policy_engine`/`a2a_handler` — all four are `None` unless their flag is set, and none of them changes the shape or behavior of the five mandatory ports (tool provider, vector store, embeddings, checkpointer, audit).

## 4. Design Decisions

### DD-OBS-001: `RunSummary` is a new, monitoring-owned type — not a reuse of `eval.Trajectory`
`prismal/eval/types.py::Trajectory` is deliberately scoped to the eval harness: it is captured post-hoc, once, from a single `EvalCase` run driven by `EvalRunner`, and its consumers (`assertions.py`, `judges.py`, `report.py`) are eval-specific. Importing `prismal.eval` from `prismal.monitoring` would invert the natural dependency direction (`eval/` already depends on graph + budget + providers; `monitoring/` is a lower-level, always-on layer that `eval/` itself could reasonably depend on instead). `RunSummary`/`SpanRecord`/`ToolCallRecord` are therefore **new, monitoring-owned types**, deliberately **shape-parallel** to `Trajectory`/`TrajectoryStep` (same fields: visited nodes, tool calls, tokens, cost, latency) so the two do not conceptually diverge, but with zero import coupling in either direction.

> **Open question:** should `prismal/eval/trajectory.py::capture_trajectory()` be refactored in a *later* phase to build its `Trajectory` by consuming `ObservabilityPort.get_run_summary()` instead of parsing the graph stream directly — collapsing two similar capture paths into one? This spec does **not** commit to that refactor (it would touch a shipped, tested Phase V module); it is flagged here as a natural follow-up once `ObservabilityPort` has shipped and proven itself, and it is **not** a task in `TASKS.md`.

### DD-OBS-002: Live provider state via a per-run registry, never in checkpointed state
Mirrors `budget/resolve.py::seed_budget_run`/`get_budget_guard`/`clear_budget_run` exactly: `observability_resolve.py::seed_observability_run(session_id, provider)` installs the live `ObservabilityPort` (and its internal span buffer) in an in-process registry keyed by `session_id`; `state["metadata"]["observability"]` carries only `{"enabled": True, "run_id": ...}`. Idempotent per user-turn, so a run's telemetry accumulates within a turn and a fresh run starts on the next one (turn-scope default, matching Budget's default scope).

### DD-OBS-003: `DefaultObservabilityProvider` is glue, not a new backend
The default adapter forwards every call to the *existing* singletons: `OTelManager.start_span()`/`.increment_counter()` for spans/counters, `LangfuseManager.create_trace()`/`.score_trace()` for traces/scores. It adds a small **bounded, in-memory ring buffer** (default size configurable, see `SPEC.md` SPEC-OBS-CFG-001) so `get_run_summary()` has something to return even when no OTel collector or Langfuse project is reachable — this is what makes the port "ship useful before any dashboard exists" (PLAN § 1). It is explicitly **not** a persistence layer: process restart loses the buffer, exactly like an unflushed OTel batch processor would.

### DD-OBS-004: Naming convention centralized in one function
`run_name_for(agent_name, session_id, turn) -> str` and `trace_tags_for(agent_name, node, org_id=None) -> list[str]` are the **single source of truth** for how a run/trace is named and tagged. Every call site — `DefaultObservabilityProvider`, a future `to_langfuse` refactor, `LangfuseManager.create_trace`, and any LangSmith-side integration a host adds — goes through these two functions instead of re-deriving names ad hoc. This directly targets the gap-analysis finding that "run/trace naming conventions [don't] match what LangSmith/Langfuse dashboards expect out of the box" (PLAN § 2): consistent `agent_name` + `session_id` + `node` tagging is exactly what both dashboards group/filter by in their default views.

### DD-OBS-005: Opt-in, snapshot-guaranteed
Every wiring point gates on `settings.observability_enabled` (default `False`). With the flag off: `RuntimeContext.observability is None`, `build_runtime()`'s composition step is skipped entirely, and — critically — **not a single existing `OTelManager`/`LangfuseManager` call site changes**, because `DefaultObservabilityProvider` is additive glue around them, never a replacement. A test asserts this (mirrors the Phase H/C/S/K snapshot-test convention; see `TASKS.md` OBS6-05).

### DD-OBS-006: `get_run_summary` is best-effort and non-durable by contract
The `ObservabilityPort` Protocol does **not** mandate persistence or a query language — `get_run_summary(run_id) -> RunSummary | None` returns `None` for an unknown or evicted `run_id`, and callers must treat it as best-effort. A durable, indexed store (so a dashboard could page through history, filter by tenant, etc.) is explicitly left to whichever component eventually needs it — most naturally `prismal-dashboard` itself, or a future adapter implementing the same port over a real database. This keeps the port's contract stable regardless of which backend implements it (OTel-only, Langfuse-only, both, or a future first-party store).

> **Open question:** is a bounded in-memory ring buffer (this phase's default) sufficient for the "recent spans/traces" requirement, or should 3.9.0 also ship a minimal SQLite-backed history (mirroring `monitoring/cost_tracker.py`'s `CostTracker` pattern) so `get_run_summary` survives a process restart? Deferred to implementation time / TASKS.md scoping — flagged here so it is not silently decided by whoever picks up OBS2.

## 5. Security & cost

- `RunSummary`/`SpanRecord`/`ToolCallRecord` carry **structural** data (node names, tool names, durations, token counts, cost) — never raw prompt/response content by default, matching the hash-first convention `AuditLogger` and the Budget/Hardening layers already use. Any adapter that chooses to attach content (e.g. for a richer dashboard) does so explicitly and is responsible for its own redaction; the default adapter does not.
- `export_dataset()` operates on **already-captured** run data (or eval-harness `EvalCase`/`Trajectory` data, when available) — it does not re-execute the graph or make new LLM calls, so it has zero incremental cost beyond serialization.
- Score/feedback annotation (`record_score`) never re-injects its `comment` into a live prompt — it is a terminal, one-way write to the observability backend (Langfuse `score_trace` or the default in-memory buffer), consistent with `SecurePromptBuilder`'s "only user-isolated content reaches a prompt" rule (there is no prompt here to reach).
- All new modules are pure Python / structural glue — no new provider SDK import (`CLAUDE.md` rule #4 is unaffected: LangSmith/Langfuse SDK usage, if any is added beyond the existing `langfuse` dependency, stays confined to `prismal/monitoring/`, which is the established home for both, not `prismal/providers/`).

## 6. Observability (of observability)

### 6.1 OTel counters (registered in `OTelManager._register_standard_metrics()`)
- `prismal.observability_runs_total{result}` (`result` ∈ `completed|evicted`)
- `prismal.observability_scores_total{name}`
- `prismal.observability_dataset_exports_total{fmt}` (`fmt` ∈ `langsmith|langfuse`)

### 6.2 Spans
- `prismal.observability.record_node`, `prismal.observability.record_score`, `prismal.observability.export_dataset`.

## 7. Relationship to existing specs

- **`agent-eval-harness/` (Phase V)** — related but not modified; see DD-OBS-001 for the explicit reuse-vs-extend decision and the open question about a future consolidation.
- **`cost-budget-governance/` (Phase C)** — the per-run registry pattern (`budget/resolve.py`) this phase mirrors (DD-OBS-002); `Usage` (`budget/types.py`) is reused for the cost/latency portion of `RunSummary` where the shape fits.
- **`composition-root/` (Phase R)** — the composition mechanics (`build_runtime`, opt-in port triples gated by a settings flag) this phase extends by one more optional field.
- **`runtime-hardening/` (Phase H)** — the OTel-counter-registration precedent and the `mode`/opt-in/snapshot-test conventions this phase's `TASKS.md` follows structurally.
- **`agent-identity-governance/` (Phase IDN)** / **`a2a-interop/` (Phase I)** — the two existing precedents in `build_runtime()` for "an opt-in port composed only when its flag is set," which `observability_enabled` follows verbatim.

## 8. Testing strategy (summary; detail in `TASKS.md`)

- Unit: `RunSummary`/`SpanRecord`/`ToolCallRecord` construction and the ring-buffer eviction policy; `run_name_for`/`trace_tags_for` naming-convention determinism; `record_score` on the default adapter (Langfuse leg mocked); `export_dataset` shape for both `fmt` values; `FakeObservabilityProvider` round-trip.
- Integration: `observability_enabled=False` — assert `RuntimeContext.observability is None` **and** that no new call reaches `OTelManager`/`LangfuseManager` beyond what already happens today (the explicit "nothing changes when off" test from the PLAN's goals table); `observability_enabled=True` with fakes — a simulated run's `get_run_summary()` reflects its node visits and tool calls end-to-end.
- Guards: no provider SDK import outside `providers/`; no `mcp`/`skills` import in `agents/**` (reuse the existing AST tests — this phase's modules don't need new exemptions).

## 9. Rollout

1. Ship modules behind `observability_enabled=False` (no wiring change observable; `RuntimeContext.observability` stays absent from any code path that doesn't explicitly opt in).
2. Land `DefaultObservabilityProvider` wrapping the existing OTel/Langfuse singletons — validate `get_run_summary()` against a real Langfuse project in a dev environment.
3. Wire the naming convention (`run_name_for`/`trace_tags_for`) into `LangfuseManager.create_trace` call sites and document the LangSmith-side equivalent for hosts that use LangSmith instead of/alongside Langfuse.
4. Publish `docs/observability-integration.md` and `examples/observability_integration.py`; mark README/CHANGELOG entries as **planned** (this spec does not ship code).

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #6) and README Roadmap item 8 |
