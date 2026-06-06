# Prismal Tool Provider Injection — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/tool-provider-injection/PLAN.md` |
| **Architecture** | `specs/tool-provider-injection/ARCHITECTURE.md` |
| **TASKS** | `specs/tool-provider-injection/TASKS.md` |

---

## Convenciones

- `from __future__ import annotations` en todos los módulos.
- Imports de `prismal.mcp` / `prismal.skills` **diferidos** (dentro de métodos), nunca a nivel de módulo del núcleo.
- Tipos de retorno de tools: `langchain_core.tools.BaseTool` (conforma `ToolPort`).
- Sync para resolución de tools (paridad con `get_tools_for_agent` actual, que es sync); la conexión async de MCP la hace el host antes de inyectar.
- Todos los símbolos públicos se re-exportan desde `prismal/agents/extension/__init__.py`.

---

## Resumen de módulos

| Módulo | Estado | Contenido |
|---|---|---|
| `prismal/agents/extension/ports.py` | MODIFICADO | `+ ToolProviderPort` |
| `prismal/agents/extension/providers.py` | NUEVO | `McpToolProvider`, `SkillToolProvider`, `StubToolProvider`, `CompositeToolProvider`, `FakeToolProvider`, `build_default_tool_provider` |
| `prismal/agents/extension/__init__.py` | MODIFICADO | re-exports |
| `prismal/agents/tool_registry.py` | MODIFICADO | `set_tool_provider`, `get_tool_provider`, delegación, shims deprecados |
| `prismal/agents/graph.py` | MODIFICADO | `tool_provider` en config (variante B) |
| `prismal/core/config.py` | MODIFICADO | `tool_provider_mode`, `tool_provider_strict` |
| `prismal/core/exceptions.py` | MODIFICADO | `+ ToolProviderNotConfigured` |

---

## SPEC-TPI-001: `ToolProviderPort` (en `ports.py`)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ToolProviderPort(Protocol):
    """Fuente de herramientas resoluble por agente y capacidad, en runtime.

    Conforman esta forma: McpToolProvider, SkillToolProvider, StubToolProvider,
    CompositeToolProvider, FakeToolProvider y cualquier proveedor del host.
    El núcleo solo invoca get_tools(); nunca construye proveedores.
    """

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[ToolPort]: ...
```

Reglas:
- `get_tools` es **sync** y **no debe lanzar** ante una fuente caída: devuelve lo que pueda (lista vacía como mínimo).
- `agent_name` permite a un proveedor (p. ej. `StubToolProvider`) seleccionar tools por agente; los proveedores que no lo usan (MCP, Skills) lo ignoran.
- `capabilities` es el filtro Fase E; `None` = sin filtro (pool completo).

---

## SPEC-TPI-002: `McpToolProvider` (en `providers.py`)

```python
class McpToolProvider:
    def __init__(self, manager: "MCPClientManager", *, max_tools: int = 60) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Tools de servidores MCP conectados, filtradas por capability y capadas a max_tools.

        Equivalente a la lógica actual de tool_registry.get_mcp_tools():
            manager.get_all_langchain_tools(capabilities=capabilities)[:max_tools]
        Captura cualquier excepción del manager y devuelve [] (paridad).
        agent_name se ignora.
        """
```

- `max_tools` por defecto `60` (= `_MAX_MCP_TOOLS` actual).
- Import de `MCPClientManager` **diferido** dentro del método.

---

## SPEC-TPI-003: `SkillToolProvider` (en `providers.py`)

```python
class SkillToolProvider:
    def __init__(self, manager: "SkillsManager | None" = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Tools de skills activas. Equivalente a SkillsManager().get_active_tools().

        capabilities y agent_name se ignoran (paridad: las skills no se filtran hoy).
        Captura excepciones y devuelve [].
        """
