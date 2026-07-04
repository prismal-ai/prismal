# Prismal — Observability Integration (`ObservabilityPort`, LangSmith/Langfuse parity)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | OBS (Observability) |
| **Target package version** | `3.9.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Priority** | P3 (polish / DX) |
| **Related** | `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (item #6, "UI de observabilidad propia o integración profunda LangSmith/Langfuse"), `README.md` § "Roadmap — features to build" (item 8, "Polish — no spec yet"), `README.md` § "Framework or host?" (row 8: "observability UI" assigned to the host/dashboard column), `specs/runtime-hardening/` (structural template + OTel counter precedent), `specs/composition-root/` (port-composition pattern this spec reuses), `specs/agent-eval-harness/` (`Trajectory`/`Scorecard` — reuse vs. extend decision), `specs/cost-budget-governance/` (per-run registry precedent), `prismal/monitoring/otel.py`, `prismal/monitoring/langfuse_client.py` |

---

## 1. Executive Summary

Prismal already emits OpenTelemetry spans/counters and Langfuse traces (`prismal/monitoring/otel.py`, `prismal/monitoring/langfuse_client.py`), and the Agent Evaluation & Reliability Harness (Phase V) already captures per-run `Trajectory`/`Scorecard` data from the public graph stream (`prismal/eval/trajectory.py`, `prismal/eval/report.py`). What is still missing — flagged independently by the repo's own roadmap (`README.md` Roadmap item 8, "Polish — no spec yet") and by the gap-analysis report (`docs/gap-analysis-loops-harness-guardrails-2026-07.md`, item #6) — is a **stable, backend-agnostic contract** for querying a run's telemetry (recent spans, cost/latency, tool-call history, node-visit sequence), plus concrete parity gaps against what a "deep" LangSmith/Langfuse integration looks like in 2026: consistent run/trace naming, a feedback/score-annotation hook usable by a human or the eval-harness's LLM-judge, and dataset export compatible with LangSmith's/Langfuse's evaluation datasets.

This feature adds an **`ObservabilityPort`** hexagonal port (mirroring `ToolProviderPort` / Phase Y, `VectorStorePort` / Phase Z, and the ports composed by `build_runtime()` / Phase R), a default adapter that is a thin wrapper over the *existing* OTel/Langfuse emission (so it ships useful before any dashboard exists), a `FakeObservabilityProvider` for tests, and the concrete LangSmith/Langfuse parity closes (naming, scoring, dataset export). It is **additive and gated** (`settings.observability_enabled`, default `False`): with the flag off, `RuntimeContext.observability` is `None` and every existing OTel/Langfuse call site is byte-for-byte unchanged.

> **Hard scope boundary.** `CLAUDE.md`'s framework/host split ("contract/logic → framework (`prismal/`); serving HTTP, authenticating, rendering, persisting config → host") applies here without exception. `prismal` is an embeddable engine with **no web server, dashboard, or CLI** (see the first line of `CLAUDE.md`). A literal "observability UI" — a web page with charts, a timeline view, a dashboard admin panel — belongs in the separate `prismal-dashboard` repository, which the README's own architecture section lists as **planned/early-stage** (dashed outline, not yet developed). This spec ships the **framework-side** contribution only: the port, the value objects it returns, the default adapter, and the parity closes. It does **not** design a web UI, React components, charts, or a dashboard server. See § 5.2 Out of Scope.

---

## 2. Context and Problem

- **The gap is real and dual-sourced.** Both `README.md` (Roadmap item 8: *"first-party observability UI (or a deep LangSmith/Langfuse integration) and per-node type safety"*) and the independent gap-analysis report (`docs/gap-analysis-loops-harness-guardrails-2026-07.md` § 5, row #6: *"UI de observabilidad propia o integración profunda LangSmith/Langfuse — Polish (P3, ya en README) — Medio-grande — Ya identificado por el propio equipo"*) name this exact gap, with no spec yet.
- **What exists today is emission, not a queryable contract.** `OTelManager` (`prismal/monitoring/otel.py`) and `LangfuseManager` (`prismal/monitoring/langfuse_client.py`) are singletons that *push* spans/counters/traces to an external backend. Nothing in the core lets a caller *ask*, in-process, "what happened in run X?" — no `RunSummary`, no queryable tool-call history, no node-visit sequence, independent of whether an OTel collector or Langfuse project is actually configured and reachable.
- **Trajectories already exist, but scoped to eval.** Phase V's `capture_trajectory()` (`prismal/eval/trajectory.py`) reconstructs a `Trajectory` (visited nodes, tool calls, tokens, cost, latency) from the graph's public event stream — but only for **eval-harness runs**, driven by `EvalRunner`, not for arbitrary production runs. There is deliberate tension here: should "observability" duplicate this shape for live runs, or should the eval harness become a *consumer* of the new port? This spec resolves it explicitly (§ 5.1, DD-OBS-001 in `ARCHITECTURE.md`).
- **Langfuse/LangSmith parity is shallow.** `LangfuseManager.create_trace()` accepts `name`/`session_id`/`user_id`/`metadata`, but nothing in the core enforces a *naming convention* that lines up with what the Langfuse/LangSmith dashboards group by (project, run name, tags per agent/node). There is no `score_trace`-equivalent hook exposed to the eval-harness's LLM-judge or to a human reviewer outside of `eval/report.py::to_langfuse` (which is a one-off, Langfuse-only, scorecard-level export — not a per-run annotation hook). There is no dataset export compatible with either vendor's evaluation-dataset format.
- **The composition root is the natural home for a 6th port.** `build_runtime()` (`prismal/composition/runtime.py`) already composes 5 ports (tool provider, vector store, embeddings, checkpointer, audit) plus two opt-in port triples (identity, A2A). An `ObservabilityPort` slots into the same `RuntimeContext` the same way, following the established "orchestrate, do not reimplement" principle (`DD-CR-001`).

---

## 3. Target Users

- **AI Engineer:** wants to answer "what did this run do, what did it cost, and where did it go wrong?" without standing up an external collector.
- **Security/Compliance Reviewer:** wants a per-run tool-call history and node-visit sequence to audit a specific incident (complements, does not replace, `AuditLogger`'s hash-chained log).
- **Eval Harness maintainer (Phase V):** wants a single place to attach an LLM-judge score to a run, instead of hand-rolling Langfuse calls in `eval/report.py`.
- **Future `prismal-dashboard` maintainer:** needs a stable, versioned contract to build a UI against — this spec is written so that repo can start against a real interface instead of raw OTel/Langfuse internals.
- **Platform Host (`prismal-server`):** composes the port once via `build_runtime()` and can forward `get_run_summary()` over its own API without prismal ever serving HTTP itself.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Queryable run telemetry | `ObservabilityPort.get_run_summary(run_id)` returns spans, tool-call history, node-visit sequence, cost/latency | Implemented |
| LangSmith/Langfuse-ready naming | Run/trace names + tags follow one documented convention consumed by both backends | Implemented |
| Score/feedback hook | A human or the eval-harness LLM-judge can attach a score to a specific `run_id` | Implemented |
| Dataset export | `export_dataset()` produces LangSmith- and Langfuse-compatible records | Implemented |
| Ships useful pre-dashboard | Default adapter wraps existing `OTelManager`/`LangfuseManager` emission — zero new backend required | Implemented |
| Composition parity | `build_runtime()` composes the port into `RuntimeContext.observability` like the other 5+2 ports | Implemented |
| Backward-compat | `observability_enabled=False` ⇒ `RuntimeContext.observability is None` and zero existing OTel/Langfuse call sites change | 100% (test) |
| Testability | `FakeObservabilityProvider` — no I/O, deterministic | Implemented |

---

## 5. Scope

### 5.1 In Scope

- **OBS1 — `ObservabilityPort`** (hexagonal `Protocol`, `agents/extension/ports.py`): a stable, backend-agnostic surface over one run's telemetry — recent spans, cost/latency summary (reusing `budget.types.Usage` where the shape fits), tool-call history, and node-visit sequence. Implementable by an OTel/Langfuse-backed adapter, a future first-party store, or a test double.
- **OBS2 — Deep LangSmith/Langfuse parity:** a documented run/trace naming convention (`run_name`, `session_id`, tags) that both dashboards group sensibly by out of the box; a `record_score()` feedback/annotation hook usable by a human reviewer or the eval-harness LLM-judge to attach a score to a specific run; an `export_dataset()` method producing records compatible with LangSmith's and Langfuse's evaluation-dataset import formats.
- **OBS3 — `build_runtime()` composition + reference adapter:** `RuntimeContext.observability: ObservabilityPort | None`, composed the same way `identity_*`/`a2a_handler` are (opt-in, additive); `DefaultObservabilityProvider` — a thin wrapper over the existing `OTelManager`/`LangfuseManager` singletons (so this ships useful *before* any dashboard or new backend exists); `FakeObservabilityProvider` for tests, mirroring `FakeToolProvider`/`FakeVectorStore`/`FakeConfigSource`.
- **OBS4 — Integration, settings, tests, docs, packaging:** `observability_*` settings; unit + integration tests, **including a test that with `observability_enabled=False` nothing changes for existing OTel/Langfuse emission** (mirrors the Phase H/C/S/K snapshot-test convention); `docs/observability-integration.md` stating the framework/host split explicitly and pointing at where `prismal-dashboard` plugs in later; `examples/observability_integration.py`; README/CHANGELOG entries recorded as **planned**, not shipped.

### 5.2 Out of Scope

- **Any web UI, dashboard server, chart rendering, or admin page** — that is `prismal-dashboard`'s job (planned, separate repo; see `README.md` architecture section, dashed-outline component). This repo ships the port and the value objects a UI would consume; it renders nothing.
- **A new first-party time-series/trace persistence backend.** `DefaultObservabilityProvider` is a wrapper over the *existing* OTel/Langfuse emission plus an in-memory, bounded, best-effort run registry (mirroring the Budget/Skynet per-run registry convention) — not a new database. A durable, queryable store is future work for whichever component (core or dashboard) needs it; this spec's port is deliberately implementation-agnostic so that door stays open (see `ARCHITECTURE.md` § Open Questions).
- **Rewriting `OTelManager`/`LangfuseManager`.** Both singletons are reused verbatim (`DD-CR-001`-style: orchestrate, do not reimplement); this feature adds a port and naming/scoring conventions around them, not a replacement.
- **Changing `prismal/eval/`'s existing behavior.** Phase V's `capture_trajectory()`/`Scorecard`/`eval/report.py::to_langfuse` keep working unmodified; this spec only *proposes* (as a follow-up task, not a hard requirement of 3.9.0) that `to_langfuse` could later delegate to `ObservabilityPort.record_score()` instead of calling `LangfuseManager` directly — flagged as an open question, not committed.
- **Guardrails/security-classifier work** (already covered by Phase H `runtime-hardening` and out of scope here).
- **Per-node type safety / `AgentState` Pydantic validation** — the other half of README Roadmap item 8; tracked separately, not bundled into this spec.
- **Network/infra hardening** (mTLS, seccomp, collector deployment) — host responsibility, same boundary `runtime-hardening/PLAN.md` draws.

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-OBS-001 | `ObservabilityPort` (`Protocol`, `@runtime_checkable`) exposes a queryable run summary: recent spans, cost/latency, tool-call history, node-visit sequence | `MUST` |
| RF-OBS-002 | Run/trace naming convention that LangSmith and Langfuse both group sensibly by (documented, single source of truth) | `MUST` |
| RF-OBS-003 | `record_score()` hook: a human or the eval-harness LLM-judge can attach a named score + comment to a specific `run_id` | `MUST` |
| RF-OBS-004 | `export_dataset()` produces LangSmith- and Langfuse-compatible evaluation-dataset records | `SHOULD` |
| RF-OBS-005 | `build_runtime()` composes an `ObservabilityPort` into `RuntimeContext.observability` when `settings.observability_enabled` | `MUST` |
| RF-OBS-006 | `DefaultObservabilityProvider` wraps the *existing* `OTelManager`/`LangfuseManager` emission — no new backend required | `MUST` |
| RF-OBS-007 | `FakeObservabilityProvider` — deterministic, I/O-free, for tests | `SHOULD` |
| RF-OBS-008 | `observability_enabled=False` ⇒ `RuntimeContext.observability is None`; zero behavior change at every existing OTel/Langfuse call site | `MUST` |
| RF-OBS-009 | No provider SDK import outside `prismal/providers/`; no `prismal.mcp`/`prismal.skills` import inside `prismal/agents/**` | `MUST` |
| RF-OBS-010 | `docs/observability-integration.md` states the framework/host split and the `prismal-dashboard` plug-in point explicitly | `SHOULD` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Scope creep into a first-party dashboard | Hard boundary stated in `CLAUDE.md`/this PLAN's header; the port returns data, it renders nothing; reviewed against `README.md`'s "Framework or host?" table |
| Duplicating `prismal/eval/`'s `Trajectory`/`Scorecard` | `RunSummary`/`SpanRecord` are new, monitoring-owned, deliberately parallel shapes (not a fork); no import of `prismal.eval` from `prismal.monitoring` (would invert the dependency direction) — see `ARCHITECTURE.md` DD-OBS-001 |
| In-memory run registry grows unbounded | Bounded ring buffer per run, mirrors the Budget/Skynet per-run registry convention (never in checkpointed state) |
| Behavior leak when disabled | Every wiring point gated on `observability_enabled`; a test asserts zero call-site change when off (mirrors Phase H/C/S/K) |
| Vendor-specific naming assumptions age out | Naming convention centralized in one function (`ARCHITECTURE.md` DD-OBS-004), not scattered at call sites — easy to revise later |
| Coupling to a non-existent `prismal-dashboard` | Feature lives in the core; the (future) dashboard only calls the port. Contract documented the same way Phase R documented the `prismal-server` lifespan before that repo existed |

---

## 8. Dependencies

- `prismal/monitoring/otel.py` (`OTelManager` — reused, extended with OBS counters).
- `prismal/monitoring/langfuse_client.py` (`LangfuseManager` — reused for the default adapter's Langfuse leg).
- `prismal/agents/extension/ports.py` (existing `Protocol` family: `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`, `ToolProviderPort`, `VectorStorePort`, `VectorStoreProviderPort`, `IdentityPort`, `CredentialVaultPort`, `PolicyPort` — `ObservabilityPort` joins this family).
- `prismal/composition/runtime.py` (`build_runtime()`, `RuntimeContext` — the composition point).
- `prismal/budget/types.py` (`Usage` — reused for the cost/latency summary shape where it fits; `prismal/budget/resolve.py` — per-run registry convention this feature mirrors).
- `prismal/eval/types.py` / `prismal/eval/trajectory.py` / `prismal/eval/report.py` (Phase V — related but not modified; see § 5.2 and `ARCHITECTURE.md` DD-OBS-001 for the reuse-vs-extend decision).
- `prismal/core/config.py` (new `observability_*` settings) and `prismal/core/exceptions.py` (new `ObservabilityError` hierarchy).

---

## 9. Next Steps

Implement per `TASKS.md` (phases OBS1–OBS6). Ship behind `observability_enabled=False` by default. Once `prismal-server`/`prismal-dashboard` exist, they consume `RuntimeContext.observability` and `ObservabilityPort.get_run_summary()`/`export_dataset()` directly — no further core changes anticipated for a first UI.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #6) and README Roadmap item 8 |
