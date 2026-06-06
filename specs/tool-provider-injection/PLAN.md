# Prismal — Tool Provider Injection (MCP & Skills como puerto inyectable)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Fase** | Y — Tool Provider Injection (sucesora natural de Fase X — Extension Surface) |

---

## 1. Resumen Ejecutivo

Hoy las herramientas que consumen los agentes provienen de tres fuentes (servidores **MCP**, **Skills** activas y *stubs* estáticos) y se fusionan en un único *facade*: `prismal/agents/tool_registry.py`. El problema es la **dirección de la dependencia**: la capa de agentes (el núcleo de orquestación) construye y conoce directamente a sus subsistemas de integración — importa `prismal.mcp.client.MCPClientManager` y `prismal.skills.manager.SkillsManager`, mantiene el ciclo de vida en *singletons* de módulo (`_mcp_manager`, `_mcp_initialized`, `_mcp_lock`) y decide la configuración (`init_mcp(config_path)`).

Esto es incoherente con el diseño establecido en la **Fase X (Extension Surface)**, donde `CheckpointPort`, `AuditPort` y `EmbeddingsPort` ya están invertidos como puertos hexagonales: el host compone e inyecta, el núcleo solo consume. **MCP y Skills son la excepción que falta normalizar.**

Esta fase define un **`ToolProviderPort`** y mueve la *construcción* de MCP/Skills fuera del núcleo, hacia el componente que compone la aplicación (`prismal-sdk`, `prismal-web`). El núcleo deja de importar `prismal.mcp` y `prismal.skills`; el host instancia los proveedores y los inyecta vía `set_tool_provider()`. El cambio es **opt-in, aditivo y retrocompatible**: los 20+ nodos-agente siguen llamando `get_tools_for_agent("coder")` sin cambios, y si nadie inyecta un proveedor el sistema degrada a *stubs* con un *warning* estructurado (igual que hoy `get_mcp_tools()` devuelve `[]` cuando MCP no se inicializó).

El entregable habilita tres capacidades que hoy son imposibles sin parchear el núcleo: (1) **toolsets por sesión/usuario** para `prismal-web` multi-tenant; (2) **núcleo `prismal` publicable y testeable** sin servidores MCP ni skills en disco; (3) **ciclo de vida de integraciones en el host**, donde corresponde.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- **`tool_registry.py` es el único punto de integración.** Mezcla MCP + Skills + stubs por llamada (`get_tools_for_agent`), aplicando prioridad (MCP → Skills → stubs), dedupe por nombre y caps de tokens (`_MAX_MCP_TOOLS = 60`, `_MAX_TOTAL_TOOLS = 120`).
- **El núcleo baja a sus periféricos.** `get_mcp_tools()` importa `prismal.mcp.client.MCPClientManager`; `get_skill_tools()` instancia `prismal.skills.manager.SkillsManager()`. La capa de agentes depende de las capas de integración.
- **Estado global mutable de módulo.** El *singleton* `_mcp_manager` y sus flags/lock viven dentro del núcleo. El llamador no puede sustituir el *manager* (uno con auth de usuario, uno *mock* en tests) sin parchear el módulo.
- **Construcción no inyectable.** `init_mcp()` instancia `MCPClientManager(config_path or _DEFAULT_MCP_CONFIG)` internamente; la elección de config y el momento de conexión los decide el núcleo.
- **Activación de skills opaca.** `SkillsManager().get_active_tools()` lee estado de disco/proceso; el host no puede decidir qué skills ve cada usuario/sesión.
- **El límite ya existe a medias.** `grep` confirma que **ningún módulo dentro de `prismal/` llama a `init_mcp()`**: ya se asume que el arranque lo dispara un componente externo. La frontera está implícita pero no formalizada.

### 2.2 Problema

Sin un puerto de proveedor de herramientas:

1. **El núcleo no es publicable de forma limpia**: arrastra dependencias e imports de MCP y Skills que un consumidor del framework podría no querer.
2. **No hay multi-tenant real**: el *singleton* global impide que dos usuarios de `prismal-web` vean *toolsets* distintos (allowlist de servidores, skills por plan).
3. **Tests acoplados**: probar un agente requiere o bien MCP/skills reales, o parchear globals de módulo.
4. **El host no controla el ciclo de vida**: cuándo conectar, con qué credenciales, qué servidores — todo está enterrado en el núcleo.

