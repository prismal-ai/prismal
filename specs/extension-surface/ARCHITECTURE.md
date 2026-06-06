# Prismal Extension Surface — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN Relacionado** | `specs/extension-surface/PLAN.md` |
| **SPEC Relacionado** | `specs/extension-surface/SPEC.md` |
| **TASKS** | `specs/extension-surface/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |

---

## 1. Contexto

Prismal hoy ofrece extensibilidad implícita (callable injection en Fase B, `SubgraphRegistry` en Fase C), pero sin contrato público ni plugin discovery. Este documento describe la implementación de la **Fase X — Extension Surface**, que convierte esa extensibilidad en una API deliberada con cinco componentes: re-export de LangGraph, decorator `@prismal_node`, builder fluent, plugin discovery vía entry points, adapter LangChain.

Principio rector: **prismal es LangGraph con baterías incluidas, no LangGraph escondido**. La superficie de extensión debe preservar la legibilidad del código de usuario en términos de LangGraph estándar — todo lo que añade prismal es opt-in y observable.

---

## 2. Objetivos Técnicos

- **Compatibilidad cero-fricción con LangGraph upstream:** un usuario que sabe LangGraph debe poder construir un nodo en ≤ 15 minutos sin leer todo el código de prismal.
- **Cross-cutting automatizado:** security, OTel, audit, logging se aplican via decorator/builder sin que el usuario los pida.
- **Plugin auto-discovery declarativo:** `entry_points` estándar de Python; cero magic.
- **Aislamiento de fallos:** un plugin roto no debe romper el startup ni el grafo principal.
- **API congelada:** la superficie de extensión versionada con SemVer; deprecation cycle obligatorio.
- **Sin dependencias nuevas obligatorias:** todo se construye sobre el stack ya instalado.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel — Módulos Nuevos

```
prismal/
├── langgraph.py                       ← [NUEVO] re-export oficial
│
├── agents/
│   └── extension/                     ← [NUEVO subdirectorio público]
│       ├── __init__.py                ← re-exports: prismal_node, builder, plugins, adapters, ports
│       ├── decorators.py              ← @prismal_node + helpers
│       ├── builder.py                 ← PrismalStateGraphBuilder
│       ├── plugins.py                 ← discover_plugins() + PluginRegistry
│       ├── adapters.py                ← LangChainRunnableAdapter
│       ├── ports.py                   ← CheckpointPort, AuditPort, EmbeddingsPort, ToolPort
│       └── _middleware.py             ← internal middleware chain (no público)
│
└── core/
    └── [EXTENSIÓN] config.py          ← plugins_autodiscover, plugins_allowlist, plugins_denylist

examples/
├── custom_node.py                     ← [NUEVO] hello world: @prismal_node
├── custom_subgraph.py                 ← [NUEVO] PrismalStateGraphBuilder
├── langchain_migration.py             ← [NUEVO] AgentExecutor → nodo
└── plugin_template/                   ← [NUEVO] esqueleto cookiecutter
    ├── pyproject.toml
    ├── src/prismal_x_<name>/
    │   ├── __init__.py
    │   └── plugin.py                  ← register_<name>(registry)
    └── README.md

docs/
└── extension.md                        ← [NUEVO] quickstart + recetario
```

### 3.2 Diagrama de Capas

```
                       ┌──────────────────────────────────────┐
                       │       USER / PLUGIN CODE             │
                       │  ┌─────────────────────────────────┐ │
                       │  │ @prismal_node                   │ │
                       │  │ PrismalStateGraphBuilder        │ │
                       │  │ LangChainRunnableAdapter        │ │
                       │  │ register_<name>(registry)       │ │
                       │  └─────────────────────────────────┘ │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────────────┐
                       │   prismal.agents.extension (público) │
                       │  decorators • builder • plugins      │
                       │  adapters • ports                    │
                       └──────┬───────────────────────────────┘
                              │ aplica middleware chain
                              ▼
                       ┌──────────────────────────────────────┐
                       │  _middleware.py (interno)            │
                       │  [security → otel → audit →          │
                       │   retry → execute → format_output]   │
                       └──────┬───────────────────────────────┘
                              │ usa
   ┌──────────────────────────┼──────────────────────────┐
   ▼                          ▼                          ▼
