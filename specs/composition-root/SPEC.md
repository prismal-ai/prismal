# Prismal Runtime Composition Root — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/composition-root/PLAN.md` |
| **Architecture** | `specs/composition-root/ARCHITECTURE.md` |
| **TASKS** | `specs/composition-root/TASKS.md` |

---

## Convenciones

- `from __future__ import annotations`.
- `build_runtime` es **async** (conecta MCP, abre stores). Los loaders puros son sync.
- Imports de subsistemas opcionales **diferidos**.
- Reutiliza, no reimplementa: `build_default_tool_provider` (Y), `VectorStoreFactory`/`set_vector_store_provider` (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.

---

## Resumen de módulos

| Módulo | Estado | Contenido |
|---|---|---|
| `prismal/composition.py` | NUEVO | `RuntimeConfig`, `RuntimeContext`, `build_runtime`, `build_test_runtime` |
| `prismal/composition/config_sources.py` | NUEVO | `load_mcp_config`, `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`, `collection_for` |
| `prismal/core/config.py` | MODIFICADO | `+ runtime_mode` |
| `prismal/core/exceptions.py` | MODIFICADO | `+ RuntimeCompositionError` |
| `prismal/agents/graph.py` | MODIFICADO | acepta `vector_store_provider` en modo context (consistencia con Z) |

---

## SPEC-CR-001: `RuntimeConfig`

```python
@dataclass(frozen=True)
class RuntimeConfig:
    """Vista inmutable de config resuelta para un runtime (opcionalmente por tenant)."""
    org_id: str | None
    runtime_mode: Literal["global", "context"]
    mcp_config_path: Path | None
    vector_store_backend: str
    collection_base: str
    collection_name: str            # collection_for(collection_base, org_id)
    # campos sensibles (DSN/keys) NO se incluyen aquí en claro; se referencian desde settings
```

## SPEC-CR-002: `RuntimeContext`

```python
@dataclass
class RuntimeContext:
    """Agrupa los puertos compuestos. No añade lógica de negocio."""
    config: RuntimeConfig
    tool_provider: ToolProviderPort          # Fase Y
    vector_store_provider: VectorStoreProviderPort | None   # Fase Z (None si factory-mode)
    embeddings: EmbeddingsPort
    checkpointer: CheckpointPort
    audit: AuditPort
    org_id: str | None

    async def aclose(self) -> None: ...
        # cierra MCP (desconecta servers), vector store (conexiones servidor), checkpointer.
        # Idempotente; no lanza si ya cerrado.

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
    """Compone TODOS los puertos del core en una llamada.

    Pasos:
      1. settings_eff = apply_org_overrides(settings or get_settings(), org_id, overrides)
      2. cfg = resolve RuntimeConfig (backend, collection_name=collection_for(base, org_id), ...)
      3. tool_provider   = await build_default_tool_provider(settings_eff)        [Fase Y]
      4. vstore_provider = (VectorStoreFactory-based provider)(settings_eff)      [Fase Z]
      5. embeddings      = EmbeddingsFactory.create(settings_eff)
      6. checkpointer    = build_checkpointer(settings_eff)
      7. audit           = AuditLogger(...)
      8. if mode == "global":
             set_tool_provider(tool_provider); set_vector_store_provider(vstore_provider)
         # mode == "context": no toca globals; el contexto se pasa a get_async_compiled_graph(...)
      9. return RuntimeContext(...)

    Si algún paso falla -> aclose() de lo creado + raise RuntimeCompositionError.
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
    """Compone un RuntimeContext determinista con fakes (FakeToolProvider/FakeVectorStore),
    sin I/O ni conexiones. aclose() es no-op."""
```

## SPEC-CR-005: Config loaders (`config_sources.py`)

```python
def load_mcp_config(path: Path | None = None) -> "McpConfig": ...
    # parsea config/mcp_servers.yaml (default settings); no conecta.

def resolve_skills_source(settings: Settings) -> "SkillsSource": ...
    # describe dirs available/active/custom y estado activado.

def resolve_vector_store(settings: Settings, org_id: str | None) -> tuple[str, str]: ...
    # -> (backend, collection_name) ; collection_name = collection_for(base, org_id)

def apply_org_overrides(settings: Settings, org_id: str | None,
                        overrides: dict[str, Any] | None) -> Settings: ...
    # devuelve settings efectivos para el tenant (no muta el global).

def collection_for(base: str, org_id: str | None) -> str:
    return base if org_id is None else f"{base}_{org_id}"
```

## SPEC-CR-006: Settings (`core/config.py`)

```python
runtime_mode: Literal["global", "context"] = "global"
# Unifica tool_provider_mode (Y) y el modo de vector store (Z): build_runtime los propaga.
# Retrocompat: si runtime_mode no se setea, se deriva de tool_provider_mode cuando exista.
```

## SPEC-CR-007: Excepción (`core/exceptions.py`)

```python
class RuntimeCompositionError(PrismalError):
    """Falla al componer el runtime. Lleva el nombre del puerto que falló."""
    def __init__(self, port: str, cause: str) -> None:
        super().__init__(f"Failed to compose runtime port '{port}': {cause}")
```

## SPEC-CR-008: Integración con el grafo (modo context)

`get_async_compiled_graph` ya acepta `tool_provider` (Y) y `vector_store_provider` (Z, según `specs/vector-store-port`). El composition root provee ambos desde el `RuntimeContext`:

```python
graph = await get_async_compiled_graph(
    tool_provider=ctx.tool_provider,
    vector_store_provider=ctx.vector_store_provider,
)
```
En modo global no hace falta pasarlos (ya inyectados como singletons).

---

## Contrato del Host — `prismal-server` (lifespan)

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
    return ctx, graph     # el caller hace ctx.aclose() al terminar
```

---

## Contrato del Dashboard — `prismal-dashboard` (config)

El dashboard lee/edita las fuentes que `build_runtime` consume; **no** llama a `build_runtime` (eso lo hace el server). Esquema estable:

| Sección UI | Fuente que el composition root resuelve |
|---|---|
| **MCP servers** | `config/mcp_servers.yaml` (vía `load_mcp_config`) |
| **Skills** | dirs `available/active/custom` (vía `resolve_skills_source`) |
| **Settings → Vector store** | `settings.vector_store_backend` + `vector_store_path/url` |
| **Settings → Runtime** | `settings.runtime_mode` |

El dashboard persiste cambios; el server re-compone (reinicio o hot-reload futuro).

---

## Compatibilidad y Versionado

- `build_runtime`, `RuntimeContext`, `RuntimeConfig` son **API pública** (SemVer; breaking → minor + deprecación 1 release).
- **Opt-in:** sin `build_runtime`, la inyección individual de Fase Y/Z y los defaults siguen válidos.
- No cambia firmas de nodos ni de patrones RAG/memoria.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Especificación inicial — composition root, loaders, contratos host/dashboard |
