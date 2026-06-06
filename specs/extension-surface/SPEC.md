# Prismal Extension Surface — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN** | `specs/extension-surface/PLAN.md` |
| **Architecture** | `specs/extension-surface/ARCHITECTURE.md` |
| **TASKS** | `specs/extension-surface/TASKS.md` |

---

## Convenciones

- `from __future__ import annotations` en todos los módulos.
- Async donde aplique; sync sólo para utilidades puras (sniff, validation).
- Dataclasses `frozen` donde aplique; tipos públicos exportados desde `__init__.py`.
- Sin nuevas dependencias obligatorias — todo construido sobre stack existente.
- API pública versionada con SemVer; cambios breaking requieren deprecation cycle.
- Excepciones propias heredan de `PrismalError` (extensión a `core/exceptions.py`).

---

## Resumen de módulos

| SPEC | Archivo | Componentes |
|---|---|---|
| SPEC-EXT-001 | `prismal/langgraph.py` | Re-exports + `VERSION` |
| SPEC-EXT-002 | `prismal/agents/extension/decorators.py` | `@prismal_node`, `NodeMetadata`, `list_registered_nodes()` |
| SPEC-EXT-003 | `prismal/agents/extension/builder.py` | `PrismalStateGraphBuilder` |
| SPEC-EXT-004 | `prismal/agents/extension/plugins.py` | `discover_plugins()`, `DiscoveryReport`, `PluginRegistry` |
| SPEC-EXT-005 | `prismal/agents/extension/adapters.py` | `LangChainRunnableAdapter` |
| SPEC-EXT-006 | `prismal/agents/extension/ports.py` | `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` |
| SPEC-EXT-007 | `prismal/agents/extension/_middleware.py` | Middleware chain (internal) |
| SPEC-EXT-008 | `prismal/plugins.py` | CLI `python -m prismal.plugins` |

---

## SPEC-EXT-001: LangGraph Re-export

**Archivo:** `prismal/langgraph.py`

```python
"""Official LangGraph passthrough for prismal extensions.

This module re-exports the LangGraph symbols that prismal supports as a
public extension surface. Importing from here (rather than directly from
``langgraph.*``) guarantees:

* Version compatibility — ``VERSION`` is the LangGraph version prismal
  was tested against.
* Stable contract — only the symbols below are part of the prismal
  extension API; other LangGraph internals may change without notice.

Example::

    from prismal.langgraph import StateGraph, START, END, Send, add_messages
    from prismal.agents.state import AgentState

    graph = StateGraph(AgentState)
    graph.add_node("my_node", my_node)
    graph.add_edge(START, "my_node")
    graph.add_edge("my_node", END)
    compiled = graph.compile()
"""
from __future__ import annotations

from importlib.metadata import version as _pkg_version

from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from prismal.agents.state import AgentState
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

VERSION: str = _pkg_version("langgraph")
"""Installed LangGraph version, resolved at import time."""

__all__ = [
    "StateGraph",
    "START",
    "END",
    "Send",
    "interrupt",
    "add_messages",
    "CompiledStateGraph",
    "AgentState",
    "SubgraphDefinition",
    "SubgraphRegistry",
    "VERSION",
]
```

---

## SPEC-EXT-002: `@prismal_node` Decorator

**Archivo:** `prismal/agents/extension/decorators.py`

### Tipos

```python
from typing import Callable, Awaitable, Literal, TypedDict

SecurityLevel = Literal["off", "standard", "strict"]
"""
'off'      — no security middleware (max performance, max risk).
'standard' — InputSanitizer + SecurePromptBuilder on last user message.
'strict'   — standard + ActionInterceptor.check() on any tool_call in state_update.
"""

@dataclass(frozen=True)
class NodeMetadata:
    """Metadata of a node registered via @prismal_node."""
    name: str
    capabilities: tuple[str, ...]
    security: SecurityLevel
    audit: bool
    retry: RetryPolicy | None
    timeout_s: float | None
    raise_on_error: bool
    registered_at: str          # ISO timestamp
    source_module: str          # módulo donde se definió

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_s: tuple[float, ...] = (0.1, 0.5, 1.0)
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)

NodeFn = Callable[[AgentState], Awaitable[dict]]
"""Signature de un nodo: async (state) → state_update."""
```