### 2.3 Oportunidad

Las primitivas ya existen casi todas:

- **`ToolPort`** (`prismal/agents/extension/ports.py`, Fase X) ya modela una herramienta ejecutable (`name`, `description`, `ainvoke`) — la unidad que MCP y Skills producen.
- El patrón "**puerto + implementación externa que conforma la forma**" ya está probado con `CheckpointPort`/`AuditPort`/`EmbeddingsPort`.
- **`discover_plugins()`** (Fase X) ya establece el precedente "el host descubre/registra, el núcleo consume".
- **`DEFAULT_CAPABILITY_MAP`** y el parámetro `required_capabilities` (Fase E) ya enrutan capacidades por agente; solo hay que hacerlos fluir hasta el proveedor.

Lo que falta es **declarar el contrato del proveedor y mover la construcción al host**. Esfuerzo bajo (un puerto nuevo + refactor de un único archivo + extracción de proveedores), impacto alto en publicabilidad, testabilidad y multi-tenant, sin romper nada.

---

## 3. Usuarios Objetivo

### Persona 1: Platform Host (prismal-sdk / prismal-web)
- **Descripción:** El componente que compone y arranca la aplicación sobre el núcleo `prismal`.
- **Necesidad principal:** Construir e inyectar los proveedores de herramientas (MCP, Skills) con el ciclo de vida, credenciales y configuración que el host decida.
- **Frecuencia de uso:** Una vez por arranque (variante global) o una vez por sesión/usuario (variante por contexto).

### Persona 2: Multi-Tenant Web Operator
- **Descripción:** Opera `prismal-web` con múltiples usuarios/organizaciones.
- **Necesidad principal:** Que cada sesión vea un *toolset* propio (servidores MCP autorizados, skills habilitadas por plan) sin estado global compartido.
- **Frecuencia de uso:** Por request/sesión.

### Persona 3: Framework Consumer / Library User
- **Descripción:** Importa `prismal` como librería para construir su propio agente, sin necesitar MCP ni Skills.
- **Necesidad principal:** Que el núcleo funcione (degradado a stubs) sin obligar a instalar/configurar MCP ni Skills.
- **Frecuencia de uso:** Continua.

### Persona 4: Core Maintainer / Test Author
- **Descripción:** Escribe tests del núcleo de agentes.
- **Necesidad principal:** Inyectar un `FakeToolProvider` determinista sin parchear *singletons* de módulo ni levantar servicios.
- **Frecuencia de uso:** Diaria.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos del Negocio

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Inversión de dependencia | Imports de `prismal.mcp` / `prismal.skills` dentro de `prismal/agents/**` | 0 | Fase Y |
| Núcleo publicable aislado | `import prismal.agents.graph` sin `prismal.mcp`/`prismal.skills` instalados | OK (degrada a stubs) | Fase Y |
| Multi-tenant | Proveedor distinto por sesión sin estado global | Soportado (variante B) | Fase Y fase 2 |
| Backward compatibility | Tests existentes pasando sin cambios | 100% | Global |
| Testabilidad | Tests de agentes que requieren MCP/skills reales | 0 (vía `FakeToolProvider`) | Fase Y |
| Cobertura de tests | Branch coverage módulos nuevos | ≥ 85% | Global |

### 4.2 Objetivos de Usuario

| Objetivo del Usuario | Indicador |
|---|---|
| Inyectar proveedores desde el host en 1 llamada | `set_tool_provider(CompositeToolProvider([...]))` |
| Toolset por sesión/usuario | Proveedor resoluble desde config del grafo (variante B) |
| Núcleo sin MCP/Skills funciona | Fallback a `StubToolProvider` + warning, sin excepción |
| Sustituir el merge por uno propio | Implementar `ToolProviderPort` y conformar la forma |
| Tests deterministas | `FakeToolProvider` reemplaza el cableado real |

