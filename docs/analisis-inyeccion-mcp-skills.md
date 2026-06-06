# Análisis: inyección de MCP y Skills desde una capa externa (prismal-sdk / prismal-web)

> Estado: propuesta de arquitectura. No modifica código todavía.
> Alcance: capa de agentes (`prismal/agents`), subsistemas `prismal/mcp` y `prismal/skills`, y superficie de extensión (`prismal/agents/extension`).

## 1. Pregunta

¿Es viable que la incorporación de herramientas **MCP** y **Skills** se haga por **inyección desde un componente distinto** (p. ej. `prismal-sdk`, `prismal-web`) y **no directamente desde la capa de arquitectura de agentes**?

Respuesta corta: **sí, es viable y además es la dirección natural del diseño actual.** El repositorio ya tiene casi todas las piezas (un *facade* de herramientas, puertos hexagonales y un punto de arranque `init_mcp`). Lo que falta es invertir una dependencia: hoy la capa de agentes *baja* a construir MCP y Skills; la propuesta es que un componente externo los *construya e inyecte*.

## 2. Cómo está acoplado hoy

El punto único de integración es `prismal/agents/tool_registry.py`. Funciona como *facade* que mezcla tres fuentes de herramientas por llamada:

1. **MCP** — vía un *singleton* de módulo `_mcp_manager: MCPClientManager`.
2. **Skills** — vía `SkillsManager().get_active_tools()`.
3. **Stubs estáticos** — de `tools.py`, solo como *fallback*.

Cada nodo-agente consume herramientas con una llamada estática por nombre:

```python
# prismal/agents/researcher.py:220, coder.py:169, rag_agent.py:236, ...
tools = get_tools_for_agent("researcher")
```

Y el *registry* alcanza **hacia abajo** a los subsistemas concretos:

```python
# tool_registry.py
from prismal.mcp.client import MCPClientManager      # get_mcp_tools()
from prismal.skills.manager import SkillsManager      # get_skill_tools()
```

Consecuencias del diseño actual:

- **Dependencia de dirección equivocada.** La capa de agentes (núcleo de orquestación) depende de `prismal.mcp` y `prismal.skills` (subsistemas de integración). El núcleo conoce a sus periféricos.
- **Estado global mutable.** `_mcp_manager`, `_mcp_initialized`, `_mcp_lock` son globales de módulo. El ciclo de vida (qué servidores, qué config, cuándo conectar) queda dentro del núcleo, no en quien arranca la app.
- **Construcción no inyectable.** `init_mcp()` instancia `MCPClientManager(config_path)` internamente. El llamador no puede sustituir el *manager* (p. ej. uno con auth de un usuario web, o un *mock* en tests) sin parchear el módulo.
- **Activación de skills opaca.** `get_skill_tools()` instancia `SkillsManager()` por llamada y devuelve `get_active_tools()`; qué skills están activas es un estado de disco/proceso, no algo que el host controle por sesión.
- **Acoplamiento por nombre.** El mapa agente→stubs y `DEFAULT_CAPABILITY_MAP` viven dentro del registry; añadir un *host* con un set de herramientas distinto obliga a tocar el núcleo.

Dato relevante: `grep` confirma que **nadie dentro de `prismal/` llama a `init_mcp()`**. Ya se espera que el arranque lo dispare un componente externo (el app/SDK hermano). Es decir, el límite ya existe de forma implícita; solo está a medio formalizar.

## 3. Lo que ya juega a favor

El repo tiene una superficie de extensión hexagonal (Fase X, `specs/extension-surface/`) con casi todo lo necesario:

- **`prismal/agents/extension/ports.py`** ya define `Protocol`s estructurales (`CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`). El patrón de "puerto + implementación externa que conforma la forma" está establecido y probado.
- **`ToolPort`** ya modela una herramienta ejecutable (`name`, `description`, `ainvoke`) — la unidad que MCP y Skills producen.
- **`PrismalStateGraphBuilder.add_node(..., capabilities=[...])`** ya acepta capacidades por nodo, y `DEFAULT_CAPABILITY_MAP` ya enruta capacidades por agente (Fase E).
- **`discover_plugins(settings)`** ya inyecta subgrafos/nodos/tools/rag-engines de terceros vía *entry points*, con allowlist/denylist. El precedente de "el host registra, el núcleo consume" ya existe.

En otras palabras: para puertos de *checkpoint*, *audit* y *embeddings* la inversión de dependencia **ya está hecha**. MCP y Skills son la excepción que falta normalizar.