### Decorator Principal

```python
def prismal_node(
    *,
    name: str | None = None,
    capabilities: list[str] | None = None,
    security: SecurityLevel = "standard",
    audit: bool = True,
    retry: RetryPolicy | None = None,
    timeout_s: float | None = None,
    raise_on_error: bool = False,
) -> Callable[[NodeFn], NodeFn]:
    """Decorator que envuelve un nodo LangGraph con cross-cutting de prismal.

    Aplica una middleware chain ordenada:
        1. security (InputSanitizer + SecurePromptBuilder + ActionInterceptor)
        2. OTel span "prismal.ext.node.<name>"
        3. logger bind {node_name, session_id, capabilities}
        4. retry con exponential backoff
        5. asyncio.wait_for(timeout_s)
        6. user function
        7. audit log (hash del state_update + duration_ms)
        8. error mapping a NodeExecutionError

    Args:
        name: Nombre del nodo (default: nombre de la función).
        capabilities: Lista de capabilities MCP requeridas.
            Se registran automáticamente en DEFAULT_CAPABILITY_MAP.
        security: Nivel de security middleware.
        audit: Si True, AuditLogger.log_node() por invocación.
        retry: Política de retry. None = sin retries.
        timeout_s: Timeout por invocación. None = sin timeout.
        raise_on_error: Si True, propaga excepciones del usuario.
            Si False (default), las captura y retorna
            {"metadata": {"error": {...}}}.

    Returns:
        Decorator que envuelve `NodeFn`.

    Example::

        @prismal_node(name="my_classifier", capabilities=["general"])
        async def my_classifier(state: AgentState) -> dict:
            last = state["messages"][-1].content
            label = await classify(last)
            return {"metadata": {"my_classifier": {"label": label}}}
    """
    ...


def list_registered_nodes() -> list[NodeMetadata]:
    """Retorna la lista de nodos registrados via @prismal_node.

    Útil para introspección, debugging, y para que el supervisor
    descubra nodos custom dinámicamente.
    """
    ...


def get_node_metadata(name: str) -> NodeMetadata | None:
    """Retorna metadata de un nodo registrado, o None."""
    ...
```

### Atributo del callable wrapeado

```python
# El decorator añade al callable retornado:
wrapped.__prismal_node__: NodeMetadata    # introspección y deduplicación por el builder
wrapped.__wrapped__: NodeFn                 # función original (via functools.wraps)
```

---

## SPEC-EXT-003: `PrismalStateGraphBuilder`

**Archivo:** `prismal/agents/extension/builder.py`

### Tipos

```python
@dataclass(frozen=True)
class BuilderDefaults:
    """Defaults aplicados por add_node si el callable no tiene @prismal_node."""
    security: SecurityLevel = "standard"
    audit: bool = True
    timeout_s: float | None = None
    retry: RetryPolicy | None = None
```

### Clase Principal

