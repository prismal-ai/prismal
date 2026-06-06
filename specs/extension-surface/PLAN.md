# Prismal — Extension Surface (LangGraph Passthrough + Plugin SDK)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Resumen Ejecutivo

Prismal envuelve LangGraph internamente: ofrece 26 agentes especialistas, 7 RAG engines, 9 patrones, 11 subgraphs y una capa multimodal planificada. Sin embargo, **no expone una API pública para que un usuario construya patrones nuevos directamente sobre LangGraph** beneficiándose de la infraestructura existente (security 5-layer, OTel, audit, `ProviderRegistry`, capability routing, checkpointing). Hoy, extender prismal requiere o bien forkear el repo, o duplicar wiring de cross-cutting concerns en cada nodo nuevo.

Este documento define una **superficie de extensión deliberada** que convierte a prismal en "LangGraph con baterías incluidas, no LangGraph escondido". Cinco componentes:

1. **Re-export oficial** (`prismal.langgraph`) — punto único, versionado, para `StateGraph`, `Send`, `interrupt`, `add_messages`.
2. **`@prismal_node` decorator** — envuelve cualquier `async (state) → state_update` con security, OTel span, audit, registro de capabilities.
3. **`PrismalStateGraphBuilder`** — fluent API sobre `StateGraph[AgentState]` que aplica defaults de prismal en `add_node()`.
4. **Plugin discovery vía entry points** — `prismal.subgraphs` y `prismal.nodes` permiten que paquetes externos (`prismal-x-finance`, `prismal-x-healthcare`) se auto-registren.
5. **`LangChainRunnableAdapter`** — convierte cualquier `Runnable` / `AgentExecutor` de LangChain en un nodo válido del grafo prismal.

El entregable es **opt-in y aditivo**: ningún consumidor existente se ve afectado. Habilita un ecosistema de plugins externos sin tocar el core, y baja el costo de adopción para equipos con código LangChain previo.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- **Extensibilidad implícita pero no documentada.** `SubgraphRegistry` (`agents/subgraphs/registry.py`) y la convención `register_<name>(registry)` ya permiten registrar subgraphs externos, pero no hay docs ni ejemplos ni un contrato versionado.
- **Cross-cutting por convención, no por contrato.** Cada nodo escribe a mano sus OTel spans, su logger, sus llamadas a `SecurePromptBuilder`/`ActionInterceptor.check()`. Olvidar uno es un bug silencioso (no hay validación).
- **Sin plugin discovery.** Un paquete externo no puede contribuir nodos al supervisor sin pedirle al operador que llame manualmente al register en el startup. No hay `entry_points`, no hay namespace de plugins.
- **LangGraph "oculto" por convención.** Aunque `agents/graph.py` importa LangGraph y lo usa, el usuario externo no sabe qué versión es compatible, qué imports son seguros, ni cómo construir un `StateGraph` aprovechando `AgentState` y `add_messages`.
- **LangChain `Runnable` no se puede usar como nodo** sin escribir el adapter cada vez.

### 2.2 Problema

Sin una superficie de extensión:

1. **Forking es la única opción** para añadir un patrón nuevo no contemplado en Fase A/B/C/F.
2. **Cada equipo reinventa** los wrappers de security/OTel/audit en su nodo custom — con riesgo de saltarse alguno.
3. **No hay ecosistema.** No puede existir `prismal-x-healthcare` o `prismal-x-finance` como paquetes pip independientes.
4. **Curva de adopción alta.** Un equipo que ya usa LangChain/LangGraph debe migrar todo a prismal en vez de adaptar incrementalmente.

### 2.3 Oportunidad

Las primitivas necesarias **ya existen casi todas** en el repo:
- Callable injection (Fase B) ya prueba que los patrones aceptan extensión sin acoplamiento.
- `SubgraphRegistry` + `register_<name>()` ya es el patrón canónico.
- `ProviderRegistry`, `SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`, `OTelManager` son componentes ya disponibles.

Lo que falta es **declarar el contrato y empaquetarlo** como una API pública. El esfuerzo es bajo (cinco módulos pequeños), el impacto en adopción y ecosistema es alto, y no rompe nada existente.

---

## 3. Usuarios Objetivo

### Persona 1: Framework Integrator
- **Descripción:** Ingeniero que integra prismal en un producto y necesita un patrón propietario (ej. workflow de dominio interno con reglas no comunes).
- **Necesidad principal:** Construir nodos custom que participen del state machine sin perder security/observability.
- **Frecuencia de uso:** Semanal/Mensual.