```

- Si `manager is None`, instancia `SkillsManager()` perezosamente (import diferido).

---

## SPEC-TPI-004: `StubToolProvider` (en `providers.py`)

```python
class StubToolProvider:
    def __init__(
        self,
        *,
        fixed_tool_agents: frozenset[str] = frozenset({"cron_manager", "critic"}),
    ) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Stubs estáticos de tools.py para agent_name (mapa actual stub_map).

        Encapsula el stub_map de get_tools_for_agent:
            researcher → RESEARCHER_TOOLS
            coder → CODER_TOOLS + SANDBOX_TOOLS
            rag_agent → RAG_AGENT_TOOLS
            critic → CRITIC_TOOLS
            data_analyst → DATA_ANALYST_TOOLS + SANDBOX_TOOLS
            file_manager → FILE_MANAGER_TOOLS
            planner → [read_file, write_file, *CRON_MANAGER_TOOLS]
            cron_manager → CRON_MANAGER_TOOLS
            data_ingester/eda_analyst/feature_engineer/model_trainer/
              model_evaluator/model_exporter → ML_PIPELINE_TOOLS
            market_data_collector/technical_analyst/fundamental_analyst/
              risk_sentiment_analyst/report_generator → []
        Agentes desconocidos → [].
        Imports de tools.py diferidos.
        """
```

- `fixed_tool_agents` se expone para que `CompositeToolProvider` decida la exención (ver SPEC-TPI-005). `StubToolProvider` en sí siempre devuelve el stub set del agente.

---

## SPEC-TPI-005: `CompositeToolProvider` (en `providers.py`)

```python
class CompositeToolProvider:
    def __init__(
        self,
        providers: list[ToolProviderPort],
        *,
        max_total: int = 120,
        fixed_tool_agents: frozenset[str] = frozenset({"cron_manager", "critic"}),
    ) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Fusiona providers reproduciendo EXACTAMENTE get_tools_for_agent:

        1. Si agent_name ∈ fixed_tool_agents:
              devolver SOLO los stubs (último provider de tipo stub), sin MCP ni skills.
        2. live = concat(p.get_tools(...) for p in providers excepto el stub final)
           respetando el orden (MCP → Skills).
        3. stubs = stub_provider.get_tools(agent_name=...)
           filtered_stubs = [s for s in stubs if s.name not in {t.name for t in live}]
        4. merged = live + filtered_stubs
        5. si len(merged) > max_total: truncar cola (drop lowest priority).
        6. log tool_provider.tools_resolved(agent, live, stubs_kept, total).
        """