```python
class PrismalStateGraphBuilder:
    """Fluent builder sobre StateGraph[AgentState] con defaults de prismal.

    Cada call a add_node() detecta si el callable ya tiene @prismal_node
    aplicado (vía atributo __prismal_node__) y, si no, lo wrapea con los
    defaults del builder.

    Args:
        name: Nombre del subgraph (para SubgraphDefinition y trazas).
        defaults: BuilderDefaults aplicados a nodos sin decorator.
        settings: Settings de prismal.

    Example::

        builder = PrismalStateGraphBuilder("my_pipeline")
        builder.add_node("classify", classify_fn, capabilities=["general"])
        builder.add_node("respond", respond_fn)
        builder.add_edge("classify", "respond")
        builder.set_entry_point("classify")
        subgraph = builder.compile()
        register_my_pipeline(registry, subgraph)
    """

    def __init__(
        self,
        name: str,
        *,
        defaults: BuilderDefaults | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    def add_node(
        self,
        name: str,
        fn: NodeFn,
        *,
        capabilities: list[str] | None = None,
        security: SecurityLevel | None = None,
        audit: bool | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> PrismalStateGraphBuilder:
        """Añade un nodo con auto-wrap si no tiene @prismal_node.

        Args:
            name: Identificador del nodo.
            fn: Callable async (state) → state_update.
            capabilities, security, audit, timeout_s, retry:
                Override de los defaults del builder.

        Returns:
            self (fluent).

        Raises:
            ValueError: Si name ya está registrado en este builder.
        """
        ...

    def add_supervisor_node(
        self,
        routing_fn: Callable[[AgentState], Awaitable[str]],
        *,
        valid_next: list[str],
        name: str = "supervisor",
    ) -> PrismalStateGraphBuilder:
        """Añade un nodo supervisor con validación de routing.

        Args:
            routing_fn: async (state) → next_node_name.
            valid_next: Lista de nodos válidos como destino.
            name: Nombre del nodo (default "supervisor").

        Raises:
            ValueError en runtime si routing_fn retorna un nombre no en valid_next.
        """
        ...

    def add_security_layer(
        self,
        *,
        at: Literal["entry", "exit"] = "entry",
        sanitizer: InputSanitizer | None = None,
    ) -> PrismalStateGraphBuilder:
        """Añade un nodo dedicado de sanitización (entry o exit del subgraph).

        Útil cuando hay nodos con security='off' pero se quiere garantizar
        sanitización al menos en los bordes del subgraph.
        """
        ...

    def add_edge(self, from_: str, to: str) -> PrismalStateGraphBuilder: ...

    def add_conditional_edges(
        self,
        from_: str,
        decision_fn: Callable[[AgentState], str],
        mapping: dict[str, str],
    ) -> PrismalStateGraphBuilder: ...

    def set_entry_point(self, name: str) -> PrismalStateGraphBuilder: ...

    def compile(self) -> SubgraphDefinition:
        """Compila y retorna SubgraphDefinition (registrable en SubgraphRegistry).

        Returns:
            SubgraphDefinition con name, compiled_graph, metadata.
        """
        ...

    def compile_raw(self) -> CompiledStateGraph:
        """Escape hatch: retorna CompiledStateGraph sin envolver en SubgraphDefinition."""
        ...
```

---

## SPEC-EXT-004: Plugin Discovery

**Archivo:** `prismal/agents/extension/plugins.py`

### Tipos

```python
PluginGroup = Literal["subgraphs", "nodes", "tools", "rag_engines"]
"""Entry point groups soportados:
- prismal.subgraphs: callables register_<name>(registry).
- prismal.nodes: callables decorados con @prismal_node.
- prismal.tools: BaseTool de LangChain.
- prismal.rag_engines: clases con protocolo RAGEngineProtocol.
"""

@dataclass(frozen=True)
class PluginInfo:
    """Información de un plugin descubierto."""
    name: str                    # nombre del entry point
    group: PluginGroup
    module: str                  # módulo de origen
    object_name: str             # callable/clase exportado
    dist_name: str               # nombre del paquete distribución
    dist_version: str            # versión del paquete

@dataclass(frozen=True)
class PluginLoadResult:
    info: PluginInfo
    status: Literal["loaded", "error", "skipped_by_denylist", "skipped_not_in_allowlist"]
    error: str | None = None
    duration_ms: float = 0.0

@dataclass(frozen=True)
class DiscoveryReport:
    """Reporte agregado de discover_plugins()."""
    loaded: list[PluginLoadResult]
    failed: list[PluginLoadResult]
    skipped: list[PluginLoadResult]
    total_duration_ms: float

    @property
    def loaded_count(self) -> int: ...
    @property
    def failed_count(self) -> int: ...
```

### Función Principal

```python
def discover_plugins(
    *,
    settings: Settings | None = None,
    registry: SubgraphRegistry | None = None,
    groups: list[PluginGroup] | None = None,
) -> DiscoveryReport:
    """Descubre e instala plugins desde entry points.

    Itera sobre los grupos especificados (default: todos), aplica
    allowlist/denylist desde settings, y llama al callable del entry
    point con la signatura correcta para su grupo:

        - subgraphs: callable(registry: SubgraphRegistry) -> None
        - nodes: callable es ya un @prismal_node (se introspecta y registra)
        - tools: callable retorna BaseTool, se añade al tool_registry
        - rag_engines: callable es una clase RAGEngineProtocol

    Cada carga está aislada en try/except; fallos individuales no
    abortan el resto.

    Args:
        settings: Settings de prismal.
        registry: SubgraphRegistry destino. None usa el global.
        groups: Grupos a descubrir. None = todos.

    Returns:
        DiscoveryReport con loaded/failed/skipped + duración total.

    Side effects:
        - Registra subgraphs en SubgraphRegistry.
        - Registra nodes en _REGISTERED_NODES.
        - Registra tools en tool_registry.
        - Registra rag_engines en RAGEngineRegistry (nuevo en X4).
        - Emite OTel spans y métricas.
        - AuditLogger.log_event("plugin_loaded", ...) por cada carga.
    """
    ...


def list_plugins(*, settings: Settings | None = None) -> list[PluginInfo]:
    """Lista plugins instalados (sin cargarlos)."""
    ...


def get_plugin_info(name: str) -> PluginInfo | None:
    """Información detallada de un plugin por nombre."""
    ...
```