## 4. Propuesta: un `ToolProviderPort` inyectado en composición

La idea central es introducir un **puerto de proveedor de herramientas** y mover la *construcción* de MCP/Skills fuera del núcleo, al componente que compone la aplicación (`prismal-sdk` / `prismal-web`).

### 4.1 Nuevo puerto (en `extension/ports.py`)

```python
@runtime_checkable
class ToolProviderPort(Protocol):
    """Fuente de herramientas resoluble por agente/capacidad, en runtime."""

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[ToolPort]: ...
```

Implementaciones que conforman esta forma (todas viven **fuera** del núcleo de agentes):

- `McpToolProvider` — envuelve `MCPClientManager` (mueve la lógica de `get_mcp_tools` + el cap `_MAX_MCP_TOOLS`).
- `SkillToolProvider` — envuelve `SkillsManager.get_active_tools()`.
- `StubToolProvider` — los *fallbacks* de `tools.py` (puede quedarse como *default* del núcleo).
- `CompositeToolProvider` — fusiona N proveedores aplicando la estrategia de prioridad y dedupe que hoy hace `get_tools_for_agent` (MCP → Skills → stubs, con `_MAX_TOTAL_TOOLS`).

### 4.2 Inyección por contexto, no por singleton

Sustituir el *singleton* de módulo por un proveedor resuelto desde el contexto de composición. Dos variantes, de menor a mayor cambio:

**(a) Registro de proveedor inyectable (cambio mínimo, retrocompatible).**
`tool_registry` deja de importar `prismal.mcp` y `prismal.skills`. En su lugar expone un *setter*:

```python
# tool_registry.py (núcleo) — sin imports de mcp/ ni skills/
_provider: ToolProviderPort | None = None

def set_tool_provider(p: ToolProviderPort) -> None: ...

def get_tools_for_agent(agent_name, required_capabilities=None):
    if _provider is None:
        return _default_stub_provider.get_tools(agent_name=agent_name)  # fallback
    return _provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)
```

El host (`prismal-sdk`/`prismal-web`) hace, una sola vez en el arranque:

```python
# en prismal-sdk / prismal-web, NO en prismal/agents
from prismal_sdk.tools import McpToolProvider, SkillToolProvider, CompositeToolProvider
from prismal.agents.tool_registry import set_tool_provider

provider = CompositeToolProvider([
    McpToolProvider(MCPClientManager("config/mcp_servers.yaml")),
    SkillToolProvider(SkillsManager()),
    StubToolProvider(),
])
set_tool_provider(provider)
```

Esto invierte la dependencia: el núcleo ya **no conoce** a `prismal.mcp` ni `prismal.skills`; el host los conoce y los inyecta. Los nodos siguen llamando `get_tools_for_agent("coder")` sin cambios → **migración sin tocar los 20+ agentes**.

**(b) Inyección por `AgentState` / config de grafo (más limpia, más invasiva).**
Pasar el proveedor en la config del grafo compilado (`get_async_compiled_graph(tool_provider=...)`) y que cada nodo lo lea del estado/config en vez de un global. Elimina el estado de módulo por completo y habilita **un proveedor distinto por sesión/usuario** (clave para `prismal-web` multi-tenant). Cuesta más porque toca la firma de los nodos.

Recomendación: empezar por **(a)** (desacopla ya, sin regresiones) y dejar **(b)** como evolución cuando `prismal-web` necesite aislamiento por usuario.

### 4.3 Dónde queda cada cosa

| Responsabilidad | Hoy | Propuesta |
|---|---|---|
| Definir el contrato de "fuente de tools" | implícito en `tool_registry` | `ToolProviderPort` en `extension/ports.py` (núcleo) |
| Construir `MCPClientManager`, elegir config, conectar | `tool_registry.init_mcp` (núcleo) | `prismal-sdk` / `prismal-web` (host) |
| Construir `SkillsManager`, decidir skills activas | `tool_registry.get_skill_tools` (núcleo) | `prismal-sdk` / `prismal-web` (host) |
| Estrategia de merge / caps / prioridad | `get_tools_for_agent` (núcleo) | `CompositeToolProvider` (host) o se queda en núcleo como default |
| Consumir tools por nodo | `get_tools_for_agent("name")` | igual (sin cambios) |

## 5. Beneficios