---

## 5. Alcance

### 5.1 In Scope (Incluido — Fase Y)

**Y1 — `ToolProviderPort` (`prismal/agents/extension/ports.py`):**
- [x] `Protocol` `ToolProviderPort` con `get_tools(*, agent_name, capabilities) -> list[ToolPort]`.
- [x] Re-export desde `prismal/agents/extension/__init__.py`.
- [x] Helper de conformidad reutilizando `conforms_to(obj, port)`.

**Y2 — Proveedores concretos (`prismal/agents/extension/providers.py`):**
- [x] `McpToolProvider` — envuelve `MCPClientManager`; mueve la lógica de `get_mcp_tools()` + cap `_MAX_MCP_TOOLS`.
- [x] `SkillToolProvider` — envuelve `SkillsManager.get_active_tools()`.
- [x] `StubToolProvider` — *fallbacks* de `tools.py` por agente (default del núcleo).
- [x] `CompositeToolProvider` — fusiona N proveedores con prioridad + dedupe + cap `_MAX_TOTAL_TOOLS` + respeto de `_FIXED_TOOL_AGENTS`.

**Y3 — Inyección global (variante A — retrocompatible):**
- [x] `tool_registry.set_tool_provider(p: ToolProviderPort)` y `get_tool_provider()`.
- [x] `get_tools_for_agent()` delega en el proveedor inyectado; si no hay, usa `StubToolProvider` + warning.
- [x] `tool_registry` deja de importar `prismal.mcp` y `prismal.skills`.
- [x] `init_mcp()` / `get_mcp_tools()` / `get_skill_tools()` se conservan como *shims* deprecados que delegan en proveedores (1 release de deprecación).

**Y4 — Inyección por contexto (variante B — multi-tenant, fase 2):**
- [x] El proveedor puede pasarse en la config del grafo (`get_async_compiled_graph(..., tool_provider=...)`) y resolverse por sesión.
- [x] Resolución por nodo desde `RunnableConfig` sin global (`resolve_provider` + `get_tools_for_agent_ctx`).

**Y5 — Composición en el host:**
- [x] Función `build_default_tool_provider(settings)` que arma el `CompositeToolProvider` estándar (MCP + Skills + stubs).
- [x] Documentado para que `prismal-sdk` / `prismal-web` lo invoquen en su *lifespan* (`docs/tool-providers.md` §1).

**Y6 — Settings y observabilidad:**
- [x] `settings.tool_provider_mode: Literal["global","context"] = "global"`.
- [x] `settings.tool_provider_strict: bool = False` (si `True`, ausencia de proveedor es error en vez de fallback silencioso).
- [x] Métricas: `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total` (+ `subprovider_errors_total`).
- [x] OTel spans: `prismal.tools.resolve{agent}`.

**Y7 — Documentación y ejemplos:**
- [x] `docs/tool-providers.md` — quickstart de composición + recetas (allowlist por usuario, mock en tests).
- [x] `examples/tool_provider_custom.py` — proveedor propio.
- [x] `examples/tool_provider_host.py` — composición tipo host (MCP + Skills + stubs) + inyección.

**Y8 — Tests:**
- [x] `FakeToolProvider` para fixtures.
- [x] Tests de paridad: la salida de `get_tools_for_agent` antes/después del refactor es idéntica con el proveedor por defecto.

### 5.2 Out of Scope (Excluido)

- **Reescribir `MCPClientManager` o `SkillsManager`** — solo se envuelven; su API interna no cambia.
- **Contenedor DI completo** — el patrón "inyectar proveedor + `settings: Settings | None = None`" basta (coherente con DD-EXT-005).
- **Cambiar la firma de los 20+ nodos-agente en la variante A** — los nodos siguen llamando `get_tools_for_agent(name)`.
- **Persistencia/caché distribuida de toolsets por usuario** — responsabilidad del host.
- **Hot reload de proveedores** — reinicio del proceso (o nueva sesión en variante B) es aceptable.
- **Mover `_MAX_MCP_TOOLS`/`_MAX_TOTAL_TOOLS` fuera de la política de plataforma** — los caps permanecen en el `CompositeToolProvider` oficial para que un host no los rompa por accidente.