---

## SPEC-EXT-005: LangChain Runnable Adapter

**Archivo:** `prismal/agents/extension/adapters.py`

### Tipos

```python
from langchain_core.runnables import Runnable
from langchain_core.agents import AgentExecutor   # subset de Runnable

InputMapping = Literal["auto", "messages", "input_dict"]
"""
'auto'       — detecta signature del Runnable y mapea.
'messages'   — pasa state["messages"] como List[BaseMessage].
'input_dict' — pasa {"input": last_user_message_content,
                     "chat_history": state["messages"][:-1]}.
"""
```

### Clase Principal

```python
class LangChainRunnableAdapter:
    """Convierte un Runnable / AgentExecutor de LangChain en un nodo prismal.

    Mapea automáticamente state["messages"] al input del Runnable, y el
    output a un state_update válido. Aplica @prismal_node con security
    standard al wrapper resultante.

    Args:
        runnable: Runnable o AgentExecutor a adaptar.
        input_mapping: Cómo mapear state al input del Runnable.
        output_key: Si el Runnable retorna dict, key del output
            (default "output" para AgentExecutor, None auto-detecta).
        settings: Settings.

    Example::

        from langchain.agents import AgentExecutor

        my_agent = AgentExecutor(agent=..., tools=...)
        adapter = LangChainRunnableAdapter(my_agent)
        node = adapter.as_node(name="legacy_research", capabilities=["research"])

        builder = PrismalStateGraphBuilder("hybrid")
        builder.add_node("classify", classify_fn)
        builder.add_node("legacy_research", node)
        builder.add_edge("classify", "legacy_research")
        subgraph = builder.compile()
    """

    def __init__(
        self,
        runnable: Runnable,
        *,
        input_mapping: InputMapping = "auto",
        output_key: str | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    def as_node(
        self,
        *,
        name: str,
        capabilities: list[str] | None = None,
        security: SecurityLevel = "standard",
        timeout_s: float | None = None,
    ) -> NodeFn:
        """Retorna un callable async (state) → state_update con @prismal_node aplicado.

        Args:
            name: Nombre del nodo.
            capabilities: Capabilities MCP.
            security: Nivel de security middleware.
            timeout_s: Timeout.

        Returns:
            NodeFn listo para add_node().

        Raises:
            LangChainAdapterError: Si el Runnable tiene firma incompatible.
        """
        ...

    async def ainvoke(self, state: AgentState) -> dict:
        """Invocación directa (para tests; normalmente se usa as_node())."""
        ...
```

---

## SPEC-EXT-006: Ports (Hexagonal)

**Archivo:** `prismal/agents/extension/ports.py`