┌──────────────┐    ┌──────────────────┐      ┌────────────────────┐
│ security/    │    │ monitoring/      │      │ providers/         │
│ SecurePrompt │    │ OTelManager      │      │ ProviderRegistry   │
│ ActionInter  │    │ get_logger()     │      │                    │
│ AuditLogger  │    │                  │      │                    │
└──────────────┘    └──────────────────┘      └────────────────────┘
                              │
                              ▼
                       ┌──────────────────────────────────────┐
                       │  prismal.langgraph (re-export)       │
                       │  StateGraph, Send, interrupt,        │
                       │  add_messages, START, END, ...       │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                              langgraph upstream
```

### 3.3 Componentes por Módulo

#### X1 — Re-export oficial

| Símbolo | Origen | Razón |
|---|---|---|
| `StateGraph` | `langgraph.graph.StateGraph` | construcción del grafo |
| `START`, `END` | `langgraph.graph` | nodos sentinel |
| `Send` | `langgraph.constants` | fan-out |
| `interrupt` | `langgraph.types` | HITL |
| `add_messages` | `langgraph.graph.message` | reducer de mensajes |
| `CompiledStateGraph` | `langgraph.graph.state` | tipo del grafo compilado |
| `VERSION` | `importlib.metadata.version("langgraph")` | trazabilidad de versión |

Además se re-exportan los tipos propios de prismal que son inseparables del uso: `AgentState`, `SubgraphDefinition`, `SubgraphRegistry`.

#### X2 — Decorator `@prismal_node`

```
@prismal_node(name=..., capabilities=..., security=..., audit=..., retry=..., timeout_s=...)
async def my_node(state: AgentState) -> dict:
    ...
```

Internamente aplica la **middleware chain** (en `_middleware.py`):

```
1. SECURITY    — opcional: si security="standard", aplica InputSanitizer y SecurePromptBuilder
                  a state["messages"][-1].content antes de pasar al usuario.
                  si security="strict", además llama ActionInterceptor.check() antes
                  de cualquier write_files / execute_code emitido en el state_update.
2. OTEL        — abre span "prismal.ext.node.<name>" con atributos session_id, node_name,
                  state_keys; cierra al final con status=OK|ERROR.
3. LOGGER      — bind contextual con node_name, session_id, capabilities.
4. RETRY       — si retry={"max_attempts": N, "backoff_s": [0.1, 0.5, 1.0]}, reintenta
                  con exponential backoff en excepciones transitorias.
5. TIMEOUT     — wrap con asyncio.wait_for(timeout_s).
6. EXECUTE     — invoca la función del usuario.
7. AUDIT       — si audit=True, AuditLogger.log_node(name, session_id, status,
                  hash(state_update), duration_ms).
8. ERROR MAP   — captura excepciones no PrismalError y las mapea a NodeExecutionError,
                  retornando state_update con metadata["error"]={...} en vez de raise.
9. FORMAT      — valida que el state_update sea dict y retorna.
```

El decorator también ejecuta **registro lateral**:

- Añade `name` a `DEFAULT_CAPABILITY_MAP` en `tool_registry.py` (vía API pública nueva `register_node_capabilities()`).
- Si está definido `capabilities`, declara qué tools MCP debe recibir.
- Mantiene un registro interno `_REGISTERED_NODES: dict[str, NodeMetadata]` para introspección (`list_registered_nodes()`).

#### X3 — Builder fluent

`PrismalStateGraphBuilder` envuelve `StateGraph[AgentState]` y expone:

```
builder = PrismalStateGraphBuilder(name="my_pipeline", settings=...)
builder.add_node(name, fn, *, capabilities=..., security=..., audit=...)
builder.add_supervisor_node(routing_fn, *, valid_next=...)
builder.add_security_layer(at="entry"|"exit")
builder.add_edge(from_, to)
builder.add_conditional_edges(from_, decision_fn, mapping)
builder.set_entry_point(name)
builder.compile() -> SubgraphDefinition
builder.compile_raw() -> CompiledStateGraph     # escape hatch sin SubgraphDefinition
```

`add_node()` detecta si el callable ya tiene `@prismal_node` aplicado (vía atributo `__prismal_node__: NodeMetadata`); si no, lo aplica con los defaults pasados al builder. Esto significa que un usuario puede pasar funciones planas y recibir todas las cross-cutting concerns sin saberlo.

#### X4 — Plugin discovery

```
[project.entry-points."prismal.subgraphs"]
my_pipeline = "prismal_x_mypkg:register_my_pipeline"