### Persona 2: Plugin Author
- **Descripción:** Mantiene un paquete `prismal-x-<dominio>` distribuido en PyPI con nodos, subgraphs y skills específicos del dominio.
- **Necesidad principal:** Que su paquete se auto-registre al instalar y no requiera modificar el core de prismal.
- **Frecuencia de uso:** Diaria durante desarrollo del plugin.

### Persona 3: LangChain Migrator
- **Descripción:** Equipo con código LangChain (chains, `Runnable`, `AgentExecutor`) que quiere adoptar prismal sin reescribir todo.
- **Necesidad principal:** Adapter de un solo paso que tome su `Runnable` y lo exponga como nodo válido.
- **Frecuencia de uso:** Migración inicial + extensiones puntuales.

### Persona 4: Researcher / Pattern Designer
- **Descripción:** Quiere experimentar con un patrón nuevo (ej. una variante de MCTS, un router con clasificador propio) sin esperar a que entre en el roadmap de prismal.
- **Necesidad principal:** Acceso a `StateGraph`, `Send`, `interrupt` con docs explícitas y `AgentState` reutilizable.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos del Negocio

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Cobertura de cross-cutting automatizado | % de nodos custom que reciben security+OTel+audit sin escribir código | 100% vía `@prismal_node` | Fase X |
| Ecosistema de plugins | Paquetes `prismal-x-*` en PyPI | ≥ 2 paquetes piloto | 3 meses post-merge |
| Tiempo de "hello world" extensión | Minutos desde `pip install prismal` a primer nodo custom funcionando | ≤ 15 min | Fase X |
| Backward compatibility | Tests existentes (~688) pasando sin cambios | 100% | Global |
| Onboarding LangChain | Demo migrando un `AgentExecutor` a nodo prismal | ≤ 30 LoC | Fase X |
| Cobertura de tests | Branch coverage módulos nuevos | ≥ 85% | Global |

### 4.2 Objetivos de Usuario

| Objetivo del Usuario | Indicador |
|---|---|
| Construir un nodo custom en minutos | `@prismal_node` documentado con ejemplo ejecutable |
| Auto-registro de plugin sin tocar core | Entry points `prismal.subgraphs` funcionan via `importlib.metadata` |
| Reutilizar `Runnable` existente | `LangChainRunnableAdapter(runnable).as_node()` |
| Construir un subgraph propio con security/audit gratis | `PrismalStateGraphBuilder` aplica defaults sin pedir nada al usuario |
| Saber qué versión de LangGraph usa prismal | `prismal.langgraph.VERSION` + docstring del módulo |

---

## 5. Alcance

### 5.1 In Scope (Incluido — Fase X)

**X1 — Re-export oficial (`prismal/langgraph.py`):**
- [ ] Módulo `prismal.langgraph` que re-exporta `StateGraph`, `START`, `END`, `Send`, `interrupt`, `add_messages`, `CompiledStateGraph`.
- [ ] Constante `VERSION` con la versión de `langgraph` resuelta vía `importlib.metadata`.
- [ ] Docstring que documente compatibilidad y deprecación.

**X2 — Decorator `@prismal_node` (`prismal/agents/extension/decorators.py`):**
- [ ] Decorator que envuelve `async (state: AgentState) → dict` con OTel span, logger estructurado, audit hook, manejo de errores → `PrismalError`.
- [ ] Parámetros: `name`, `capabilities`, `security`, `audit`, `retry`, `timeout_s`.
- [ ] Registro automático en `DEFAULT_CAPABILITY_MAP` del `tool_registry` cuando el módulo se importa.

**X3 — Builder fluent (`prismal/agents/extension/builder.py`):**
- [ ] `PrismalStateGraphBuilder` que envuelve `StateGraph[AgentState]` con métodos `.add_node()`, `.add_edge()`, `.add_conditional_edges()`, `.add_supervisor()`, `.add_security_layer()`, `.compile()`.
- [ ] Cada `.add_node()` aplica el equivalente a `@prismal_node` si el callable no lo está ya.

**X4 — Plugin discovery (`prismal/agents/extension/plugins.py`):**
- [ ] Entry point groups: `prismal.subgraphs`, `prismal.nodes`, `prismal.tools`, `prismal.rag_engines`.
- [ ] `discover_plugins()` que itera vía `importlib.metadata.entry_points()` y llama a la función `register(registry)` declarada por cada plugin.
- [ ] Toggle `settings.plugins_autodiscover` (default `True`) para deshabilitar en entornos sandboxed.
- [ ] CLI helper (opcional): `python -m prismal.plugins list`.