### 5.3 Futuras Consideraciones (Fase Y+)

- Proveedores con caché TTL por sesión.
- Telemetría agregada por servidor MCP / skill (latencia, errores, uso por usuario).
- Cuotas por tenant (nº de tools, nº de llamadas).
- Negociación dinámica de capacidades (el agente declara, el proveedor resuelve el mínimo set).
- Proveedor remoto (gRPC/HTTP) para toolsets servidos por un servicio externo.

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-TPI-001 | `ToolProviderPort` declara `get_tools(*, agent_name, capabilities)` como `Protocol` | `MUST` |
| RF-TPI-002 | `McpToolProvider` envuelve `MCPClientManager` y aplica el cap `_MAX_MCP_TOOLS` | `MUST` |
| RF-TPI-003 | `SkillToolProvider` envuelve `SkillsManager.get_active_tools()` | `MUST` |
| RF-TPI-004 | `StubToolProvider` provee los fallbacks de `tools.py` por agente | `MUST` |
| RF-TPI-005 | `CompositeToolProvider` fusiona con prioridad MCP→Skills→stubs, dedupe y cap total | `MUST` |
| RF-TPI-006 | `set_tool_provider()` / `get_tool_provider()` inyectan/leen el proveedor (variante A) | `MUST` |
| RF-TPI-007 | `get_tools_for_agent()` delega en el proveedor; fallback a stubs si no hay proveedor | `MUST` |
| RF-TPI-008 | `prismal/agents/**` no importa `prismal.mcp` ni `prismal.skills` | `MUST` |
| RF-TPI-009 | `_FIXED_TOOL_AGENTS` (cron_manager, critic) siguen recibiendo solo stubs | `MUST` |
| RF-TPI-010 | `required_capabilities` fluye hasta `provider.get_tools(capabilities=...)` | `MUST` |
| RF-TPI-011 | Shims deprecados `init_mcp/get_mcp_tools/get_skill_tools` delegan en proveedores | `SHOULD` |
| RF-TPI-012 | Variante B: proveedor resoluble por sesión vía config del grafo | `SHOULD` |
| RF-TPI-013 | `build_default_tool_provider(settings)` arma el composite estándar para el host | `MUST` |
| RF-TPI-014 | Settings `tool_provider_mode` / `tool_provider_strict` | `SHOULD` |
| RF-TPI-015 | Métricas y spans de resolución de tools | `SHOULD` |
| RF-TPI-016 | Ejemplos ejecutables: proveedor custom + composición de host | `MUST` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- Resolución de tools por nodo (`get_tools`) ≤ 5 ms adicionales sobre el merge actual.
- La variante B no debe añadir > 1 ms por nodo al leer el proveedor desde la config.
- La composición del host (`build_default_tool_provider`) corre una vez por arranque (variante A) — sin impacto por request.

### Seguridad
- El proveedor inyectado **no puede saltarse** las capas L1–L5: la ejecución sigue pasando por `react_loop` + el *middleware* de `@prismal_node` (`SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`).
- Los caps de tokens (`_MAX_MCP_TOOLS`, `_MAX_TOTAL_TOOLS`) permanecen en el composite oficial; un host no debe poder superarlos.
- En multi-tenant (variante B), el proveedor de un usuario no debe exponer tools de otro (aislamiento por sesión, sin estado global).

### Disponibilidad
- Ausencia de proveedor (modo no estricto) degrada a stubs con warning — nunca excepción.
- Falla de un sub-proveedor (p. ej. MCP caído) en el composite no impide devolver el resto (skills + stubs), igual que hoy `get_mcp_tools()` devuelve `[]` ante error.

### Escalabilidad
- Soportar N proveedores en el composite sin degradación apreciable.
- Variante B: soportar miles de sesiones concurrentes con proveedores independientes (sin lock global por resolución).

### Observabilidad
- OTel span `prismal.tools.resolve{agent}` con `provider`, `n_tools`, `fallback`.
- Métricas listadas en Y6.
- Log estructurado por resolución: `agent`, `provider`, `live`, `stubs_kept`, `total` (paridad con el log `tool_registry.tools_resolved` actual).