[project.entry-points."prismal.nodes"]
my_node = "prismal_x_mypkg.nodes:my_node"

[project.entry-points."prismal.tools"]
my_tool = "prismal_x_mypkg.tools:my_tool"

[project.entry-points."prismal.rag_engines"]
my_engine = "prismal_x_mypkg.rag:MyRAGEngine"
```

`discover_plugins(settings)` itera los cuatro grupos, aplica allowlist/denylist, y para cada entry point:

- Si grupo es `subgraphs` o `nodes`: importa el callable y lo invoca como `register(registry)` o lo añade al registro de nodos.
- Si grupo es `tools`: añade al `tool_registry` respetando el cap de 120.
- Si grupo es `rag_engines`: instancia y registra en un nuevo `RAGEngineRegistry` (también introducido en X4).

Cada carga está aislada en `try/except` con log + métrica. Falla individual no aborta el resto.

CLI helper opcional:
```
python -m prismal.plugins list                 # lista todos los plugins descubiertos
python -m prismal.plugins info <name>          # detalle (versión, hash, entry points)
python -m prismal.plugins doctor               # diagnóstico de errores de carga
```

#### X5 — `LangChainRunnableAdapter`

```python
adapter = LangChainRunnableAdapter(runnable)
adapter.as_node(name="legacy", capabilities=[...])  # devuelve callable @prismal_node-decorated
```

Internamente:
- Inspecciona el `Runnable` para detectar si espera `dict` con keys (`input`, `chat_history`) o `BaseMessage[]`.
- Mapea `state["messages"]` → input del Runnable.
- Mapea output → `{"messages": [AIMessage(content=output)]}` o respeta `state_update` si el Runnable lo retorna ya en formato prismal.
- Soporta `AgentExecutor`, `RunnableSequence`, `RunnableLambda`, `RunnableParallel`.

#### X6 — Ports formalizados

```python
class CheckpointPort(Protocol):
    async def aget(self, config: dict) -> Checkpoint | None: ...
    async def aput(self, config: dict, checkpoint: Checkpoint, metadata: dict) -> None: ...

class AuditPort(Protocol):
    def log_event(self, event_type: str, payload: dict) -> None: ...