```

Convención de orden: el **último** proveedor de la lista debe ser el `StubToolProvider` (los stubs son fallback). Los anteriores son fuentes "live" (MCP, Skills). `CompositeToolProvider` identifica el stub provider por `isinstance(p, StubToolProvider)`; si hay varios, el último gana como fuente de fallback.

- `max_total` por defecto `120` (= `_MAX_TOTAL_TOOLS`).
- Un sub-proveedor que lanza se captura, se loguea (`tool_provider.subprovider_error`) y se omite.

---

## SPEC-TPI-006: `FakeToolProvider` (en `providers.py`, para tests)

```python
class FakeToolProvider:
    def __init__(self, mapping: dict[str, list[BaseTool]] | None = None, *, default: list[BaseTool] | None = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Devuelve mapping.get(agent_name, default or []). Determinista, sin I/O."""
```

---

## SPEC-TPI-007: `build_default_tool_provider` (en `providers.py`)

```python
async def build_default_tool_provider(
    settings: "Settings | None" = None,
    *,
    mcp_config_path: "Path | None" = None,
) -> CompositeToolProvider:
    """Arma el CompositeToolProvider estándar para uso del host.

    - Si hay config MCP: construye MCPClientManager(config), await load_from_config(),
      lo envuelve en McpToolProvider. Si falla la conexión: loguea y omite MCP.
    - Construye SkillToolProvider(SkillsManager()).
    - Añade StubToolProvider() como fallback final.
    - Devuelve CompositeToolProvider([mcp?, skill, stub]).

    Pensado para el lifespan de prismal-sdk / prismal-web:
        provider = await build_default_tool_provider(settings)
        set_tool_provider(provider)
    """
```

- Async porque conecta MCP. Es la **única** pieza async del feature; el resto de `get_tools` es sync.
- Vive en `extension/` (host-facing); no la importa el núcleo puro.

---

## SPEC-TPI-008: Registry — inyección y delegación (en `tool_registry.py`)

```python
# Estado de módulo (reemplaza _mcp_manager / _mcp_initialized / _mcp_lock)
_provider: ToolProviderPort | None = None

def set_tool_provider(provider: ToolProviderPort) -> None:
    """Inyecta el proveedor global. Idempotente; el host lo llama una vez al arranque."""

def get_tool_provider() -> ToolProviderPort | None:
    """Devuelve el proveedor global inyectado, o None."""

def get_tools_for_agent(
    agent_name: str,
    required_capabilities: list[str] | None = None,
) -> list[BaseTool]:
    """API ESTABLE (sin cambios de firma). Delega:

    provider = get_tool_provider()
    if provider is None:
        if get_settings().tool_provider_strict:
            raise ToolProviderNotConfigured(agent_name)
        logger.warning("tool_registry.no_provider", agent=agent_name)
        return _DEFAULT_STUB_PROVIDER.get_tools(agent_name=agent_name)
    return provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)
    """

# _DEFAULT_STUB_PROVIDER: StubToolProvider  (singleton de fallback, sin MCP/skills)
```

Shims deprecados (delegan, emiten `DeprecationWarning`):
```python
async def init_mcp(config_path: Path | None = None) -> None:
    """DEPRECATED. Use build_default_tool_provider(settings) + set_tool_provider() en el host."""

def get_mcp_tools(capabilities: list[str] | None = None) -> list[BaseTool]:
    """DEPRECATED. Resuelto por el proveedor inyectado."""

def get_skill_tools() -> list[BaseTool]:
    """DEPRECATED. Resuelto por el proveedor inyectado."""
```

`DEFAULT_CAPABILITY_MAP`, `get_recommended_capabilities`, `react_loop` y todas las constantes de `react_loop` **permanecen sin cambios**.

---

## SPEC-TPI-009: Variante B — proveedor por contexto (en `graph.py`)

```python
async def get_async_compiled_graph(
    *,
    tool_provider: ToolProviderPort | None = None,
    # ... resto de parámetros existentes sin cambios ...
) -> CompiledStateGraph:
    """Si tool_provider se pasa y settings.tool_provider_mode == 'context',
    se guarda en la config compilable bajo configurable.tool_provider.
    """

# Helper de resolución usado por los nodos en modo context:
def resolve_provider(config: "RunnableConfig | None") -> ToolProviderPort | None:
    """Lee config['configurable']['tool_provider'] o cae al global get_tool_provider()."""
```

En modo `context`, `get_tools_for_agent` acepta un `config` opcional o los nodos llaman a un helper `get_tools_for_agent_ctx(agent_name, config, required_capabilities)`. La variante A (global) no requiere `config`.

---

## SPEC-TPI-010: Excepciones (en `core/exceptions.py`)

```python
class ToolProviderNotConfigured(PrismalError):
    """No hay ToolProviderPort inyectado y settings.tool_provider_strict es True."""
    def __init__(self, agent_name: str) -> None:
        super().__init__(
            f"No tool provider configured for agent '{agent_name}'. "
            "Call set_tool_provider(...) at startup, or set "
            "settings.tool_provider_strict=False to fall back to stubs."
        )
```

---

## SPEC-TPI-011: Settings (extensión, en `core/config.py`)

```python
# Tool provider injection
tool_provider_mode: Literal["global", "context"] = "global"
tool_provider_strict: bool = False
```

- `global`: se usa `set_tool_provider()` (variante A).
- `context`: el proveedor se resuelve por sesión desde la config del grafo (variante B).
- `strict`: si `True`, ausencia de proveedor lanza `ToolProviderNotConfigured` en vez de fallback a stubs.

---

## SPEC-TPI-012: Re-exports (en `extension/__init__.py`)

```python
from prismal.agents.extension.ports import ToolProviderPort
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    SkillToolProvider,
    StubToolProvider,
    build_default_tool_provider,
)
```

---

## Host Contract (prismal-sdk / prismal-web)

### Arranque estándar (variante A)
```python
from prismal.agents.extension import build_default_tool_provider
from prismal.agents.tool_registry import set_tool_provider
from prismal.core.config import get_settings

async def on_startup() -> None:
    provider = await build_default_tool_provider(get_settings())
    set_tool_provider(provider)
```

### Toolset por usuario (variante B)
```python
from prismal.agents.extension import (
    CompositeToolProvider, McpToolProvider, SkillToolProvider, StubToolProvider,
)
from prismal.agents.graph import get_async_compiled_graph

async def graph_for_user(user) -> CompiledStateGraph:
    provider = CompositeToolProvider([
        McpToolProvider(await mcp_manager_for(user)),
        SkillToolProvider(skills_for_plan(user.plan)),
        StubToolProvider(),
    ])
    return await get_async_compiled_graph(tool_provider=provider)
```

### Proveedor propio (sustituye el merge)
```python
class MyToolProvider:
    def get_tools(self, *, agent_name, capabilities=None):
        return my_lookup(agent_name, capabilities)   # conforma ToolProviderPort
set_tool_provider(MyToolProvider())
```

---

## Compatibilidad y Versionado

- `ToolProviderPort` + providers son **API pública**; cambios breaking requieren bump minor + `DeprecationWarning` 1 release antes.
- `get_tools_for_agent` mantiene firma y semántica (paridad verificada por test).
- Shims `init_mcp`/`get_mcp_tools`/`get_skill_tools` se eliminan no antes de la versión `X+1` (1 minor de deprecación).

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Especificación inicial de interfaces — `ToolProviderPort`, proveedores, delegación del registry |