### Mantenibilidad
- Coverage ≥ 85% en módulos nuevos.
- `ruff` + `mypy --strict` + `bandit` clean.
- Imports diferidos: el núcleo no debe importar MCP/Skills en tiempo de import del módulo (respeta `filterwarnings=error`).

### Compatibilidad
- `prismal/` sigue siendo namespace package PEP 420 (no añadir `__init__.py`).
- API pública (`ToolProviderPort`, providers) versionada; breaking requiere bump minor + deprecation 1 release.

---

## 8. Restricciones y Dependencias

### Restricciones Técnicas
- Python 3.13+, `uv`.
- No añadir dependencias obligatorias nuevas al core.
- Los proveedores que tocan SDKs externos (MCP, skills) deben mantener los imports **diferidos** (dentro de métodos), no a nivel de módulo del núcleo.

### Dependencias Externas

| Dependencia | Tipo | Uso | Estado |
|---|---|---|---|
| `prismal/agents/extension/ports.py` | Existente (Fase X) | Base para `ToolProviderPort` (extiende `ToolPort`) | ✅ Presente |
| `prismal.mcp.client.MCPClientManager` | Existente | Envuelto por `McpToolProvider` | ✅ Presente |
| `prismal.skills.manager.SkillsManager` | Existente | Envuelto por `SkillToolProvider` | ✅ Presente |
| `langchain_core.tools.BaseTool` | Existente | Conforma `ToolPort` | ✅ Presente |
| `opentelemetry-api` / `structlog` | Existente | Spans + logs de resolución | ✅ Presente |

**Sin nuevas dependencias** — todo sobre stack ya instalado.

---

## 9. User Stories

### Épica Y: Inyectar el toolset desde el host

**US-TPI-001:** Como Platform Host, quiero componer e inyectar los proveedores de herramientas en el arranque sin que el núcleo conozca MCP ni Skills.
```python
# en prismal-sdk / prismal-web (NO en prismal/agents)
from prismal.agents.extension.providers import (
    McpToolProvider, SkillToolProvider, StubToolProvider, CompositeToolProvider,
)
from prismal.agents.tool_registry import set_tool_provider
from prismal.mcp.client import MCPClientManager
from prismal.skills.manager import SkillsManager

provider = CompositeToolProvider([
    McpToolProvider(MCPClientManager("config/mcp_servers.yaml")),
    SkillToolProvider(SkillsManager()),
    StubToolProvider(),
])
set_tool_provider(provider)
```
- [ ] Los nodos siguen llamando `get_tools_for_agent("coder")` sin cambios.
- [ ] `prismal/agents/**` no importa `prismal.mcp` ni `prismal.skills`.

### Épica Y: Núcleo sin MCP/Skills

**US-TPI-002:** Como Framework Consumer, quiero usar el núcleo de agentes sin instalar MCP ni Skills.
- [ ] Sin proveedor inyectado, `get_tools_for_agent` devuelve stubs + emite warning.
- [ ] Ninguna excepción de import por MCP/Skills ausentes.

### Épica Y: Multi-tenant por sesión

**US-TPI-003:** Como Multi-Tenant Web Operator, quiero que cada usuario vea un toolset propio.
```python
provider = CompositeToolProvider([
    McpToolProvider(mgr_for_user(user)),   # allowlist de servidores del usuario
    SkillToolProvider(skills_for_plan(user.plan)),
    StubToolProvider(),
])
graph = await get_async_compiled_graph(tool_provider=provider)  # variante B
```
- [ ] Dos usuarios concurrentes ven toolsets distintos sin estado global.

### Épica Y: Tests deterministas

**US-TPI-004:** Como Core Maintainer, quiero inyectar un proveedor falso en tests.
```python
set_tool_provider(FakeToolProvider({"researcher": [echo_tool]}))
```
- [ ] El test del agente no requiere MCP ni skills reales.

---

