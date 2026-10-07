# Prismal Runtime Composition Root (Phase R)

The **composition root** is a single facade — `build_runtime()` — that assembles
every core port from `settings` plus an optional tenant (`org_id`): the tool
provider (Phase Y), the vector store (Phase Z), embeddings, the checkpointer, and
the audit log. The host (`prismal-server`) calls it **once** in its lifespan and
gets back a `RuntimeContext` grouping all the ports with a coordinated teardown.

Guiding principle — **orchestrate, do not reimplement**: `build_runtime` calls
`build_default_tool_provider` (Y), `VectorStoreFactory` (Z), `EmbeddingsFactory`,
`build_checkpointer`, and `AuditLogger`; it never duplicates their logic.

Everything is importable from `prismal.composition`. A runnable example lives at
[`examples/composition_root.py`](../examples/composition_root.py).

The feature is **additive and opt-in**: code that keeps calling
`set_tool_provider` / `VectorStoreFactory` directly is unaffected.

---

## 1. The contract

```python
async def build_runtime(
    settings: Settings | None = None,
    *,
    org_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    mode: Literal["global", "context"] | None = None,
    collection_base: str = "default",
    mcp_config_path: Path | None = None,
) -> RuntimeContext: ...
```

`RuntimeContext` groups the composed ports — it adds no business logic:

| Field | Port | Source builder |
|---|---|---|
| `tool_provider` | `ToolProviderPort` | `build_default_tool_provider` (Phase Y) |
| `vector_store_provider` | `VectorStoreProviderPort` | `VectorStoreFactory` (Phase Z) |
| `embeddings` | `EmbeddingsPort` | `EmbeddingsFactory.create` |
| `checkpointer` | `CheckpointPort` | `build_checkpointer` |
| `audit` | `AuditPort` | `AuditLogger` |
| `config` | `RuntimeConfig` | resolved view (backend, collection, mode, org) |

`RuntimeContext.aclose()` disconnects MCP, closes the checkpointer, and releases
any built vector stores. It is **idempotent** and works as an async context
manager.

---

## 2. Two modes

`settings.runtime_mode` (or the `mode=` argument) selects how providers are wired:

| Mode | What happens | When |
|---|---|---|
| `global` (default) | injects the tool provider as a process singleton (`set_tool_provider`); the graph picks it up with no extra wiring | single-tenant host startup |
| `context` | keeps every port inside the returned `RuntimeContext`, leaving the process globals untouched | per-session / per-tenant, parallel tenants |

`runtime_mode` unifies Phase Y's `tool_provider_mode` and the vector-store mode.
For backward compatibility, if `runtime_mode` is **not** set explicitly but
`tool_provider_mode` is, `runtime_mode` is derived from it.

> **Phase Z note.** Phase Z ships a *factory* (`VectorStoreFactory`), not a
> process singleton or a graph-bound provider, so the vector store is **always**
> carried in the `RuntimeContext` via `VectorStoreProvider` — there is no
> `set_vector_store_provider` global to inject in global mode. Consumers that
> need a tenant-scoped store call `ctx.vector_store_provider.get_store(...)`.

---

## 3. Host contract — `prismal-server` lifespan

```python
from contextlib import asynccontextmanager

from prismal.agents.graph import get_async_compiled_graph
from prismal.composition import build_runtime
from prismal.core.config import get_settings


@asynccontextmanager
async def lifespan(app):
    ctx = await build_runtime(get_settings())          # mode=global, ≤5 LoC
    app.state.runtime = ctx
    app.state.graph = await get_async_compiled_graph()  # uses the injected provider
    try:
        yield
    finally:
        await ctx.aclose()
```

### Per-tenant (context mode)

```python
async def graph_for_org(org_id: str):
    ctx = await build_runtime(get_settings(), org_id=org_id, mode="context")
    # RAG / memory for this tenant read isolated collections:
    store = ctx.vector_store_provider.get_store("docs")   # -> "docs_<org_id>"
    return ctx  # caller calls ctx.aclose() when the session ends
```

Two tenants composed in parallel never share state: each gets its own providers
and a collection suffixed by `org_id` (`collection_for(base, org_id)`), applied
identically to RAG and memory.

---

## 4. Dashboard contract — `prismal-dashboard`

