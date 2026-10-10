# Prismal Composition Root — DELTA 2026-10: Host Read Surface (HRS)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-06, v3.17.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | HRS (Host Read Surface) — delta to Fase R `specs/composition-root/` (implemented) |
| **Target package version** | `3.17.0` (ships with PRT; SemVer minor, additive) |
| **Consumers** | `prismal-server` 0.2 (`SPEC-RH2-*`), via it `prismal-sdk` 0.2 and `prismal-dashboard` DSH2 |

---

## 1. Rationale

`prismal-server` 0.2 must read engine state it cannot reach today: tenant budget status, gateway quota
windows, audit pages with hash-chain verification, DEC shadow agreement, AMR routing savings, the
tenant-scoped A2A route shape, and per-tenant configuration injection. The host is forbidden to
re-implement engine logic and its AST import guard allows only a fixed list of entry points. Without
an engine-owned read surface the host would either reach into internals (breaking the guard) or
"verify and degrade" (shipping `unknown` everywhere). HRS publishes one small, versioned,
**read-only** surface: a `config_source` argument on `build_runtime`, read methods on the
`RuntimeContext` it returns (attribute access — no new imports), and view types the host may import
for annotations. It is the authoritative allowlist for the host import guard.

## 2. Dependencies

Fase R `composition/runtime.py` (`build_runtime`, `RuntimeContext`, `build_test_runtime`); Fase W
`ConfigSourcePort` / `build_settings(source)`; Phase C `BudgetGuard.check`, `CostMeter`; GW
`GatewayPort.quota() -> QuotaSnapshot`; L5 `AuditLogger` (xxhash chain); DEC `ShadowRunner.report()`,
`prismal.decisions_total`, `prismal.llm_calls_avoided_total`; AMR routing instruments
(`SPEC-AMR-OTEL-001`); PRT1 `build_agent_card(protocol_version=)`; PRT5 `get_run_summary` /
`RunSummary.to_usage_wire` (owned by PRT5-06, listed here only).

## 3. Public host read surface

| # | Import path | Symbols | Owner |
|---|---|---|---|
| 1 | `prismal.composition` | `build_runtime`, `build_test_runtime` | Fase R (MODIFIED by HRS-CMP-001) |
| 2 | `prismal.agents.graph` | `get_async_compiled_graph` | v0.1 host entry point |
| 3 | `prismal.a2a` | `build_agent_card`, `get_settings`, `DEFAULT_CAPABILITY_MAP` (re-exports since 3.10.2) | Phase I / PRT1 |
| 4 | `prismal.events.agui` | `AgUiContext`, `start_run`, `to_agui_events`, `finish_run`, `error_run`, `AGUI_PROTOCOL_VERSION` | PRT5 |
| 5 | `prismal.monitoring` | `get_run_summary` (returns `RunSummary` with `to_usage_wire()`) | PRT5 |
| 6 | `prismal.composition.host_surface` | view types of §4 (annotations / `isinstance` only) | HRS |

Rows 1–3 are the v0.1 entry points; HRS1-01 reconciles them with the host's current guard and adds
any missing symbol here (never silently removes one). Everything the host reads beyond rows 1–6 is
reached as **methods on `RuntimeContext`** (§4). Changing a row is a coordinated minor in engine + host.

## 4. Requirements

### MODIFIED

**SPEC-HRS-CMP-001 — `build_runtime(..., config_source=)`**
```python
def build_runtime(settings: Settings | None = None, *, org_id: str | None = None,
                  config_source: ConfigSourcePort | None = None,   # NEW
                  overrides: Mapping[str, Any] | None = None, **existing: Any) -> RuntimeContext: ...
```
WHEN `config_source` is given, THE SYSTEM SHALL build this runtime's settings with
`build_settings(ChainedConfigSource([config_source, <process source>]))` (first wins) and SHALL NOT
mutate the global source or `os.environ`. WHEN it is `None`, THE SYSTEM SHALL behave byte-for-byte as 3.16.

### ADDED — `RuntimeContext` read methods (all never raise, scoped to `ctx.org_id`, no secrets)