**X5 — `LangChainRunnableAdapter` (`prismal/agents/extension/adapters.py`):**
- [ ] Wrapper que toma un `Runnable` o `AgentExecutor` de LangChain y retorna una función async `(state) → state_update`.
- [ ] `as_node(name=..., capabilities=...)` para registro directo.
- [ ] Mapeo automático de `state["messages"]` ↔ input/output del Runnable.

**X6 — Ports y Adapters formalizados (`prismal/agents/extension/ports.py`):**
- [ ] `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` como `Protocol` explícitos.
- [ ] Adaptadores existentes (SqliteSaver, AuditLogger, etc.) declaran cumplimiento.
- [ ] Permite que usuarios sustituyan implementaciones sin tocar el core.

**X7 — Documentación y ejemplos:**
- [ ] `docs/extension.md` con quickstart + recetas.
- [ ] `examples/custom_node.py` — nodo custom con `@prismal_node`.
- [ ] `examples/custom_subgraph.py` — subgraph custom con `PrismalStateGraphBuilder`.
- [ ] `examples/plugin_template/` — esqueleto de paquete `prismal-x-<name>` listo para PyPI.
- [ ] `examples/langchain_migration.py` — migración de `AgentExecutor` a nodo prismal.

**X8 — Settings y observabilidad:**
- [ ] `settings.plugins_autodiscover: bool = True`.
- [ ] `settings.plugins_allowlist: list[str] = []` (vacío = todos los descubiertos).
- [ ] `settings.plugins_denylist: list[str] = []`.
- [ ] Métricas: `prismal_plugins_discovered_total`, `prismal_plugins_loaded_total{status="success|error"}`, `prismal_custom_nodes_invocations_total{node}`.

### 5.2 Out of Scope (Excluido)

- **Contenedor DI completo** (estilo `dependency-injector`) — overhead alto vs beneficio actual; el patrón "inject `settings: Settings | None = None`" basta.
- **DSL propio sobre LangGraph** — rompería el principio "es LangGraph estándar"; el usuario debe poder leer docs de LangGraph y aplicarlas tal cual.
- **Hot reload de plugins** — requiere infraestructura compleja; reinicio del proceso es aceptable.
- **Marketplace de plugins** — fuera del scope del framework; se delegaría a una propiedad externa (`plugins.prismal.dev`) en fases posteriores.
- **Migración automatizada de chains LangChain** — el adapter resuelve `Runnable`; transformaciones más profundas son responsabilidad del usuario.
- **Firma criptográfica de plugins** — Fase Y; en X confiamos en el ecosistema PyPI estándar + allowlist/denylist por nombre.

### 5.3 Futuras Consideraciones (Fase Y+)

- Plugin signing + verificación opcional.
- Hot reload vía `watchdog`.
- Plugin marketplace UI.
- Schema validation declarativa de inputs/outputs de nodos (con Pydantic models opcionales).
- Cuotas/sandboxing por plugin (CPU/memoria/red).
- Telemetría agregada por plugin (latencia, errores, uso).

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-EXT-001 | `prismal.langgraph` re-exporta símbolos de LangGraph con versión declarada | `MUST` |
| RF-EXT-002 | `@prismal_node` envuelve callables con OTel span + logger + audit + error handling | `MUST` |
| RF-EXT-003 | `@prismal_node` registra capabilities automáticamente al import | `SHOULD` |
| RF-EXT-004 | `PrismalStateGraphBuilder` provee fluent API sobre `StateGraph[AgentState]` | `MUST` |
| RF-EXT-005 | `discover_plugins()` itera entry points y llama `register(registry)` | `MUST` |
| RF-EXT-006 | Toggle `plugins_autodiscover` + allowlist/denylist por settings | `MUST` |
| RF-EXT-007 | `LangChainRunnableAdapter` convierte `Runnable`/`AgentExecutor` a nodo válido | `SHOULD` |
| RF-EXT-008 | `Protocol`s explícitos para `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` | `SHOULD` |
| RF-EXT-009 | Ejemplos ejecutables: nodo custom, subgraph custom, plugin template, migración LangChain | `MUST` |
| RF-EXT-010 | Métricas de plugins: `discovered_total`, `loaded_total`, `custom_nodes_invocations_total` | `SHOULD` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- Overhead del decorator `@prismal_node` ≤ 5 ms por invocación (excluyendo OTel exporter, que es async).
- Plugin discovery al startup: ≤ 500 ms para 50 plugins instalados.
- `PrismalStateGraphBuilder.compile()` no debe ser más lento que `StateGraph.compile()` + 50 ms (overhead de wrapping).

