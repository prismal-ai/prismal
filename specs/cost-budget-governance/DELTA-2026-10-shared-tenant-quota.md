# DELTA — Shared tenant quota counters (SQLite)

| Field | Value |
|---|---|
| **Status** | `IMPLEMENTED` (2026-10-12) |
| **Base spec** | `specs/cost-budget-governance/` (Phase C) |
| **Source** | `docs/budget.md` § Multi-tenant ("shared cross-tenant quota counters are a follow-up"); `specs/open-items-2026-10/PLAN.md` §2.5 |
| **Decision** | Store = SQLite (user decision, 2026-10-12) |

## 1. Problem

`budget_scope="tenant"` is accepted, but every run gets a fresh in-memory `CostMeter`.
A tenant ceiling therefore never adds up across runs, sessions or worker processes, so
it behaves like `turn`.

## 2. Requirements (EARS)

- **TQ-001.** When `budget_enabled` and `budget_scope == "tenant"` and the run has a
  tenant (`org_id` argument, else the host's `bind_org()` binding), the per-run guard
  shall check the tenant's **window total**, not the run's own usage.
- **TQ-002.** The window shall be the UTC calendar `month` (default) or `day`
  (`budget_tenant_window`). A new window starts from zero.
- **TQ-003.** With `budget_tenant_store_path` set, totals shall be shared by every
  process that uses the same SQLite file. Increments shall be atomic: a
  `BEGIN IMMEDIATE` upsert with `RETURNING`, WAL mode and a busy timeout.
  With the path empty, totals are process-local (in memory).
- **TQ-004.** Each recorded call shall add its delta to the store and adopt the
  returned total. Another process's spend becomes visible at this run's next
  recorded call. No extra read happens on the hot path.
- **TQ-005.** If the store fails, metering shall fall back to local accumulation for
  that call. It never raises into the graph. A warning
  `budget_tenant_quota_store_failed` is logged.
- **TQ-006.** `budget_scope == "tenant"` without a resolvable tenant shall fall back to
  a per-run meter and log `budget_tenant_scope_without_org`.
- **TQ-007.** Tenants and windows shall be isolated: key `(org_id, window)`.
- **TQ-008.** An unknown `budget_tenant_window` shall fail `Settings` load.
- **TQ-009.** With `budget_enabled=False` or another scope, behaviour shall be
  unchanged: no store is built and no file is touched.

## 3. Design

- `prismal/budget/tenant_quota.py`
  - `TenantQuotaStorePort`: `add(org_id, window, usage) -> Usage` and
    `get(org_id, window) -> Usage`.
  - `InMemoryTenantQuotaStore` and `SqliteTenantQuotaStore(path)`. The SQLite table
    `budget_tenant_quota` has the primary key `(org_id, window)` and the columns
    `prompt_tokens`, `completion_tokens`, `calls`, `cost_usd` and `wall_clock_s`
    (the four guarded dimensions).
  - `window_key(now, kind)` returns `"2026-10"` or `"2026-10-12"` (UTC).
  - `TenantCostMeter(CostMeter)`: loads the window total when it is built. On `record`
    it keeps OTel and FinOps attribution per call, then adopts the store total.
  - `get_tenant_quota_store(settings)` is a per-path singleton;
    `set_tenant_quota_store()` lets hosts and tests inject a store.
- `budget/resolve.py::seed_budget_run` picks `TenantCostMeter` for TQ-001 and
  resolves the tenant from `org_id` or `host_stats.current_org`.
- Settings (budget block): `budget_tenant_window` (`month`) and
  `budget_tenant_store_path` (`""`). `_validate_budget` checks the window.

## 4. Non-goals

- Server-backed stores (Redis/Postgres). The port allows them later.
- Per-dimension windows, or rolling (non-calendar) windows.
- Tenant usage beyond the four guarded dimensions. Cache and gateway fields stay
  in the per-call attribution only.
