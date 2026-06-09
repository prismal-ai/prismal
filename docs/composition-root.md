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