### Seguridad
- Plugin allowlist/denylist enforcement antes de invocar `register()` del plugin.
- Cada nodo registrado vía `@prismal_node` debe pasar por `ActionInterceptor` si declara `tool_calls`.
- `LangChainRunnableAdapter` aplica `SecurePromptBuilder` al input antes de invocar el `Runnable`.
- Audit log registra carga de cada plugin con su versión, hash del wheel (cuando disponible vía `importlib.metadata`), y entry point usado.

### Disponibilidad
- Falla en carga de un plugin **no impide** el startup del resto: log estructurado de error + métrica `plugins_loaded_total{status="error"}`, continuación normal.
- Plugin con error en runtime no debe tumbar el grafo principal — el `@prismal_node` wrapper captura excepciones y emite `state_update` con flag `error=True`.

### Escalabilidad
- Soportar ≥ 50 plugins instalados sin degradación apreciable.
- Soportar ≥ 200 nodos custom registrados con `@prismal_node`.

### Observabilidad
- OTel spans: `prismal.ext.discover`, `prismal.ext.load_plugin{name}`, `prismal.ext.node{name}`, `prismal.ext.adapter.langchain`.
- Métricas Prometheus-compatibles ya listadas en X8.
- Logs estructurados con `plugin_name`, `node_name`, `entry_point`.

### Mantenibilidad
- Coverage ≥ 85% por módulo nuevo (target más alto que el 80% global por ser API pública).
- `ruff` + `mypy --strict` + `bandit` clean.
- API versionada: cambios breaking de la superficie de extensión requieren bump de minor en SemVer y deprecation warning 1 release antes.

### Documentación
- Quickstart de 1 página.
- Recetario de patrones comunes (router custom, gate condicional, post-processor, etc.).
- Plantilla de plugin lista para `cookiecutter` o `copier`.
- Migration guide desde LangChain.

---

## 8. Restricciones y Dependencias

### Restricciones Técnicas
- Python 3.13+, `uv` como gestor.
- Mantener `prismal/` como namespace package PEP 420.
- Compatibilidad: el contrato de `@prismal_node` debe funcionar con LangGraph ≥ 0.4 (versión actual del repo).
- Sin nuevas dependencias obligatorias para el core (todo nuevo debe ser stdlib o ya presente).

### Dependencias Externas

| Dependencia | Tipo | Uso | Estado |
|---|---|---|---|
| `langgraph` | Existente | Re-export y builder | ✅ Ya incluida |
| `langchain-core` | Existente | `Runnable` interface para adapter | ✅ Ya incluida |
| `importlib.metadata` | Stdlib | Plugin discovery | ✅ Stdlib |
| `opentelemetry-api` | Existente | Spans en decorator | ✅ Ya incluida |
| `structlog` | Existente | Logging | ✅ Ya incluida |

**Sin nuevas dependencias** — todo el trabajo es sobre stack ya instalado.

---

## 9. User Stories

### Épica X: Build Your Own Node

**US-EXT-001:** Como Framework Integrator, quiero decorar mi función async como nodo prismal para recibir security, OTel y audit sin escribir el wiring.
```python
from prismal.agents.extension import prismal_node

@prismal_node(name="my_classifier", capabilities=["general"])
async def my_classifier(state):
    return {"messages": [...], "metadata": {"my_node": {"score": 0.8}}}
```
- [ ] Funciona sin más config.
- [ ] OTel span aparece en el trace export con `node.name="my_classifier"`.
- [ ] Audit log contiene una entrada por invocación.

### Épica X: Build Your Own Subgraph

**US-EXT-002:** Como Framework Integrator, quiero ensamblar un subgraph con fluent API que aplique defaults.
```python
from prismal.agents.extension import PrismalStateGraphBuilder

builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn)        # se auto-wrapea con @prismal_node
builder.add_node("respond", respond_fn)
builder.add_edge("classify", "respond")
subgraph = builder.compile()
```
- [ ] `subgraph` es un `SubgraphDefinition` registrable.

### Épica X: Plugin Ecosystem

**US-EXT-003:** Como Plugin Author, quiero publicar `prismal-x-healthcare` que se auto-registre al instalar.
```toml
# pyproject.toml del plugin
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare:register_healthcare_pipeline"
```
- [ ] Tras `pip install prismal-x-healthcare`, al hacer `discover_plugins()` el subgraph queda registrado.
- [ ] Operador puede desactivarlo vía `settings.plugins_denylist=["prismal_x_healthcare"]`.