```python
from typing import Protocol, runtime_checkable, Any, AsyncIterator

@runtime_checkable
class CheckpointPort(Protocol):
    """Interfaz para persistencia de estado del grafo.

    Implementaciones existentes que cumplen:
        - langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver
        - langgraph.checkpoint.postgres.aio.AsyncPostgresSaver

    Usuarios pueden implementar sus propios checkpointers (Redis,
    DynamoDB, etc.) y sustituir vía build_checkpointer() en config.
    """
    async def aget(self, config: dict) -> Any | None: ...
    async def aput(self, config: dict, checkpoint: Any, metadata: dict) -> None: ...
    async def alist(self, config: dict, *, limit: int | None = None,
                    before: dict | None = None) -> AsyncIterator[Any]: ...


@runtime_checkable
class AuditPort(Protocol):
    """Interfaz de audit log append-only.

    Implementación existente que cumple: prismal.security.AuditLogger.

    Usuarios pueden enviar audit a sistemas externos (Splunk, Datadog,
    CloudTrail) implementando este protocolo.
    """
    def log_event(self, event_type: str, payload: dict) -> None: ...
    def log_node(self, node_name: str, session_id: str, status: str,
                 state_hash: str, duration_ms: float) -> None: ...
    def log_media(self, event: str, sha256: str, modality: str,
                  size_bytes: int, duration_s: float | None) -> None: ...


@runtime_checkable
class EmbeddingsPort(Protocol):
    """Interfaz de embeddings.

    Implementaciones existentes que cumplen:
        - langchain_core.embeddings.Embeddings (todos los providers).
        - prismal.rag.embeddings.EmbeddingsFactory.
    """
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def aembed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class ToolPort(Protocol):
    """Interfaz de tool ejecutable.

    Implementación existente que cumple: langchain_core.tools.BaseTool.
    """
    name: str
    description: str
    async def ainvoke(self, args: dict) -> Any: ...


# Helpers para validar conformidad
def conforms_to(obj: Any, port: type[Protocol]) -> bool:
    """Verifica si obj cumple un Protocol estructuralmente."""
    return isinstance(obj, port)
```

---

## SPEC-EXT-007: Middleware Chain (interno)

**Archivo:** `prismal/agents/extension/_middleware.py`

```python
# NOTA: este módulo es INTERNO (prefijo _). No es API pública;
# se documenta sólo para mantenedores de prismal.

from typing import Callable, Awaitable

Middleware = Callable[
    [NodeFn, AgentState, NodeMetadata],
    Awaitable[dict],
]
"""Signatura de un middleware: recibe (next_fn, state, metadata) → state_update."""


# Stack ordenado (orden importa: security primero, error mapping al final)
DEFAULT_MIDDLEWARE_STACK: list[Middleware] = [
    security_middleware,       # InputSanitizer + SecurePromptBuilder + ActionInterceptor
    otel_middleware,           # span open/close
    logger_middleware,         # bind contextual
    retry_middleware,          # retry con backoff
    timeout_middleware,        # asyncio.wait_for
    # → user fn ejecuta aquí ←
    audit_middleware,          # AuditLogger.log_node
    error_mapping_middleware,  # excepciones → state_update con error=True
]


def build_pipeline(
    user_fn: NodeFn,
    metadata: NodeMetadata,
    stack: list[Middleware] | None = None,
) -> NodeFn:
    """Compone el pipeline funcional aplicando middlewares en orden inverso."""
    ...
```

---

## SPEC-EXT-008: CLI

**Archivo:** `prismal/plugins.py`

```python
"""CLI: python -m prismal.plugins <subcommand>

Subcomandos:
    list                 — lista plugins instalados (sin cargarlos).
    info <name>          — detalle de un plugin: versión, entry points, hash.
    doctor               — intenta cargar todos los plugins y reporta errores.
    enable <name>        — añade a allowlist (mutate config file si está disponible).
    disable <name>       — añade a denylist.

Exit codes:
    0 — éxito.
    1 — error general.
    2 — plugin no encontrado.
    3 — error de carga (doctor).
"""

def main(argv: list[str] | None = None) -> int: ...
```

---

## Excepciones

**Archivo:** `prismal/core/exceptions.py` (extensión)

```python
class ExtensionError(PrismalError):
    """Base para errores de la superficie de extensión."""

class NodeExecutionError(ExtensionError):
    """Error capturado durante la ejecución de un nodo decorado."""
    node_name: str
    state_keys: list[str]
    cause: BaseException

class NodeTimeoutError(NodeExecutionError):
    """Timeout en ejecución de nodo."""
    timeout_s: float

class NodeValidationError(NodeExecutionError):
    """El state_update retornado por el nodo no es válido."""

class PluginLoadError(ExtensionError):
    """Error cargando un plugin."""
    plugin_name: str
    entry_point: str
    cause: BaseException

class PluginConflictError(ExtensionError):
    """Dos plugins intentaron registrar el mismo nombre."""
    conflicting_name: str
    plugins: list[str]

class AdapterError(ExtensionError):
    """Base para errores de adapters."""

class LangChainAdapterError(AdapterError):
    """Error en LangChainRunnableAdapter."""
    runnable_type: str
```

