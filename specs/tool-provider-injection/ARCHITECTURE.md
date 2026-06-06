# Prismal Tool Provider Injection — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN Relacionado** | `specs/tool-provider-injection/PLAN.md` |
| **SPEC Relacionado** | `specs/tool-provider-injection/SPEC.md` |
| **TASKS** | `specs/tool-provider-injection/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |

---

## 1. Contexto

Prismal resuelve las herramientas de cada agente en `prismal/agents/tool_registry.py`, un *facade* que fusiona tres fuentes: servidores **MCP** (vía un *singleton* de módulo `_mcp_manager: MCPClientManager`), **Skills** activas (`SkillsManager().get_active_tools()`) y *stubs* estáticos de `tools.py`. La fusión aplica prioridad (MCP → Skills → stubs), dedupe por nombre, un cap por servidor (`_MAX_MCP_TOOLS = 60`) y un cap total (`_MAX_TOTAL_TOOLS = 120`, límite de OpenAI), y exime a `_FIXED_TOOL_AGENTS = {cron_manager, critic}`.

El acoplamiento problemático: **el núcleo de agentes importa y construye sus subsistemas de integración**. Esto contradice la Fase X (Extension Surface), donde `CheckpointPort`/`AuditPort`/`EmbeddingsPort` ya están invertidos como puertos hexagonales. Este documento describe la **Fase Y — Tool Provider Injection**, que introduce un `ToolProviderPort` y traslada la construcción de MCP/Skills al host (`prismal-sdk`, `prismal-web`), dejando el núcleo como consumidor puro.

---

## 2. Objetivos Técnicos

- **OT-1:** Invertir la dependencia: `prismal/agents/**` deja de importar `prismal.mcp` y `prismal.skills`.
- **OT-2:** Modelar las fuentes de herramientas como `ToolProviderPort` (estructural, sin clase base).
- **OT-3:** Conservar `get_tools_for_agent(name)` como API estable para los 20+ nodos (cero cambios en agentes).
- **OT-4:** Mantener paridad exacta del merge (prioridad, dedupe, caps, fixed-tool agents).
- **OT-5:** Habilitar inyección por sesión (multi-tenant) sin estado global (variante B).
- **OT-6:** Preservar las capas de seguridad L1–L5 sobre cualquier tool inyectada.
- **OT-7:** Degradar a stubs + warning cuando no hay proveedor (no estricto) o fallar limpio (estricto).

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel — Inversión de Dependencia

```
ANTES (Fase actual):

  prismal/agents/graph.py (nodos)
        │  get_tools_for_agent("coder")
        ▼
  prismal/agents/tool_registry.py  ──imports──▶ prismal/mcp/client.py (MCPClientManager)
        (facade + singleton _mcp_manager)  └──▶ prismal/skills/manager.py (SkillsManager)
                                            └──▶ prismal/agents/tools.py (stubs)

  ⮕ El núcleo (agents) DEPENDE de las capas de integración (mcp, skills).


DESPUÉS (Fase Y):

  HOST (prismal-sdk / prismal-web)
        │  construye proveedores e inyecta
        │  set_tool_provider(CompositeToolProvider([...]))
        ▼
  prismal/agents/extension/providers.py
        ├─ McpToolProvider  ──▶ prismal/mcp/client.py (MCPClientManager)
        ├─ SkillToolProvider ─▶ prismal/skills/manager.py (SkillsManager)
        └─ StubToolProvider ──▶ prismal/agents/tools.py
        ▲
        │ conforma ToolProviderPort
  prismal/agents/extension/ports.py  (ToolProviderPort : Protocol)
        ▲
        │ delega
  prismal/agents/tool_registry.py  (get_tools_for_agent → provider.get_tools)
        ▲
        │ get_tools_for_agent("coder")  (sin cambios)
  prismal/agents/graph.py (nodos)

  ⮕ El núcleo (agents) NO conoce mcp ni skills. El host compone e inyecta.
```

### 3.2 Diagrama de Capas

```
┌──────────────────────────────────────────────────────────────┐
│  HOST: prismal-sdk / prismal-web                              │
│  - build_default_tool_provider(settings)                     │
│  - construye MCPClientManager, SkillsManager                 │
│  - set_tool_provider(...) (variante A)                        │
│  - get_async_compiled_graph(tool_provider=...) (variante B)  │
└───────────────┬──────────────────────────────────────────────┘
                │ inyecta
┌───────────────▼──────────────────────────────────────────────┐
│  NÚCLEO PUBLICABLE: prismal/agents                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ extension/ports.py     → ToolProviderPort (contrato)   │  │
│  │ extension/providers.py → Mcp/Skill/Stub/Composite      │  │
│  │ tool_registry.py       → get_tools_for_agent (delega)  │  │
│  │ react_loop + @prismal_node → L1–L5 sobre cada tool     │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────────────────────────────┘
                │ usa (vía wrappers, imports diferidos)
┌───────────────▼──────────────────────────────────────────────┐
│  INTEGRACIONES: prismal/mcp, prismal/skills, agents/tools.py  │
│  (MCPClientManager, SkillsManager — sin cambios de API)       │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Componentes por Módulo

#### Y1 — `ToolProviderPort` (`prismal/agents/extension/ports.py`)
- `@runtime_checkable Protocol` con un único método `get_tools(*, agent_name: str, capabilities: list[str] | None) -> list[ToolPort]`.
- Reutiliza `ToolPort` (Fase X) como tipo de retorno.
- Se añade junto a los puertos existentes; re-export desde `extension/__init__.py`.
- Helper `conforms_to(obj, ToolProviderPort)` ya disponible.

#### Y2 — Proveedores concretos (`prismal/agents/extension/providers.py`)
- **`McpToolProvider(manager: MCPClientManager, *, max_tools: int = 60)`** — `get_tools()` ignora `agent_name`, aplica `manager.get_all_langchain_tools(capabilities=...)[:max_tools]`. Import de `MCPClientManager` diferido dentro del módulo de proveedores (que vive fuera del path prohibido `agents/` desde el punto de vista del test de arquitectura — ver DD-TPI-003).
- **`SkillToolProvider(manager: SkillsManager)`** — `get_tools()` devuelve `manager.get_active_tools()`; `capabilities` se ignora (paridad con hoy: skills no se filtran).
- **`StubToolProvider()`** — encapsula el `stub_map` actual (researcher→RESEARCHER_TOOLS, coder→CODER_TOOLS+SANDBOX_TOOLS, …) y la exención `_FIXED_TOOL_AGENTS`.
- **`CompositeToolProvider(providers: list[ToolProviderPort], *, max_total: int = 120, fixed_tool_agents=frozenset({"cron_manager","critic"}))`** — implementa la estrategia de merge actual: si `agent_name ∈ fixed_tool_agents`, devuelve solo stubs; en otro caso concatena proveedores en orden (MCP→Skills→stubs), filtra stubs cuyo nombre ya exista en live, y trunca a `max_total`. Emite el log `tool_provider.tools_resolved` (paridad con `tool_registry.tools_resolved`).

#### Y3 — Inyección global (`prismal/agents/tool_registry.py`, variante A)
- Nuevo estado de módulo: `_provider: ToolProviderPort | None = None`.
- `set_tool_provider(p)` / `get_tool_provider()`.
- `get_tools_for_agent(agent_name, required_capabilities=None)` → si `_provider` es `None`: usa `StubToolProvider` + warning `tool_registry.no_provider` (o `raise ToolProviderNotConfigured` si `settings.tool_provider_strict`). Si existe: `_provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)`.
- Se eliminan los imports de `prismal.mcp` y `prismal.skills` del módulo.
- `init_mcp()`, `get_mcp_tools()`, `get_skill_tools()` se conservan como *shims* que emiten `DeprecationWarning` y delegan en proveedores/host.

#### Y4 — Inyección por contexto (variante B, multi-tenant)
- `get_async_compiled_graph(..., tool_provider: ToolProviderPort | None = None)` guarda el proveedor en la config compilada.
- Resolución por nodo: un helper `resolve_provider(config)` lee el proveedor de `RunnableConfig["configurable"]["tool_provider"]` o cae al global. Sin lock global por resolución.
- Activado por `settings.tool_provider_mode = "context"`.

#### Y5 — Composición del host (`prismal/agents/extension/providers.py::build_default_tool_provider`)
- `build_default_tool_provider(settings) -> CompositeToolProvider` arma el composite estándar (MCP si configurado + Skills + Stubs), respetando `settings`. Es el helper que `prismal-sdk`/`prismal-web` llaman en su *lifespan*. Vive en el namespace de extensión (host-facing), no en el path de núcleo puro.

#### Y6 — Settings y observabilidad
- `settings.tool_provider_mode: Literal["global","context"] = "global"`.
- `settings.tool_provider_strict: bool = False`.
- Métricas: `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total`.
- Span `prismal.tools.resolve{agent}`.

### 3.4 Flujos de Datos Detallados

#### Flujo Y-A: Arranque y composición en el host (variante A)
```
1. prismal-web lifespan startup
2. build_default_tool_provider(settings)
     ├─ MCPClientManager(config).load_from_config()  (await)
     ├─ SkillsManager()
     └─ CompositeToolProvider([Mcp, Skill, Stub])
3. set_tool_provider(provider)          # estado de módulo en tool_registry
4. get_async_compiled_graph()           # núcleo, sin conocer mcp/skills
5. primer turno → nodo coder → get_tools_for_agent("coder")
     └─ _provider.get_tools(agent_name="coder", capabilities=None)
```

#### Flujo Y-B: Resolución por sesión (variante B)
```
1. request de usuario U
2. provider_U = CompositeToolProvider([Mcp(allowlist_U), Skill(plan_U), Stub])
3. graph = await get_async_compiled_graph(tool_provider=provider_U)
4. nodo coder → resolve_provider(config) → provider_U  (no global)
5. provider_U.get_tools(agent_name="coder", ...)
   ⮕ usuario V con provider_V en paralelo no comparte estado
```

#### Flujo Y-C: Fallback sin proveedor
```
1. núcleo importado como librería, sin host
2. nodo → get_tools_for_agent("researcher")
3. _provider is None
     ├─ strict=False → StubToolProvider().get_tools(...) + warning
     └─ strict=True  → raise ToolProviderNotConfigured
```

---

## 4. Decisiones de Diseño

### DD-TPI-001: Puerto estructural (Protocol), no clase base
Coherente con `ports.py` de Fase X. Un host puede inyectar cualquier objeto con `get_tools(...)` sin heredar de nada ni registrarse. `MCPClientManager`/`SkillsManager` no se modifican: se envuelven en adaptadores delgados.

### DD-TPI-002: Conservar `get_tools_for_agent` como fachada estable
Los 20+ nodos llaman `get_tools_for_agent(name)`. Mantener esa firma hace el refactor invisible para los agentes y reduce el blast radius a un solo archivo (variante A). Alternativa descartada: cambiar la firma de cada nodo (variante B pura) — más limpio pero invasivo; se ofrece como fase 2 opt-in.

### DD-TPI-003: Proveedores viven en `extension/`, no en el núcleo puro
`providers.py` importa `MCPClientManager`/`SkillsManager` (diferido). Para que el test de arquitectura "agents no importa mcp/skills" sea cierto y útil, se excluye `prismal/agents/extension/providers.py` de la regla (es código *host-facing*, equivalente a `plugins.py` de Fase X que también orquesta integraciones). El **núcleo puro** (graph, supervisor, nodos, tool_registry) queda limpio.

### DD-TPI-004: Caps de tokens son política de plataforma
`_MAX_MCP_TOOLS` y `_MAX_TOTAL_TOOLS` permanecen en el `CompositeToolProvider` oficial (no como parámetro libre del host) para que un consumidor no rompa el límite de 128 de OpenAI por accidente. Configurables pero con defaults seguros.

### DD-TPI-005: Fallback a stubs por defecto, estricto opt-in
Para no romper a consumidores que usan el núcleo como librería sin MCP/skills, la ausencia de proveedor degrada a stubs + warning (paridad con `get_mcp_tools()` devolviendo `[]`). `tool_provider_strict=True` lo convierte en error para despliegues que exijan toolset real.

### DD-TPI-006: Shims deprecados, no remoción inmediata
`init_mcp`/`get_mcp_tools`/`get_skill_tools` siguen existiendo (con `DeprecationWarning`) delegando en proveedores, durante 1 minor. Evita romper a `prismal-sdk`/`prismal-web` y a ejemplos antes de que migren.

### DD-TPI-007: Seguridad no se mueve
Las capas L1–L5 viven en `react_loop` + el *middleware* de `@prismal_node`, downstream del proveedor. El proveedor solo **provee** tools; la **ejecución** sigue pasando por las barreras. Un proveedor malicioso no puede saltarse `ActionInterceptor`/`AuditLogger`.

### DD-TPI-008: `required_capabilities` se preserva end-to-end
La firma `get_tools(*, agent_name, capabilities)` mantiene el filtro Fase E. `CompositeToolProvider` solo aplica `capabilities` al sub-proveedor MCP (paridad: skills y stubs no se filtran).

---

## 5. Estructura del Código

```
prismal/
├── agents/
│   ├── tool_registry.py            # MODIFICADO: delega en proveedor; sin imports mcp/skills
│   ├── extension/
│   │   ├── ports.py                # MODIFICADO: + ToolProviderPort
│   │   ├── providers.py            # NUEVO: Mcp/Skill/Stub/Composite + build_default_tool_provider
│   │   └── __init__.py             # MODIFICADO: re-export ToolProviderPort + providers
│   ├── graph.py                    # MODIFICADO (variante B): tool_provider en config
│   └── tools.py                    # SIN CAMBIOS (stubs reubicados lógicamente en StubToolProvider)
├── core/
│   ├── config.py                   # MODIFICADO: tool_provider_mode, tool_provider_strict
│   └── exceptions.py               # MODIFICADO: + ToolProviderNotConfigured
docs/
└── tool-providers.md               # NUEVO
examples/
├── tool_provider_custom.py         # NUEVO
└── tool_provider_host.py           # NUEVO
tests/
└── unit/extension/
    ├── test_tool_provider_port.py  # NUEVO
    ├── test_providers.py           # NUEVO
    ├── test_registry_delegation.py # NUEVO (paridad)
    └── test_no_mcp_skills_imports.py # NUEVO (arquitectura)
```

### Patrones Aplicados
- **Hexagonal Ports & Adapters** (igual que Fase X).
- **Factory injection** (igual que Fase A/B/C: el negocio acepta el proveedor, defaults perezosos).
- **Facade estable** (`get_tools_for_agent`) sobre implementación intercambiable.
- **Strategy** (`CompositeToolProvider` encapsula la política de merge).

### Manejo de Errores
- Sub-proveedor que lanza → se captura, se loguea (`tool_provider.subprovider_error`), se devuelve el resto (paridad con `get_mcp_tools()` actual).
- Sin proveedor → fallback o `ToolProviderNotConfigured` según `strict`.
- Errores de ejecución de tools siguen gestionados por `react_loop` (failure budget, rate-limit backoff) sin cambios.

---

## 6. Seguridad

### 6.1 Superficie de Ataque
- **Proveedor inyectado malicioso:** podría devolver tools arbitrarias. Mitigación: la ejecución pasa por `ActionInterceptor.check()` y `AuditLogger`; el proveedor no ejecuta, solo lista. El host es responsable de qué proveedores compone (confianza explícita, igual que entry points de Fase X).
- **Multi-tenant (variante B):** fuga de tools entre sesiones. Mitigación: sin estado global; proveedor por sesión; test de aislamiento.

### 6.2 Reglas Transversales
- Toda tool, venga de donde venga, se ejecuta dentro de `react_loop` con las barreras L1–L5.
- Caps de tokens en el composite oficial.
- Audit log registra el proveedor resuelto por nodo (no el contenido de las tools).
- Imports de SDKs externos siguen diferidos y aislados (no a nivel de módulo del núcleo).

---

## 7. Observabilidad

### 7.1 OTel Spans
- `prismal.tools.resolve{agent}` — atributos `provider`, `n_tools`, `fallback`, `capabilities`.

### 7.2 Métricas
```
# Resolución
prismal_tool_provider_resolved_total{provider="composite|mcp|skill|stub|fake"}
prismal_tools_injected_total{agent}
prismal_tool_provider_fallback_total          # nº de veces que cayó a stubs por falta de proveedor
prismal_tool_provider_subprovider_errors_total{provider}
```

### 7.3 Startup Report (host)
- `build_default_tool_provider` loguea qué sub-proveedores quedaron activos (MCP servers conectados, nº skills activas) — paridad con el log `tool_registry.mcp_initialized` actual, pero emitido desde el host.

---

## 8. Testing Strategy

- **Unit:** cada proveedor en aislamiento con managers *mock*.
- **Paridad:** `test_registry_delegation.py` compara la salida de `get_tools_for_agent` con el composite por defecto contra una *golden list* derivada de la implementación actual (mismo orden, dedupe, caps, fixed agents).
- **Arquitectura:** `test_no_mcp_skills_imports.py` recorre el AST de `prismal/agents/**` (excluyendo `extension/providers.py`) y falla si aparece `import prismal.mcp` o `import prismal.skills`.
- **Aislamiento (variante B):** dos proveedores en paralelo no comparten tools.
- **Fallback:** sin proveedor → stubs + warning; `strict=True` → excepción.

### Estrategia de Mock
- `FakeToolProvider(mapping: dict[str, list[BaseTool]])` para fixtures de agentes — reemplaza MCP/skills reales.

---

## 9. Plan de Rollout

### 9.1 Estrategia de Adopción
1. Merge de Y1–Y3 (variante A) — comportamiento idéntico si el host llama `build_default_tool_provider` + `set_tool_provider` en el lifespan.
2. `prismal-sdk` / `prismal-web` migran su arranque de `init_mcp()` a `set_tool_provider(build_default_tool_provider(settings))`.
3. Y4 (variante B) se adopta solo donde se necesite multi-tenant.

### 9.2 Backward Compatibility
- Nodos sin cambios.
- Shims deprecados mantienen el arranque viejo funcionando 1 minor.
- Si el host no migra, el fallback a stubs evita crashes (modo degradado visible por warning).

### 9.3 Estabilidad de API
- `ToolProviderPort` y los proveedores son API pública versionada (SemVer; breaking → minor + deprecation 1 release).

---

## 10. Preguntas Abiertas

- **PA-1:** ¿`build_default_tool_provider` debe vivir en `prismal/agents/extension/providers.py` o trasladarse físicamente a `prismal-sdk`? (Propuesta: empezar en `extension/` para no bloquear; mover a `prismal-sdk` cuando exista como paquete.)
- **PA-2:** ¿La variante B debe ser el default a medio plazo y deprecar el global? (Depende de la prioridad de multi-tenant en `prismal-web`.)
- **PA-3:** ¿Conviene exponer `capabilities` también a `SkillToolProvider` (filtrar skills por capacidad) o mantener paridad estricta con hoy? (Propuesta: paridad ahora, evaluar después.)

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Diseño técnico inicial — `ToolProviderPort` + inyección desde host |