## 10. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Regresión silenciosa si nadie inyecta proveedor | Media | Alto | Fallback a `StubToolProvider` + warning estructurado; `tool_provider_strict=True` para entornos que exijan proveedor |
| Cambio de paridad en el merge (orden/dedupe/caps) | Media | Alto | Tests de paridad byte-a-byte de `get_tools_for_agent` antes/después; los caps quedan en el composite oficial |
| Orden de arranque: nodo corre antes de inyectar | Baja | Alto | Documentar inyección en el lifespan del host; en variante B la resolución es perezosa por nodo |
| Import accidental de MCP/Skills a nivel de módulo rompe `filterwarnings=error` | Media | Medio | Imports diferidos; test de arquitectura que prohíbe `prismal.mcp`/`prismal.skills` en `prismal/agents/**` |
| Multi-tenant: fuga de tools entre sesiones | Baja | Crítico | Variante B sin estado global; proveedor por sesión; test de aislamiento |
| Shims deprecados se usan indefinidamente | Media | Bajo | `DeprecationWarning` + remoción anunciada a 1 minor |
| Sobre-ingeniería del puerto | Baja | Medio | Mantener `get_tools` como único método; sin DI container (DD-EXT-005) |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| Y1 — `ToolProviderPort` | 0.2 semana | Protocol + re-export |
| Y2 — Proveedores concretos | 1 semana | McpToolProvider, SkillToolProvider, StubToolProvider, CompositeToolProvider + tests |
| Y3 — Inyección global (variante A) | 0.8 semana | `set_tool_provider`, refactor `tool_registry`, shims deprecados |
| Y4 — Inyección por contexto (variante B) | 1 semana | Resolución por sesión vía config del grafo + tests de aislamiento |
| Y5 — Composición del host | 0.3 semana | `build_default_tool_provider(settings)` |
| Y6 — Settings + métricas | 0.3 semana | Toggles + counters + spans |
| Y7 — Docs + ejemplos | 0.6 semana | `docs/tool-providers.md` + 2 ejemplos |
| Y8 — Tests + paridad | 0.5 semana | `FakeToolProvider` + tests de paridad |
| Hardening | 0.5 semana | Coverage ≥ 85%, test de arquitectura, security audit |
| **Total** | **~5 semanas** | MCP/Skills inyectables desde el host + multi-tenant opcional |

---

## 12. Definición de Done (Global de Fase Y)

- [x] `ToolProviderPort` declarado y re-exportado.
- [x] `McpToolProvider`, `SkillToolProvider`, `StubToolProvider`, `CompositeToolProvider` implementados y testeados.
- [x] `set_tool_provider()` / `get_tool_provider()` funcionando; `get_tools_for_agent()` delega.
- [x] `prismal/agents/**` sin imports de `prismal.mcp` / `prismal.skills` (verificado por test de arquitectura; exenciones documentadas: `extension/providers.py`, `skill_manager.py`).
- [x] Paridad: salida idéntica de `get_tools_for_agent` con el proveedor por defecto (test).
- [x] `_FIXED_TOOL_AGENTS` y caps de tokens preservados.
- [x] Variante B opcional disponible y con test de aislamiento por sesión.
- [x] `build_default_tool_provider(settings)` documentado para el host.
- [x] Shims `init_mcp/get_mcp_tools/get_skill_tools` deprecados con `DeprecationWarning`.
- [x] `docs/tool-providers.md` + 2 ejemplos ejecutables.
- [x] Coverage ≥ 85% en módulos nuevos (providers 100%, tool_registry 85%).
- [x] Suite verde en el alcance de la fase (2604 passed; ~50 fallos preexistentes ajenos a Fase Y, idénticos en baseline).
- [x] `ruff` + `mypy --strict` + `bandit` clean (en `prismal/` y `tests/`; 20 issues ruff preexistentes en `examples/` multimodal/rag/subgraphs quedan fuera de scope).
- [x] `CLAUDE.md` y `README.md` actualizados.
- [ ] PR mergeado a `main` con code review aprobado.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Versión inicial — inyección de MCP/Skills vía `ToolProviderPort` desde capa externa |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
| DX Lead | — | | ☐ Pendiente |