The dashboard reads/edits the same config sources the runtime consumes; it does
**not** call `build_runtime` (that is the server's job). The loaders in
`prismal.composition.config_sources` are pure and side-effect-free:

| UI section | Loader |
|---|---|
| MCP servers | `load_mcp_config(path)` → `McpConfig` |
| Skills | `resolve_skills_source(settings)` → `SkillsSource` |
| Vector store | `resolve_vector_store(settings, org_id)` → `(backend, collection)` |
| Runtime mode | `settings.runtime_mode` |

The dashboard persists changes; the server re-composes (restart or future
hot-reload).

---

## 5. Testing with fakes

`build_test_runtime` assembles a deterministic `RuntimeContext` with no I/O and
no global injection — `aclose()` is a no-op:

```python
from prismal.composition import build_test_runtime
from prismal.agents.extension.providers import FakeToolProvider
from prismal.rag.vector_store_factory import FakeVectorStore

ctx = build_test_runtime(
    tool_provider=FakeToolProvider({"coder": [...]}),
    vector_store=FakeVectorStore({"query": [(doc, 0.9)]}),
    org_id="acme",
)
```

---

## 6. Error handling

Any sub-port that fails to compose raises `RuntimeCompositionError(port, cause)`
after tearing down whatever was already created, so no MCP connection or
checkpointer is left hanging:

```python
from prismal.core.exceptions import RuntimeCompositionError

try:
    ctx = await build_runtime(settings)
except RuntimeCompositionError as exc:
    log.error("composition failed", port=exc.port, cause=exc.cause)
```

---

## 7. Observability

`build_runtime` logs `composition.runtime_built` (with `mode`, `org_id`,
`vector_store_backend`, `collection`) and `aclose` logs
`composition.runtime_teardown` — parity with `mcp_initialized` /
`vector_store.created`.

---

## 8. Host read surface (HRS, 3.17.0)

`prismal-server` 0.2 reads engine state only through a small, versioned,
**read-only** surface (`specs/composition-root/DELTA-2026-10-host-read-surface.md`).
Everything is reached as **methods on the `RuntimeContext`** returned by
`build_runtime` (no new imports); the frozen view dataclasses live in
`prismal.composition.host_surface` (re-exported from `prismal.composition`) and
may be imported for annotations / `isinstance` only. Their field names are the
host wire names.

### Per-tenant configuration — `build_runtime(config_source=)`

```python
ctx = await build_runtime(org_id="acme", config_source=tenant_source, mode="context")
```

When `config_source` is given the runtime's settings are
`build_settings(ChainedConfigSource([config_source, <process source>]))` — the
tenant source wins, the process source (the injected global one, else the
default `EnvConfigSource`) fills the gaps — then `overrides` are layered on
top. `settings` is ignored in that case. Nothing global is mutated: neither
`set_config_source` nor `os.environ`. With `config_source=None` the behaviour is
unchanged. The effective settings are exposed as `ctx.settings`
(`build_test_runtime(settings=...)` accepts one too).

### Read methods

| Method | Returns | Notes |
|---|---|---|
| `budget_status(*, thread_id=None)` | `BudgetStatusView` | `BudgetGuard.check()` over the thread's per-run meter (`turn` / `session`, keyed by `session_id` = `thread_id`). `status` ∈ `within\|soft_exceeded\|hard_exceeded\|disabled\|unknown`; `breached_dimension` ∈ `tokens\|cost_usd\|calls\|wall_clock_s`; a `0` limit ⇒ `remaining=None`. No `thread_id`, an unseeded thread or another tenant's thread ⇒ `unknown`. `tenant` scope ⇒ `unknown` (Phase C has no tenant-wide meter yet). |
| `await quota_windows()` | `list[QuotaWindowView]` | One view per gateway window (`credit\|5h\|daily\|weekly\|monthly`; engine `day/week/month` are renamed). `source="api"` for the gateway API, `"ledger"` for `QuotaLedger`; ledger USD windows are `unit="usd_equivalent"`. A failing gateway is omitted and counted. |
| `audit_page(*, page=1, page_size=50, since=None)` | `AuditPageView` | Newest first, `page_size ≤ 200`, `seq` = 1-based record number in the log. Tenant contexts see records whose `org_id` field matches; records without `org_id` are visible only to an `org_id=None` context. `fields` is hash-first via an **allowlist**: numbers, booleans and `None` pass; a string (≤ 256 chars) passes only under a known-safe key (identifiers, agent/tool/model names, status/outcome/mode verdicts, and names ending in `_hash`, `_count`, `_length`, `_ms`, `_id`, …); every other value — any other string (e.g. `params`, `args`, `messages`, `inputs`, `result_preview`, `reason`) and every nested dict / list under any key — becomes `sha256:<16 hex>`. Bounded work: only the newest 100 000 records (`host_surface.AUDIT_SCAN_MAX_RECORDS`) are parsed; on a larger log older records are not paged and `total=None` (unknown). `total=None` when the audit port is not a file-backed `AuditLogger`. |
| `verify_audit_chain(*, max_records=10_000)` | `AuditChainStatus` | `valid`, `broken` (+ `first_broken_seq`) or `unverified` (non-file audit port, unreadable log, or more than `max_records` records). Verifies the whole log file (the chain is process-wide). |
| `decisions_report(*, since=None, until=None)` | `DecisionsReportView` | One row per DEC integration: `mode` (`resolve_mode`), `samples` / `agreement_ratio` from `ShadowRunner.report()` (`None` when `samples=0`), outcome counts and `llm_calls_avoided` from the in-process recorder. `decisions_enabled=False` ⇒ `enabled=False`, no rows. |
| `routing_report(*, since=None, until=None)` | `RoutingReportView` | Per routing policy: decisions (by tier), escalations (by reason), plan-cache hits, `estimated_savings_usd` = Σ `prismal.routing_savings_usd` µUSD / 1e6. Escalations and plan-cache hits carry no policy label at their emit sites, so they are attributed to the configured `routing_policy` (which always has a row). `adaptive_routing_enabled=False` ⇒ `enabled=False`, no rows. |

`a2a_routes()` (HRS1-08) lands with the A2A v1 Agent Card.

**Never raise.** Every method returns its documented fallback (`unknown`,
`unverified`, empty list/page, `enabled=False`) on an internal failure and
increments `prismal.host_surface_errors_total{method}`. No method mutates engine
state.

### Report recorders and tenant scoping

`prismal.monitoring.host_stats` keeps a process `DecisionStatsRecorder` and
`RoutingStatsRecorder`, fed by a tap in `OTelManager.increment_counter` — i.e.
at exactly the same points as the OTel counters (`decisions`,
`llm_calls_avoided`, `routing_decisions`, `routing_escalations`,
`plan_cache_hits`, `routing_savings_usd`), and also when OTel is disabled. They
keep 1-minute buckets for `host_surface_stats_retention_h` (default 168 h), bound
per-bucket key cardinality, and are thread-safe. A report's `window.since` is
clamped to the later of the process start and the retention horizon (never
extrapolated); `until` is clamped to now. Recorders are in-process and reset on
restart.

A sample's tenant comes from its `tenant` / `org_id` attribute when present,
else from the `current_org` ContextVar. The DEC cascade tags `decisions` /
`llm_calls_avoided` with `tenant` from the decide `context["org_id"]`, and AMR
tags `routing_decisions` / `routing_savings_usd` from `state["metadata"]["org_id"]`
— only when known, never invented. Everything else (e.g. `routing_escalations`,
`plan_cache_hits`) relies on the ContextVar, so a multi-tenant host **wraps each
graph run** in `ctx.bind_org()`:

```python
with ctx.bind_org():  # sync context manager over host_stats.use_org(ctx.org_id)
    result = await graph.ainvoke(state, config)
```

`bind_org()` nests and restores the previous binding on exit (also on error);
`org_id=None` binds "no tenant". The A2A inbound handler binds the request's
tenant itself. A report for a context with `org_id=None` sees **all** samples;
a tenant context sees only its own (unattributed samples belong to no tenant).

### Deviation from the DELTA

The DELTA describes the audit chain as xxhash. The L5 `AuditLogger` actually
chains with **SHA-256** over `json.dumps(entry_without_entry_hash,
sort_keys=True)` with `prev_hash` linking from the genesis `"0" * 64`;
`verify_audit_chain` verifies what the code writes.