---

## Settings (extensión)

**Archivo:** `prismal/core/config.py`

```python
# Plugin discovery
plugins_autodiscover: bool = Field(
    default=True,
    description="Habilita auto-discovery de plugins via entry points al startup.",
)
plugins_allowlist: list[str] = Field(
    default=[],
    description="Si no vacío, sólo se cargan plugins en esta lista. "
                "Recomendado en producción.",
)
plugins_denylist: list[str] = Field(
    default=[],
    description="Plugins a deshabilitar. Tiene precedencia sobre allowlist.",
)
plugins_groups_enabled: list[str] = Field(
    default=["subgraphs", "nodes", "tools", "rag_engines"],
    description="Grupos de entry points a descubrir. "
                "Default: todos los soportados.",
)

# Decorator defaults
extension_default_security: str = Field(
    default="standard",
    description="Default para @prismal_node sin security explícito.",
)
extension_default_audit: bool = Field(
    default=True,
    description="Default para @prismal_node sin audit explícito.",
)
extension_default_timeout_s: float | None = Field(
    default=None,
    description="Default timeout para @prismal_node (None = sin timeout).",
)
```

Env vars:

```
PRISMAL_PLUGINS_AUTODISCOVER=true
PRISMAL_PLUGINS_ALLOWLIST=["prismal_x_finance","prismal_x_healthcare"]
PRISMAL_PLUGINS_DENYLIST=["broken_plugin"]
PRISMAL_PLUGINS_GROUPS_ENABLED=["subgraphs","nodes"]
PRISMAL_EXTENSION_DEFAULT_SECURITY=strict
PRISMAL_EXTENSION_DEFAULT_AUDIT=true
PRISMAL_EXTENSION_DEFAULT_TIMEOUT_S=30
```

---

## Plugin Author Contract

Para que un plugin sea descubierto y cargado, su `pyproject.toml` debe declarar entry points en uno o más de los grupos soportados.

### Ejemplo: plugin que aporta un subgraph

```toml
# prismal-x-healthcare/pyproject.toml
[project]
name = "prismal-x-healthcare"
version = "0.1.0"
dependencies = ["prismal>=3.0.0"]

[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare.plugin:register_healthcare_pipeline"
```

```python
# prismal_x_healthcare/plugin.py
from prismal.agents.extension import PrismalStateGraphBuilder
from prismal.langgraph import SubgraphRegistry

def register_healthcare_pipeline(registry: SubgraphRegistry) -> None:
    builder = PrismalStateGraphBuilder("healthcare_triage")
    builder.add_node("intake", intake_fn)
    builder.add_node("triage", triage_fn)
    builder.add_node("response", response_fn)
    builder.add_edge("intake", "triage")
    builder.add_edge("triage", "response")
    builder.set_entry_point("intake")
    registry.register(builder.compile())
```

### Ejemplo: plugin que aporta nodos sueltos

```toml
[project.entry-points."prismal.nodes"]
medical_classifier = "prismal_x_healthcare.nodes:medical_classifier"
```

```python
# prismal_x_healthcare/nodes.py
from prismal.agents.extension import prismal_node

@prismal_node(name="medical_classifier", capabilities=["general"])
async def medical_classifier(state):
    ...
    return {"metadata": {"medical": {"category": "..."}}}
```

### Ejemplo: plugin con multiple groups

```toml
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare.plugin:register_healthcare_pipeline"

[project.entry-points."prismal.nodes"]
medical_classifier = "prismal_x_healthcare.nodes:medical_classifier"

[project.entry-points."prismal.tools"]
fhir_lookup = "prismal_x_healthcare.tools:fhir_lookup_tool"
```

---

## Compatibilidad y Versionado

- La superficie de extensión es API pública con compromiso SemVer.
- Cambios breaking requieren bump de minor (pre-1.0) o major (post-1.0).
- Deprecations vía `warnings.warn(DeprecationWarning)` con 1 minor de aviso mínimo.
- Decorator interno `@frozen_api` marca las funciones del contrato público; CI valida que sus firmas no cambien sin entrada en `CHANGELOG.md` con justificación.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — contratos para 8 módulos de extensión |