```python
class RuntimeContext:
    def budget_status(self, *, thread_id: str | None = None) -> BudgetStatusView: ...
    async def quota_windows(self) -> list[QuotaWindowView]: ...
    def audit_page(self, *, page: int = 1, page_size: int = 50,
                   since: datetime | None = None) -> AuditPageView: ...
    def verify_audit_chain(self, *, max_records: int = 10_000) -> AuditChainStatus: ...
    def decisions_report(self, *, since: datetime | None = None,
                         until: datetime | None = None) -> DecisionsReportView: ...
    def routing_report(self, *, since: datetime | None = None,
                       until: datetime | None = None) -> RoutingReportView: ...
    def a2a_routes(self, *, protocol_version: Literal["0.3", "1.0"] | None = None) -> A2ARouteSpec: ...
```

View types (`prismal/composition/host_surface.py`, frozen dataclasses; field names = host wire):
```python
BudgetAmounts(tokens: int | None, cost_usd: float | None, calls: int | None, wall_clock_s: float | None)
BudgetStatusView(enabled, scope, status, breached_dimension, soft_ratio, hard_cap,
                 limits: BudgetAmounts | None, consumed: BudgetAmounts | None, remaining: BudgetAmounts | None)
QuotaWindowView(gateway, window, unit, limit, used, remaining, resets_at, source, as_of)
AuditEntryView(seq, ts, event, actor, org_id, hash, prev_hash, fields)          # hash-first, no content
AuditPageView(page, page_size, total, entries: tuple[AuditEntryView, ...])
AuditChainStatus(status: Literal["valid", "broken", "unverified"], checked: int, first_broken_seq: int | None)
ReportWindow(since: datetime, until: datetime)
DecisionIntegrationStats(integration, mode, samples, agreement_ratio: float | None, accepted, escalated,
                         abstained, llm_calls_avoided, window: ReportWindow)
DecisionsReportView(enabled: bool, integrations: tuple[DecisionIntegrationStats, ...])
RoutingPolicyStats(policy, decisions, decisions_by_tier: Mapping[str, int], escalations,
                   escalations_by_reason: Mapping[str, int], plan_cache_hits, estimated_savings_usd, window)
RoutingReportView(enabled: bool, mode: str, policies: tuple[RoutingPolicyStats, ...])
A2ARouteSpec(card_path: str, rpc_path: str, tenant_scoped: bool)
```

| ID | Requirement (EARS) |
|---|---|
| SPEC-HRS-BUD-001 | WHEN `budget_status()` is called, THE SYSTEM SHALL derive `status`/`breached_dimension` from `BudgetGuard.check()` over the tenant meter (scope `tenant`) or the thread's per-run meter (`turn`/`session`); IF no meter applies (e.g. `turn` scope without `thread_id`), THEN `status="unknown"` and `consumed=None`; WHERE `budget_enabled` is false, `status="disabled"` and limits/remaining/soft_ratio/hard_cap are `None`. A `0` limit yields `remaining=None`. |
| SPEC-HRS-QTA-001 | WHEN `quota_windows()` is awaited, THE SYSTEM SHALL flatten each enabled gateway's `QuotaSnapshot` into one `QuotaWindowView` per window (`credit\|5h\|daily\|weekly\|monthly`; `source="api"` for API data, `"ledger"` for `QuotaLedger`); IF one gateway fails, THEN it is omitted and `prismal.host_surface_errors_total{method="quota_windows"}` increments. |
| SPEC-HRS-AUD-001 | WHEN `audit_page(page, page_size≤200)` is called, THE SYSTEM SHALL return the tenant's audit entries newest-first with `total` (or `None` if unknown) and never record content beyond the hash-first fields already logged. |
| SPEC-HRS-AUD-002 | WHEN `verify_audit_chain()` runs, THE SYSTEM SHALL recompute the xxhash chain over at most `max_records` and return `valid`, or `broken` with the first failing `seq`; IF the log is unreadable or exceeds the bound, THEN `unverified`. |
| SPEC-HRS-DEC-001 | WHEN `decisions_report()` is called, THE SYSTEM SHALL return one row per DEC integration with `samples=AgreementReport.n`, `agreement_ratio=AgreementReport.agreement_accepted` (`None` when `n=0`), `accepted/escalated/abstained` from outcome counts, `llm_calls_avoided` from the DEC counter, all from an in-process `DecisionStatsRecorder` fed at the same points as the OTel instruments; WHERE `decisions_enabled` is false, `enabled=False` and no rows. |
| SPEC-HRS-RTE-001 | WHEN `routing_report()` is called, THE SYSTEM SHALL return per routing policy the decisions (total and by tier), escalations (total and by reason), plan-cache hits and `estimated_savings_usd` (frontier counterfactual − actual, same computation as `prismal.routing_savings_usd`), from an in-process `RoutingStatsRecorder`; WHERE `adaptive_routing_enabled` is false, `enabled=False` and no rows. |
| SPEC-HRS-WIN-001 | Recorders keep 1-minute buckets for `host_surface_stats_retention_h` (default 168 h); IF `since` precedes the oldest bucket or process start, THEN each row's `window.since` is clamped to it (never extrapolated). |
| SPEC-HRS-A2A-001 | WHEN `a2a_routes()` is called, THE SYSTEM SHALL return paths whose concatenation with `a2a_base_url` equals the URLs that `build_agent_card(org_id=ctx.org_id, protocol_version=…)` advertises (contract test). |
| SPEC-HRS-SUR-001 | THE SYSTEM SHALL keep §3 symbols and §4 signatures stable within 3.x; a signature-snapshot test fails on any change. |
| SPEC-HRS-SUR-002 | WHEN any read method fails internally, THE SYSTEM SHALL return its documented fallback (`unknown`, `unverified`, empty list, `enabled=False`) and increment `prismal.host_surface_errors_total{method}`; no read method mutates engine state. |
| SPEC-HRS-GRD-001 | WHERE no host calls the new methods and `config_source` is `None`, THE SYSTEM SHALL keep the supervisor graph snapshot and `build_runtime` behaviour unchanged. |