- **Inversión de dependencia correcta.** `prismal/agents` deja de depender de `prismal/mcp` y `prismal/skills`. El núcleo se vuelve publicable y testeable sin servidores MCP ni skills en disco — coherente con el patrón factory-injection que el resto del repo ya usa ("el negocio acepta *callables*, los defaults cablean el provider perezosamente").
- **Ciclo de vida en manos del host.** `prismal-web` puede crear un proveedor por usuario/sesión (auth, allowlist de servidores, skills habilitadas por plan). `prismal-sdk` puede inyectar un proveedor *mock* en tests sin parchear globals.
- **Multi-tenant real.** La variante (b) permite que dos usuarios web vean *toolsets* distintos sin estado global compartido.
- **Coherencia con `discover_plugins`.** Mismo principio que ya rige plugins: el host descubre/registra, el núcleo consume.
- **Frontera de seguridad más clara.** Las capas L1–L5 (`InputSanitizer`, `ActionInterceptor`, `AuditLogger`) siguen en el núcleo y se aplican a *cualquier* tool que entre por el puerto, venga de donde venga. El proveedor externo no puede saltárselas porque la ejecución sigue pasando por `react_loop` y el *middleware* de `@prismal_node`.

## 6. Riesgos y mitigaciones

- **Regresión silenciosa si nadie inyecta el proveedor.** Mitigación: *fallback* a `StubToolProvider` (comportamiento degradado pero funcional) y un *warning* estructurado, igual que hoy `get_mcp_tools` devuelve `[]` si MCP no se inicializó.
- **Orden de arranque.** El host debe inyectar **antes** del primer turno del grafo. Mitigación: documentarlo en el *lifespan* del SDK/web (donde hoy ya se esperaría `init_mcp`).
- **Caps y límites de tokens.** `_MAX_MCP_TOOLS=60` y `_MAX_TOTAL_TOOLS=120` (límite de OpenAI) son política de plataforma, no del host. Mantenerlos en el núcleo o en `CompositeToolProvider` "oficial" para que un host no los rompa por accidente.
- **`filterwarnings=error` en tests.** Cualquier import diferido nuevo debe seguir siendo perezoso para no romper el árbol de imports del núcleo sin extras instalados.
- **Compatibilidad de la rama Fase E.** `DEFAULT_CAPABILITY_MAP` y `required_capabilities` deben seguir fluyendo hasta `provider.get_tools(capabilities=...)`; la firma del puerto ya lo contempla.

## 7. Plan incremental sugerido

1. Añadir `ToolProviderPort` a `extension/ports.py` (aditivo, sin romper nada).
2. Extraer `McpToolProvider` / `SkillToolProvider` / `StubToolProvider` / `CompositeToolProvider` a un módulo de *host* (idealmente en `prismal-sdk`; transitoriamente en `prismal/agents/extension/providers.py` para no bloquear).
3. Refactor de `tool_registry`: reemplazar imports de `mcp`/`skills` por el proveedor inyectado + `set_tool_provider()`, conservando `get_tools_for_agent` como API estable para los nodos.
4. Mover la construcción y el arranque (`init_mcp` equivalente) a `prismal-sdk` / `prismal-web`.
5. (Opcional, fase 2) Variante (b): proveedor por sesión vía config del grafo para multi-tenant.
6. Tests: un `FakeToolProvider` reemplaza el cableado real; los tests del núcleo dejan de necesitar MCP/skills.

## 8. Conclusión

La inyección de MCP/Skills desde `prismal-sdk` / `prismal-web` no solo es posible: es la forma de cerrar una inconsistencia del diseño actual, donde *checkpoint*, *audit* y *embeddings* ya están invertidos como puertos pero MCP y Skills siguen construyéndose dentro del núcleo. El cambio se puede hacer **retrocompatible y sin tocar los 20+ nodos-agente**, concentrando todo en `tool_registry` (un único archivo) más un puerto nuevo. El resultado es un núcleo `prismal` publicable y testeable de forma aislada, con el ciclo de vida de las integraciones donde corresponde: en el componente que compone la aplicación.

### Archivos clave referenciados

- `prismal/agents/tool_registry.py` — *facade* y acoplamiento actual (`get_mcp_tools`, `get_skill_tools`, `init_mcp`, `get_tools_for_agent`).
- `prismal/agents/extension/ports.py` — puertos hexagonales existentes (`ToolPort`, etc.).
- `prismal/agents/extension/builder.py` / `plugins.py` — precedente de inyección por el host.
- `prismal/mcp/client.py` (`MCPClientManager`), `prismal/skills/manager.py` (`SkillsManager`) — subsistemas a envolver como proveedores.