class EmbeddingsPort(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...

class ToolPort(Protocol):
    name: str
    description: str
    async def ainvoke(self, args: dict) -> Any: ...
```

Las implementaciones existentes (`AsyncSqliteSaver`, `AuditLogger`, embeddings de ChromaDB, herramientas LangChain `BaseTool`) ya cumplen estos protocolos estructuralmente. El cambio es declararlos explícitamente para que usuarios sustituyan con sus propios adapters.

### 3.4 Flujos de Datos Detallados

#### Flujo X-A: Invocación de nodo con `@prismal_node`

```
LangGraph dispatcher ─▶ wrapper(state)
                     ─▶ [security_mw if enabled]
                          └─ sanitize last message
                     ─▶ [otel_mw] open span "prismal.ext.node.<name>"
                     ─▶ [logger_mw] bind {node_name, session_id}
                     ─▶ [retry_mw] try N times
                          └─ [timeout_mw] asyncio.wait_for
                              └─ user_fn(state) ─▶ state_update
                     ─▶ [audit_mw if enabled] log hash(state_update), duration_ms
                     ─▶ [error_map_mw] if exception → {"metadata":{"error":{...}}}
                     ─▶ return state_update
                     ─▶ [otel_mw] close span with status
```

#### Flujo X-B: Plugin discovery al startup

```
app startup ─▶ get_settings() ─▶ if not plugins_autodiscover: skip
            ─▶ discover_plugins(settings)
                  ├─ entry_points(group="prismal.subgraphs")
                  ├─ filter by allowlist / denylist
                  └─ for each ep:
                        try:
                            fn = ep.load()
                            fn(SubgraphRegistry())     # plugin se registra
                            metric: plugins_loaded_total{status="success"} ++
                            audit: log_event("plugin_loaded", {name, version, ep})
                        except Exception as e:
                            metric: plugins_loaded_total{status="error"} ++
                            log.error("plugin_load_failed", name=..., error=str(e))
                            continue
            ─▶ same para grupos: nodes, tools, rag_engines
            ─▶ return DiscoveryReport(loaded=[...], failed=[...])
```

#### Flujo X-C: LangChain adapter en runtime

```
state ─▶ adapter.as_node returns wrapped_fn
       ─▶ wrapped_fn(state)
              ─▶ extract messages: state["messages"]
              ─▶ build runnable input: detect signature (BaseMessage[] vs dict)
              ─▶ apply SecurePromptBuilder a content si security="standard"
              ─▶ runnable.ainvoke(input)
              ─▶ map output:
                    if AIMessage → {"messages": [output]}
                    if str       → {"messages": [AIMessage(content=output)]}
                    if dict con "messages" → respetar
              ─▶ return state_update
```

---

## 4. Decisiones de Diseño

### DD-EXT-001: Decorator over Base Class

- **Decisión:** Cross-cutting concerns se aplican vía decorator `@prismal_node`, no vía base class `BaseNode` que el usuario herede.
- **Contexto:** Python idiomatic favorece composición sobre herencia; los nodos LangGraph son simplemente callables `async (state) → dict`, no objetos.
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Decorator (elegida)** | Idiomatic Python; permite funciones simples; opt-in per-parámetro | Magia oculta si no se lee el wrapper |
| Base class `BaseNode` | OOP explícito; methods documentados | Fuerza estilo; menos flexible para funciones puras |
| Middleware list explícita | Máxima transparencia; sin magia | Verboso; mucho boilerplate en cada nodo |

- **Justificación:** El decorator es el patrón canónico en Python para cross-cutting (FastAPI, Click, Flask) y encaja con la convención callable-injection ya establecida en Fase B.

### DD-EXT-002: Plugin Discovery vía Entry Points (no scan)

- **Decisión:** Plugins se descubren vía `importlib.metadata.entry_points()`, no por escaneo de directorios o convenciones de nombre.
- **Contexto:** Entry points son el estándar de Python (PEP 621); funcionan con cualquier instalador (pip, uv, poetry).
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Entry points (elegida)** | Estándar; sin escaneo; declarativo | Plugin debe estar instalado (no funciona con código suelto) |
| Directorio `plugins/` escaneado | Funciona con código no instalado | No estándar; ambiguo con dev installs; security |
| Config YAML manual | Operador controla 100% | Burocrático; no aprovecha PyPI |

- **Justificación:** Entry points son la única opción que escala a un ecosistema PyPI; el costo (plugin debe instalarse) es aceptable y deseable (audit trail).

### DD-EXT-003: Builder devuelve `SubgraphDefinition`, no `CompiledStateGraph` por default

- **Decisión:** `builder.compile()` retorna `SubgraphDefinition` (registrable en `SubgraphRegistry`). Existe `compile_raw()` como escape hatch que retorna `CompiledStateGraph` directo.
- **Contexto:** El path 95% de uso es construir un subgraph para registrar; sólo casos avanzados quieren el `CompiledStateGraph` crudo (testing, embebido en otros sistemas).
- **Consecuencias:** Builder integra naturalmente con la convención `register_<name>(registry)`. El escape hatch evita lock-in.

### DD-EXT-004: Aislamiento de fallos de plugins

- **Decisión:** Cada plugin se carga en `try/except` independiente. Falla individual produce log + métrica pero no impide el resto.
- **Contexto:** Un ecosistema de plugins necesariamente tendrá calidad heterogénea; fallar el startup por un plugin roto es inaceptable.
- **Consecuencias:** `DiscoveryReport` agrega `loaded` y `failed`; CLI `prismal.plugins doctor` ayuda a diagnosticar.

### DD-EXT-005: Sin contenedor DI completo

- **Decisión:** Se mantiene el patrón actual `settings: Settings | None = None` y resolución vía `get_settings()`. No se introduce `dependency-injector` ni equivalente.
- **Contexto:** A la escala actual del repo, un contenedor DI añade complejidad sin beneficio claro. Los ports formalizados (X6) cubren el caso de "sustituir implementación".
- **Consecuencias:** Si en futuro el grafo de dependencias crece (>50 servicios), se reevalúa.

### DD-EXT-006: Allowlist/Denylist como settings (no en código)

- **Decisión:** Los toggles `plugins_autodiscover`, `plugins_allowlist`, `plugins_denylist` viven en `core/config.py` (Pydantic Settings) y son configurables vía env vars.
- **Contexto:** Deployment diferentes (dev, staging, prod, sandboxed) necesitan diferente postura; hardcoding viola configurabilidad.
- **Consecuencias:** Variables `PRISMAL_PLUGINS_AUTODISCOVER`, `PRISMAL_PLUGINS_ALLOWLIST`, etc.

### DD-EXT-007: `prismal.langgraph` re-exporta, no envuelve

- **Decisión:** El módulo `prismal.langgraph` re-exporta los símbolos tal cual de `langgraph.*`; no los envuelve ni los modifica.
- **Contexto:** Envolver crea drift; el usuario debe poder copiar código de docs de LangGraph sin traducir.
- **Consecuencias:** `from prismal.langgraph import StateGraph` es 100% equivalente a `from langgraph.graph import StateGraph`. La adición de prismal es `AgentState` (que el usuario quiere igual) y `VERSION` (trazabilidad).

### DD-EXT-008: Adapter LangChain como módulo opcional (sin extra)

- **Decisión:** `LangChainRunnableAdapter` vive en `agents/extension/adapters.py` sin requerir extra adicional — `langchain-core` ya es dep core de prismal.
- **Contexto:** Casi todos los usuarios de prismal vienen del ecosistema LangChain; pedirles un extra extra (`[langchain-bridge]`) es fricción innecesaria.
- **Consecuencias:** El adapter es lazy-import si `Runnable` no se usa; sin overhead para quien no lo necesita.

---

## 5. Estructura del Código

```
prismal/
│
├── langgraph.py                         ← re-exports + VERSION
│
├── agents/
│   └── extension/
│       ├── __init__.py
│       ├── decorators.py                ← @prismal_node, NodeMetadata
│       ├── builder.py                   ← PrismalStateGraphBuilder
│       ├── plugins.py                   ← discover_plugins, DiscoveryReport, PluginRegistry
│       ├── adapters.py                  ← LangChainRunnableAdapter
│       ├── ports.py                     ← CheckpointPort, AuditPort, EmbeddingsPort, ToolPort
│       ├── _middleware.py               ← (interno) security_mw, otel_mw, retry_mw, etc.
│       └── _registry.py                 ← (interno) _REGISTERED_NODES
│
├── plugins.py                           ← CLI entry: python -m prismal.plugins (list|info|doctor)
│
tests/
├── unit/
│   ├── test_langgraph_reexport.py
│   ├── agents/extension/
│   │   ├── test_decorators.py
│   │   ├── test_builder.py
│   │   ├── test_plugins.py
│   │   ├── test_adapters_langchain.py
│   │   └── test_ports.py
│   └── test_plugins_cli.py
└── integration/
    ├── test_custom_node_e2e.py
    ├── test_custom_subgraph_e2e.py
    ├── test_plugin_discovery_e2e.py     ← instala un plugin de prueba in-memory
    └── test_langchain_adapter_e2e.py    ← AgentExecutor real con LLM mockeado

examples/
├── custom_node.py
├── custom_subgraph.py
├── langchain_migration.py
└── plugin_template/
    ├── pyproject.toml
    ├── src/prismal_x_hello/
    │   ├── __init__.py
    │   └── plugin.py
    └── README.md

docs/
└── extension.md
```

### Patrones Aplicados

| Patrón | Dónde | Por qué |
|---|---|---|
| **Decorator** | `@prismal_node` | Cross-cutting sin herencia; idiomatic Python |
| **Builder fluent** | `PrismalStateGraphBuilder` | API declarativa sobre `StateGraph` |
| **Plugin / Registry** | `discover_plugins` + entry points | Ecosistema extensible sin tocar core |
| **Adapter** | `LangChainRunnableAdapter` | Bridge entre dos contratos (`Runnable` ↔ nodo async) |
| **Ports & Adapters (Hexagonal)** | `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` | Sustitución de implementaciones |
| **Middleware Chain** | `_middleware.py` | Composición ordenada de cross-cutting |
| **Strategy** | `security="standard"\|"strict"\|"off"` en decorator | Comportamiento configurable |
| **Composite** | Subgraph como nodo | LangGraph nativo; prismal documenta y da helper |
| **Template Method** | (implícito en `_middleware.py`) | Pipeline fijo con hook `execute` proveído por usuario |
| **Open/Closed** | Plugin system | Extensible sin modificar el core |

### Manejo de Errores

```python
class ExtensionError(PrismalError): ...
class NodeExecutionError(ExtensionError): ...
class NodeTimeoutError(NodeExecutionError): ...
class NodeValidationError(NodeExecutionError): ...
class PluginLoadError(ExtensionError):
    plugin_name: str
    entry_point: str
    cause: BaseException
class PluginConflictError(ExtensionError): ...     # nombre duplicado
class AdapterError(ExtensionError): ...
class LangChainAdapterError(AdapterError): ...
```

Política: el decorator captura excepciones del usuario y las **mapea** a `NodeExecutionError`, retornando un `state_update` con `metadata["error"]={...}` por default. El usuario opt-in puede pedir `@prismal_node(raise_on_error=True)` para propagar.

---

## 6. Seguridad

### 6.1 Superficie de Ataque

| Vector | Mitigación |
|---|---|
| Plugin malicioso registra nodos backdoor | Allowlist/denylist por settings; audit log de cada carga; entry points son confianza explícita del operador (firmados por PyPI publisher) |
| Nodo custom salta `SecurePromptBuilder` | `@prismal_node(security="standard")` lo aplica automáticamente; opt-out es explícito |
| Adapter LangChain ejecuta tools no autorizadas | `ActionInterceptor.check()` aplicado antes de `Runnable.ainvoke()` si `security="strict"` |
| Plugin carga código en import time | Try/except aislado; falla no afecta resto; warning si import time > 100 ms |
| Conflicto de nombres entre plugins | Registry detecta y lanza `PluginConflictError` con mensaje claro |
| Entry point apunta a callable inexistente | `ep.load()` falla, capturado, log estructurado |
| Audit log crece con cada plugin load | Una entry por load (no por nodo); rotación heredada del `AuditLogger` |

### 6.2 Reglas Transversales

1. **Plugins son confianza explícita** — instalar un plugin equivale a instalar código Python; operador es responsable.
2. **Allowlist es preferida a denylist en producción** — modo strict.
3. **`@prismal_node` por default `security="standard"`** — opt-out explícito requerido.
4. **Adapter LangChain aplica `SecurePromptBuilder`** al input antes del Runnable.
5. **Audit log de cargas** con versión y entry point — trazabilidad completa.

---

## 7. Observabilidad

### 7.1 OTel Spans

| Componente | Spans |
|---|---|
| Decorator | `prismal.ext.node.<name>` con attrs `node_name`, `session_id`, `capabilities`, `status` |
| Builder | `prismal.ext.builder.compile` con attrs `subgraph_name`, `node_count`, `edge_count` |
| Plugin discovery | `prismal.ext.discover` (overall), `prismal.ext.load_plugin.<name>` por plugin |
| Adapter | `prismal.ext.adapter.langchain.ainvoke` con attrs `runnable_type`, `input_chars` |

### 7.2 Métricas

```
# Plugins
prismal_plugins_discovered_total{group="subgraphs|nodes|tools|rag_engines"}
prismal_plugins_loaded_total{name, status="success|error", group}
prismal_plugin_load_duration_seconds{name}

# Custom nodes
prismal_custom_nodes_registered_total{node}
prismal_custom_nodes_invocations_total{node, status="success|error|timeout"}
prismal_custom_nodes_latency_seconds{node}

# Adapters
prismal_adapter_langchain_invocations_total{runnable_type}
prismal_adapter_langchain_latency_seconds{runnable_type}

# Builder
prismal_builder_compile_total{subgraph}
```

### 7.3 Startup Report

Al arrancar la app con plugins activos, el logger emite un resumen estructurado:

```
INFO prismal.extension.discovery
  loaded=15 failed=2 skipped_by_denylist=1 duration_ms=287
  groups={subgraphs: 5, nodes: 8, tools: 2, rag_engines: 0}
  failed_plugins=[
    {name: "broken_plugin", error: "ImportError: ..."},
    {name: "another", error: "SyntaxError: ..."}
  ]
```

---

## 8. Testing Strategy

| Nivel | Cobertura | Herramientas | Qué cubre |
|---|---|---|---|
| Unit | ≥ 85% por módulo | pytest + `AsyncMock` | Decorator, builder, plugin loader, adapter, ports |
| Integration | Flujos críticos | pytest + plugin de prueba in-memory (via `pkg_resources` mock) | Discovery end-to-end; nodo custom dentro de grafo |
| Live API | `@pytest.mark.live_api` | Skip por default | Adapter con LangChain `AgentExecutor` real |
| Bench | `@pytest.mark.bench` | pytest-benchmark | Overhead del decorator ≤ 5 ms p95 |
| Plugin lifecycle | `tests/integration/test_plugin_discovery_e2e.py` | crea wheel temporal con `build` + instala en venv aislado | Verifica que un plugin "real" se descubre |

### Estrategia de Mock

- **Entry points:** `monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: [...])` para inyectar plugins de prueba sin tocar el filesystem.
- **LangChain Runnable:** se construye con `RunnableLambda(lambda x: AIMessage(content="ok"))` para tests rápidos.
- **OTel:** `OTelManager` mockeado para verificar que se abrió/cerró el span correcto.
- **Audit:** `AuditLogger.log_event` mockeado para verificar que se loguearon cargas.

---

## 9. Plan de Rollout

### 9.1 Estrategia de Adopción

Fase X es **aditiva y opt-in**:

1. `prismal.langgraph` se publica como módulo nuevo — no afecta a nadie.
2. `@prismal_node`, builder, adapters son APIs nuevas — no afectan nodos existentes.
3. `discover_plugins()` se invoca explícitamente desde el startup del operador; `settings.plugins_autodiscover=True` por default pero sin plugins instalados es no-op.
4. Los nodos existentes (26 textuales) **no se decoran retroactivamente** en Fase X. Migración es un seguimiento posterior (Fase X.1) si se quiere consolidar el comportamiento.

### 9.2 Backward Compatibility

- Cero cambios en la API pública existente.
- `SubgraphRegistry` y `register_<name>()` siguen funcionando idénticamente.
- Tests existentes (~688) pasan sin modificación.
- Nuevas excepciones heredan de `PrismalError` (jerarquía conocida).

### 9.3 Estabilidad de API

La superficie de extensión es API pública con compromiso de SemVer:

- **Breaking changes** requieren bump de **minor** (en pre-1.0) o **major** (post-1.0).
- **Deprecations** vía `warnings.warn(DeprecationWarning, stacklevel=2)` con 1 minor release de aviso mínimo.
- Decorador interno `@frozen_api` marca las funciones del contrato público; pre-commit hook valida que no cambien sin justificación documentada.

---

## 10. Preguntas Abiertas

- [ ] **`@prismal_node` defaults:** ¿`security="standard"` por default o `security="off"` para evitar overhead silencioso? — Owner: Tech Lead, Deadline: inicio X2.
- [ ] **Entry point para `rag_engines`:** ¿realmente útil o es feature creep? Las 7 engines actuales son in-tree. — Owner: AI Architect, Deadline: inicio X4.
- [ ] **Plugin sandboxing:** ¿permitimos ejecutar plugin en subprocess aislado (futuro `prismal.plugins exec --sandbox`) o confiamos en la jerarquía actual de seguridad? — Owner: Tech Lead, Deadline: Fase Y.
- [ ] **Compatibilidad con `langgraph-checkpoint-redis`** y otros checkpointers externos — ¿declarar `CheckpointPort` con el contrato exacto que ellos usan? — Owner: AI Architect.
- [ ] **CLI `python -m prismal.plugins`:** ¿incluir en Fase X o diferir a un follow-up? Costo bajo pero scope creep. — Owner: DX Lead, Deadline: inicio X4.
- [ ] **Plugin templates:** ¿`cookiecutter` o `copier`? Copier tiene mejor UX moderna pero menor adopción. — Owner: DX Lead.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — superficie de extensión LangGraph + plugin SDK |