## 5. Tasks

| ID | Task | Est. | Dep | SPEC | Status |
|---|---|---|---|---|---|
| HRS1-01 | Reconcile §3 with the host's current guard; `host_surface.py` view types + re-exports | 0.4 d | — | SUR-001 | DONE |
| HRS1-02 | `build_runtime(config_source=)` via chained source, no global mutation | 0.4 d | HRS1-01 | CMP-001 | DONE |
| HRS1-03 | `budget_status()` over tenant / per-run meters | 0.5 d | HRS1-01 | BUD-001 | DONE |
| HRS1-04 | `quota_windows()` flattening GW `QuotaSnapshot` | 0.3 d | HRS1-01, GW5 | QTA-001 | DONE |
| HRS1-05 | `audit_page()` + `verify_audit_chain()` | 0.6 d | HRS1-01 | AUD-001/002 | DONE |
| HRS1-06 | `DecisionStatsRecorder` + `decisions_report()` | 0.5 d | DEC3 | DEC-001, WIN-001 | DONE |
| HRS1-07 | `RoutingStatsRecorder` + `routing_report()` | 0.5 d | AMR6 | RTE-001, WIN-001 | DONE |
| HRS1-08 | `a2a_routes()` + card/route consistency | 0.3 d | PRT1-06 | A2A-001 | DONE |
| HRS1-09 | Signature-snapshot test, fail-open tests, graph snapshot, `host_surface_errors_total` counter | 0.3 d | HRS1-02..08 | SUR-001/002, GRD-001 | DONE |
| HRS1-10 | `docs/composition-root.md` "Host read surface" section | 0.2 d | HRS1-09 | — | DONE |
| **Total** | | **~4.0 d** | | | |

## 6. Tests

| Test module | Covers |
|---|---|
| `tests/unit/composition/test_host_surface_signatures.py` | SUR-001 (symbol + `inspect.signature` snapshot) |
| `tests/unit/composition/test_build_runtime_config_source.py` | CMP-001 (two tenants, no `os.environ` change), GRD-001 |
| `tests/unit/composition/test_budget_status.py` | BUD-001 (all scopes, disabled, unknown, zero limits) |
| `tests/unit/composition/test_quota_windows.py` | QTA-001 (OpenRouter credit, OpenCode Go windows, failing gateway) |
| `tests/unit/composition/test_audit_surface.py` | AUD-001/002 (paging, tampered record → `broken`, oversize → `unverified`) |
| `tests/unit/composition/test_reports.py` | DEC-001, RTE-001, WIN-001 (recorders vs OTel counter parity, clamping) |
| `tests/unit/composition/test_a2a_routes.py` | A2A-001 |
| `tests/unit/composition/test_host_surface_fail_open.py` | SUR-002 |

## 7. Out of scope

Write/admin operations (mode changes, tenant creation, audit pruning); persistent report storage
across restarts (recorders are in-process; host persists usage itself); OTel read-back; an
`AuthorizationPrompter` for host-side outbound OAuth; HITL resume API.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial delta: engine-owned read surface for prismal-server 0.2 |