### Épica X: LangChain Bridge

**US-EXT-004:** Como LangChain Migrator, quiero usar mi `AgentExecutor` como nodo prismal sin reescribirlo.
```python
from prismal.agents.extension import LangChainRunnableAdapter

adapter = LangChainRunnableAdapter(my_agent_executor)
node = adapter.as_node(name="legacy_agent", capabilities=["research"])
```
- [ ] El nodo participa del state machine prismal.
- [ ] Input/output `state["messages"]` se mapean correctamente.

---

## 10. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Plugin malicioso ejecuta código arbitrario al carga | Media | Crítico | Allowlist/denylist por settings; audit log de cada carga; documentar que entry points son confianza explícita del operador |
| Decorator overhead suma latencia perceptible | Baja | Medio | Benchmark en CI (target ≤ 5 ms); cache de spans; opt-out via `@prismal_node(otel=False)` |
| Drift entre versión de LangGraph documentada y real | Media | Alto | `prismal.langgraph.VERSION` se resuelve dinámicamente; tests de compatibilidad en CI por cada upgrade |
| Adapter LangChain rompe con cambio de API en `Runnable` | Media | Medio | Pin de versión mínima en `pyproject.toml`; tests de smoke por release de LangChain |
| `discover_plugins()` falla y bloquea startup | Baja | Alto | Cada plugin se carga en try/except aislado; falla individual no afecta al resto |
| Conflicto de nombres entre plugins | Media | Medio | Registry detecta duplicados y rechaza con error claro; namespace recomendado `<vendor>_<name>` |
| Plugins instalados sin saberlo (autodiscover default `True`) | Media | Alto | Toggle disponible; log de plugins cargados visible en startup; allowlist como modo strict |
| Backward compat de `@prismal_node` se rompe entre versiones | Baja | Alto | API congelada con `@frozen_api` decorator interno; deprecation cycle de 1 minor mínimo |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| X1 — Re-export oficial | 0.2 semana | `prismal.langgraph` |
| X2 — Decorator `@prismal_node` | 1 semana | Decorator + registro automático + tests |
| X3 — Builder fluent | 0.8 semana | `PrismalStateGraphBuilder` + tests |
| X4 — Plugin discovery | 1 semana | Entry points + `discover_plugins()` + CLI + tests |
| X5 — Adapter LangChain | 0.5 semana | `LangChainRunnableAdapter` + tests |
| X6 — Ports formalizados | 0.5 semana | `Protocol`s + smoke tests |
| X7 — Docs + ejemplos | 1 semana | 4 ejemplos ejecutables + `docs/extension.md` |
| X8 — Settings + métricas | 0.2 semana | Toggles + counters |
| Hardening | 0.8 semana | Coverage ≥ 85%, security audit, ejemplo de plugin externo en TestPyPI |
| **Total** | **~6 semanas** | Superficie de extensión completa + ecosistema piloto |

---

## 12. Definición de Done (Global de Fase X)

- [ ] `prismal.langgraph` re-exporta los 7 símbolos clave + `VERSION`.
- [ ] `@prismal_node` documentado y testeado con ejemplos.
- [ ] `PrismalStateGraphBuilder` con fluent API funcional.
- [ ] `discover_plugins()` carga plugins desde entry points + respeta allow/denylist.
- [ ] `LangChainRunnableAdapter` convierte `Runnable` y `AgentExecutor` a nodos válidos.
- [ ] 4 `Protocol`s de ports declarados + adapters existentes conformes.
- [ ] 4 ejemplos ejecutables en `examples/`.
- [ ] `docs/extension.md` publicado.
- [ ] Plugin template (`examples/plugin_template/`) listo para `cookiecutter` o `copier`.
- [ ] Coverage ≥ 85% en `prismal/agents/extension/` y `prismal/langgraph.py`.
- [ ] Benchmark de overhead del decorator ≤ 5 ms documentado.
- [ ] 2 plugins piloto publicados en TestPyPI (ej. `prismal-x-hello`, `prismal-x-financial-extra`).
- [ ] `uv run pytest -m "not live_api"` pasa al 100%.
- [ ] `ruff` + `mypy --strict` + `bandit` clean.
- [ ] `CLAUDE.md` y `README.md` actualizados.
- [ ] PR mergeado a `main` con code review aprobado.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — superficie de extensión LangGraph + plugin SDK |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
| DX Lead | — | | ☐ Pendiente |
