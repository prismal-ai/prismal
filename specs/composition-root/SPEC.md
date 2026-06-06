# Prismal Runtime Composition Root — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/composition-root/PLAN.md` |
| **Architecture** | `specs/composition-root/ARCHITECTURE.md` |
| **TASKS** | `specs/composition-root/TASKS.md` |

---

## Conventions

- `from __future__ import annotations`.
- `build_runtime` is **async** (connects MCP, opens stores). The pure loaders are sync.
- Imports of optional subsystems **deferred**.
- Reuse, do not reimplement: `build_default_tool_provider` (Y), `VectorStoreFactory`/`set_vector_store_provider` (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.

---

## Module Summary

| Module | Status | Content |
|---|---|---|
| `prismal/composition.py` | NEW | `RuntimeConfig`, `RuntimeContext`, `build_runtime`, `build_test_runtime` |
| `prismal/composition/config_sources.py` | NEW | `load_mcp_config`, `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`, `collection_for` |
| `prismal/core/config.py` | MODIFIED | `+ runtime_mode` |
| `prismal/core/exceptions.py` | MODIFIED | `+ RuntimeCompositionError` |
| `prismal/agents/graph.py` | MODIFIED | accepts `vector_store_provider` in context mode (consistency with Z) |

---

## SPEC-CR-001: `RuntimeConfig`

```python
@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable view of resolved config for a runtime (optionally per tenant)."""
    org_id: str | None
    runtime_mode: Literal["global", "context"]
    mcp_config_path: Path | None
    vector_store_backend: str
    collection_base: str
    collection_name: str            # collection_for(collection_base, org_id)
    # sensitive fields (DSN/keys) are NOT included here in cleartext; they are referenced from settings
```

## SPEC-CR-002: `RuntimeContext`

```python
@dataclass
class RuntimeContext:
    """Groups the composed ports. Adds no business logic."""
    config: RuntimeConfig
    tool_provider: ToolProviderPort          # Phase Y
    vector_store_provider: VectorStoreProviderPort | None   # Phase Z (None if factory-mode)
    embeddings: EmbeddingsPort
    checkpointer: CheckpointPort
    audit: AuditPort
    org_id: str | None

    async def aclose(self) -> None: ...
        # closes MCP (disconnects servers), vector store (server connections), checkpointer.
        # Idempotent; does not raise if already closed.

    async def __aenter__(self) -> RuntimeContext: ...
    async def __aexit__(self, *exc) -> None: ...   # -> aclose()
```

## SPEC-CR-003: `build_runtime` (composition root)

```python
async def build_runtime(
    settings: Settings | None = None,
    *,
    org_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    mode: Literal["global", "context"] | None = None,   # None -> settings.runtime_mode
) -> RuntimeContext:
    """Composes ALL the core ports in one call.

    Steps:
      1. settings_eff = apply_org_overrides(settings or get_settings(), org_id, overrides)
      2. cfg = resolve RuntimeConfig (backend, collection_name=collection_for(base, org_id), ...)
      3. tool_provider   = await build_default_tool_provider(settings_eff)        [Phase Y]
      4. vstore_provider = (VectorStoreFactory-based provider)(settings_eff)      [Phase Z]
      5. embeddings      = EmbeddingsFactory.create(settings_eff)
      6. checkpointer    = build_checkpointer(settings_eff)
      7. audit           = AuditLogger(...)
      8. if mode == "global":
             set_tool_provider(tool_provider); set_vector_store_provider(vstore_provider)
         # mode == "context": does not touch globals; the context is passed to get_async_compiled_graph(...)
      9. return RuntimeContext(...)

    If any step fails -> aclose() of what was created + raise RuntimeCompositionError.
    """
```

## SPEC-CR-004: `build_test_runtime` (tests)

```python
def build_test_runtime(
    *,
    tool_provider: ToolProviderPort | None = None,
    vector_store: VectorStorePort | None = None,
    embeddings: EmbeddingsPort | None = None,
    org_id: str | None = None,
) -> RuntimeContext:
    """Composes a deterministic RuntimeContext with fakes (FakeToolProvider/FakeVectorStore),
    with no I/O or connections. aclose() is a no-op."""
```

## SPEC-CR-005: Config loaders (`config_sources.py`)

```python
def load_mcp_config(path: Path | None = None) -> "McpConfig": ...
    # parses config/mcp_servers.yaml (default settings); does not connect.

def resolve_skills_source(settings: Settings) -> "SkillsSource": ...
    # describes available/active/custom dirs and activated state.

def resolve_vector_store(settings: Settings, org_id: str | None) -> tuple[str, str]: ...
    # -> (backend, collection_name) ; collection_name = collection_for(base, org_id)

def apply_org_overrides(settings: Settings, org_id: str | None,
                        overrides: dict[str, Any] | None) -> Settings: ...
    # returns effective settings for the tenant (does not mutate the global).

def collection_for(base: str, org_id: str | None) -> str:
    return base if org_id is None else f"{base}_{org_id}"
```

## SPEC-CR-006: Settings (`core/config.py`)

```python
runtime_mode: Literal["global", "context"] = "global"
# Unifies tool_provider_mode (Y) and the vector store mode (Z): build_runtime propagates them.
# Backward-compat: if runtime_mode is not set, it is derived from tool_provider_mode when present.
```

## SPEC-CR-007: Exception (`core/exceptions.py`)

```python
class RuntimeCompositionError(PrismalError):
    """Failure composing the runtime. Carries the name of the port that failed."""
    def __init__(self, port: str, cause: str) -> None:
        super().__init__(f"Failed to compose runtime port '{port}': {cause}")
```

## SPEC-CR-008: Integration with the graph (context mode)

`get_async_compiled_graph` already accepts `tool_provider` (Y) and `vector_store_provider` (Z, per `specs/vector-store-port`). The composition root provides both from the `RuntimeContext`:

```python
graph = await get_async_compiled_graph(
    tool_provider=ctx.tool_provider,
    vector_store_provider=ctx.vector_store_provider,
)
```
In global mode there is no need to pass them (already injected as singletons).

---

## Host Contract — `prismal-server` (lifespan)

```python
from contextlib import asynccontextmanager
from prismal.composition import build_runtime
from prismal.agents.graph import get_async_compiled_graph
from prismal.core.config import get_settings

@asynccontextmanager
async def lifespan(app):
    ctx = await build_runtime(get_settings())          # mode=global
    app.state.runtime = ctx
    app.state.graph = await get_async_compiled_graph()
    try:
        yield
    finally:
        await ctx.aclose()
```

### Per-tenant (context)
```python
async def graph_for_org(org_id: str):
    ctx = await build_runtime(get_settings(), org_id=org_id, mode="context")
    graph = await get_async_compiled_graph(
        tool_provider=ctx.tool_provider,
        vector_store_provider=ctx.vector_store_provider,
    )
    return ctx, graph     # the caller calls ctx.aclose() when done
```

---

## Dashboard Contract — `prismal-dashboard` (config)

The dashboard reads/edits the sources that `build_runtime` consumes; it does **not** call `build_runtime` (that is the server's job). Stable schema:

| UI Section | Source that the composition root resolves |
|---|---|
| **MCP servers** | `config/mcp_servers.yaml` (via `load_mcp_config`) |
| **Skills** | dirs `available/active/custom` (via `resolve_skills_source`) |
| **Settings → Vector store** | `settings.vector_store_backend` + `vector_store_path/url` |
| **Settings → Runtime** | `settings.runtime_mode` |

The dashboard persists changes; the server re-composes (restart or future hot-reload).

---

## Compatibility and Versioning

- `build_runtime`, `RuntimeContext`, `RuntimeConfig` are **public API** (SemVer; breaking → minor + 1-release deprecation).
- **Opt-in:** without `build_runtime`, the individual injection of Phase Y/Z and the defaults remain valid.
- Does not change the signatures of nodes or RAG/memory patterns.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial specification — composition root, loaders, host/dashboard contracts |
