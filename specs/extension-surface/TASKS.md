# Prismal Extension Surface — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-05-27 |
| **PLAN** | `specs/extension-surface/PLAN.md` |
| **Architecture** | `specs/extension-surface/ARCHITECTURE.md` |
| **SPEC** | `specs/extension-surface/SPEC.md` |

---

## 1. Resumen de Implementación

La Fase X se divide en **8 sub-fases secuenciales** más hardening:

- **X1 (0.2 semana):** Re-export oficial `prismal.langgraph`.
- **X2 (1 semana):** Decorator `@prismal_node` + middleware chain interna.
- **X3 (0.8 semana):** `PrismalStateGraphBuilder` fluent API.
- **X4 (1 semana):** Plugin discovery vía entry points + CLI.
- **X5 (0.5 semana):** `LangChainRunnableAdapter`.
- **X6 (0.5 semana):** Ports formalizados (`Protocol`s) + verificación de conformidad existente.
- **X7 (1 semana):** Documentación + 4 ejemplos ejecutables + plugin template.
- **X8 (0.2 semana):** Settings + métricas + audit hooks.
- **Hardening (0.8 semana):** Coverage ≥ 85%, benchmark del decorator, security audit, publicación de 2 plugins piloto a TestPyPI.

**Duración total estimada:** ~6 semanas
**Equipo mínimo:** 1 engineer senior con experiencia en LangGraph + Python tooling (entry points, packaging).
**Fecha objetivo:** 2026-07-10

---

## 2. Pre-requisitos

| Pre-requisito | Owner | Estado | Fecha Límite |
|---|---|---|---|
| PLAN.md aprobado | Tech Lead + DX Lead | ☐ Pendiente | 2026-06-01 |
| ARCHITECTURE.md aprobado | Tech Lead + AI Architect | ☐ Pendiente | 2026-06-01 |
| SPEC.md aprobado | Tech Lead | ☐ Pendiente | 2026-06-01 |
| Decisión sobre default de `@prismal_node(security=...)` | Tech Lead | ☐ Pendiente | Inicio X2 |
| Decisión sobre `cookiecutter` vs `copier` para template | DX Lead | ☐ Pendiente | Inicio X7 |
| Branch `feature/extension-surface` creado | Engineer | ☐ Pendiente | Inicio X1 |
| Suite de tests existente pasa al 100% (688+) | Engineer | ☐ Verificar | Inicio X1 |
| Nombre reservado en TestPyPI: `prismal-x-hello` | DevOps | ☐ Pendiente | Antes de Hardening |

---

## 3. Fases de Implementación

---

### FASE X1 — Re-export Oficial

**Duración:** 0.2 semana | **Archivo:** `prismal/langgraph.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X1-01 | Crear `prismal/langgraph.py` con re-exports + `VERSION` | 0.2d | — | ☐ |
| X1-02 | Docstring completo con ejemplo y nota de versionado | 0.2d | X1-01 | ☐ |
| X1-03 | Test unitario: importar cada símbolo, verificar identidad con upstream | 0.3d | X1-01 | ☐ |
| X1-04 | Test que `VERSION` no esté vacío y matchee `importlib.metadata.version("langgraph")` | 0.1d | X1-01 | ☐ |
| X1-05 | Añadir a `__init__.py` (si namespace, evitar) o documentar import path | 0.1d | X1-01 | ☐ |

**Criterios de Done X1:**
- `from prismal.langgraph import StateGraph` funciona y es idéntico al upstream.
- `prismal.langgraph.VERSION` retorna versión resuelta dinámicamente.
- Docstring incluye ejemplo ejecutable.
- Coverage ≥ 90% (es un módulo pequeño).

---

### FASE X2 — Decorator `@prismal_node`

**Duración:** 1 semana | **Archivos:** `prismal/agents/extension/decorators.py`, `_middleware.py`, `_registry.py`

#### X2-01 — Estructura base
**Estimación:** 1 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X2-01-01 | Crear `prismal/agents/extension/` con `__init__.py` (re-exports vacíos por ahora) | 0.1d | — | ☐ |
| X2-01-02 | `decorators.py` con `NodeMetadata`, `RetryPolicy`, `SecurityLevel` dataclasses/types | 0.4d | X2-01-01 | ☐ |
| X2-01-03 | `_registry.py` con `_REGISTERED_NODES: dict[str, NodeMetadata]` thread-safe | 0.3d | X2-01-02 | ☐ |
| X2-01-04 | Tests de las dataclasses (frozen, equality, repr) | 0.2d | X2-01-02 | ☐ |

#### X2-02 — Middleware chain interna
**Estimación:** 2 días | **Archivo:** `_middleware.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X2-02-01 | Signatura `Middleware = Callable[[NodeFn, AgentState, NodeMetadata], Awaitable[dict]]` | 0.2d | X2-01 | ☐ |
| X2-02-02 | `security_middleware` que aplica `InputSanitizer` + `SecurePromptBuilder` según level | 0.5d | X2-02-01 | ☐ |
| X2-02-03 | `otel_middleware` con span open/close, attrs estándar | 0.3d | X2-02-01 | ☐ |
| X2-02-04 | `logger_middleware` con `structlog.bind()` contextual | 0.2d | X2-02-01 | ☐ |
| X2-02-05 | `retry_middleware` con exponential backoff configurable | 0.4d | X2-02-01 | ☐ |
| X2-02-06 | `timeout_middleware` con `asyncio.wait_for` + mapeo a `NodeTimeoutError` | 0.2d | X2-02-01 | ☐ |
| X2-02-07 | `audit_middleware` con hash xxhash de state_update + duration_ms | 0.3d | X2-02-01 | ☐ |
| X2-02-08 | `error_mapping_middleware` que captura BaseException → `NodeExecutionError` o state_update con error | 0.4d | X2-02-01 | ☐ |
| X2-02-09 | `build_pipeline()` que compone middlewares en orden inverso | 0.3d | X2-02-02..08 | ☐ |
| X2-02-10 | Tests unitarios por middleware (≥10 tests, cubrir on/off de cada uno) | 0.6d | X2-02-09 | ☐ |

#### X2-03 — Decorator público
**Estimación:** 1.5 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X2-03-01 | `prismal_node()` factory que construye `NodeMetadata` y aplica `build_pipeline()` | 0.6d | X2-02 | ☐ |
| X2-03-02 | Side effect: registro en `_REGISTERED_NODES` + `DEFAULT_CAPABILITY_MAP` | 0.3d | X2-03-01 | ☐ |
| X2-03-03 | `list_registered_nodes()` + `get_node_metadata()` | 0.2d | X2-03-02 | ☐ |
| X2-03-04 | Atributos `__prismal_node__` y `__wrapped__` en el callable retornado | 0.2d | X2-03-01 | ☐ |
| X2-03-05 | Tests de decorator: con/sin params, doble decoración (idempotente), introspección | 0.5d | X2-03-01..04 | ☐ |
| X2-03-06 | Excepciones `NodeExecutionError`, `NodeTimeoutError`, `NodeValidationError` en `core/exceptions.py` | 0.2d | — | ☐ |

#### X2-04 — Benchmark del decorator
**Estimación:** 0.5 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X2-04-01 | Test `@pytest.mark.bench` que mide overhead vs función desnuda | 0.3d | X2-03 | ☐ |
| X2-04-02 | Target documentado: ≤ 5 ms p95 por invocación | 0.1d | X2-04-01 | ☐ |
| X2-04-03 | CI step opcional que reporta benchmark al PR | 0.2d | X2-04-01 | ☐ |

**Criterios Globales X2:**
- Cada middleware tiene tests aislados.
- `@prismal_node` documentado con ejemplo ejecutable en docstring.
- Benchmark documentado en `docs/extension.md`.
- Coverage ≥ 85% en `decorators.py` y `_middleware.py`.

---

### FASE X3 — Builder Fluent

**Duración:** 0.8 semana | **Archivo:** `prismal/agents/extension/builder.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X3-01 | `BuilderDefaults` dataclass | 0.1d | — | ☐ |
| X3-02 | `PrismalStateGraphBuilder.__init__` con `StateGraph[AgentState]` interno | 0.3d | X2 | ☐ |
| X3-03 | `add_node()` con auto-wrap si callable no tiene `__prismal_node__` | 0.5d | X3-02 | ☐ |
| X3-04 | `add_edge()`, `add_conditional_edges()`, `set_entry_point()` (passthrough) | 0.3d | X3-02 | ☐ |
| X3-05 | `add_supervisor_node()` con validación de `valid_next` | 0.4d | X3-02 | ☐ |
| X3-06 | `add_security_layer(at="entry"|"exit")` insertando nodo dedicado | 0.3d | X3-02 | ☐ |
| X3-07 | `compile()` retorna `SubgraphDefinition` con metadata enriquecida | 0.4d | X3-02..06 | ☐ |
| X3-08 | `compile_raw()` retorna `CompiledStateGraph` (escape hatch) | 0.1d | X3-07 | ☐ |
| X3-09 | Tests: fluent API completa, duplicados, validación de aristas, supervisor inválido | 0.7d | X3-07 | ☐ |

**Criterios Globales X3:**
- Builder ejecuta sin diferencia funcional con `StateGraph` directo (regresión zero).
- Auto-wrap detectado vía `hasattr(fn, "__prismal_node__")`.
- Coverage ≥ 85%.

---

### FASE X4 — Plugin Discovery

**Duración:** 1 semana | **Archivos:** `plugins.py`, `prismal/agents/extension/plugins.py`

#### X4-01 — Discovery core
**Estimación:** 2.5 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X4-01-01 | `PluginGroup`, `PluginInfo`, `PluginLoadResult`, `DiscoveryReport` dataclasses | 0.3d | — | ☐ |
| X4-01-02 | `_iter_entry_points(group)` wrapper sobre `importlib.metadata.entry_points` | 0.3d | X4-01-01 | ☐ |
| X4-01-03 | `_load_subgraph_plugin(ep)` invoca `register(registry)` con try/except | 0.4d | X4-01-02 | ☐ |
| X4-01-04 | `_load_node_plugin(ep)` importa callable y verifica `__prismal_node__` | 0.3d | X4-01-02 | ☐ |
| X4-01-05 | `_load_tool_plugin(ep)` añade a `tool_registry` respetando cap=120 | 0.4d | X4-01-02 | ☐ |
| X4-01-06 | `_load_rag_engine_plugin(ep)` registra en nuevo `RAGEngineRegistry` | 0.3d | X4-01-02 | ☐ |
| X4-01-07 | `discover_plugins(settings, registry, groups)` orquesta | 0.5d | X4-01-03..06 | ☐ |
| X4-01-08 | Allowlist/denylist enforcement con precedencia documented | 0.3d | X4-01-07 | ☐ |

#### X4-02 — Audit y observabilidad
**Estimación:** 0.5 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X4-02-01 | `AuditLogger.log_event("plugin_loaded", payload)` por cada carga | 0.2d | X4-01-07, X2 | ☐ |
| X4-02-02 | Métricas: `prismal_plugins_loaded_total{name,status,group}`, `plugin_load_duration_seconds` | 0.2d | X4-01-07 | ☐ |
| X4-02-03 | Startup report estructurado con `loaded/failed/skipped` | 0.1d | X4-01-07 | ☐ |

#### X4-03 — `list_plugins()` y `get_plugin_info()`
**Estimación:** 0.5 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X4-03-01 | `list_plugins()` sin cargar — sólo inspecciona entry points | 0.3d | X4-01-02 | ☐ |
| X4-03-02 | `get_plugin_info(name)` retorna `PluginInfo` o None | 0.2d | X4-03-01 | ☐ |

#### X4-04 — CLI `python -m prismal.plugins`
**Estimación:** 1 día

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X4-04-01 | `prismal/plugins.py` con `main(argv)` y dispatch de subcomandos | 0.4d | X4-03 | ☐ |
| X4-04-02 | `list` — tabla con name, group, version, status | 0.2d | X4-04-01 | ☐ |
| X4-04-03 | `info <name>` — detalle (module, object, dist_version) | 0.1d | X4-04-01 | ☐ |
| X4-04-04 | `doctor` — intenta cargar todos y reporta errores formatted | 0.3d | X4-04-01 | ☐ |
| X4-04-05 | Tests CLI con `argparse` namespace mockeado | 0.3d | X4-04-04 | ☐ |

#### X4-05 — Tests
**Estimación:** 1.5 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X4-05-01 | Unit tests con `monkeypatch` de `entry_points` (≥ 15 tests) | 0.8d | X4-01..03 | ☐ |
| X4-05-02 | Integration test: crea wheel temporal con `build`, instala en venv aislado, descubre | 0.7d | X4-01 | ☐ |

**Criterios Globales X4:**
- Falla en un plugin no aborta el resto (test específico).
- Allowlist + denylist con casos edge cubiertos.
- CLI ejecutable: `python -m prismal.plugins list` produce output legible.
- Coverage ≥ 85% en `plugins.py`.

---

### FASE X5 — LangChain Adapter

**Duración:** 0.5 semana | **Archivo:** `prismal/agents/extension/adapters.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X5-01 | `LangChainRunnableAdapter.__init__` con detección de signature | 0.3d | — | ☐ |
| X5-02 | `_map_input(state)` con `input_mapping` auto/messages/input_dict | 0.5d | X5-01 | ☐ |
| X5-03 | `_map_output(raw)` con detección AIMessage / str / dict | 0.4d | X5-01 | ☐ |
| X5-04 | `ainvoke(state)` que orquesta map → runnable → map | 0.3d | X5-02, X5-03 | ☐ |
| X5-05 | `as_node(name, capabilities, security, timeout_s)` aplica `@prismal_node` | 0.3d | X5-04, X2 | ☐ |
| X5-06 | Soporte explícito de `AgentExecutor` (subclass de `Runnable`) | 0.2d | X5-04 | ☐ |
| X5-07 | Tests con `RunnableLambda`, `RunnableSequence`, `AgentExecutor` mockeado (≥ 12 tests) | 0.7d | X5-04..06 | ☐ |
| X5-08 | Excepción `LangChainAdapterError` en `core/exceptions.py` | 0.1d | — | ☐ |

**Criterios Globales X5:**
- Adapter soporta los 4 tipos comunes de Runnable.
- Test integration con `AgentExecutor` real + LLM mockeado (`live_api` opcional).
- Coverage ≥ 85%.

---

### FASE X6 — Ports Formalizados

**Duración:** 0.5 semana | **Archivo:** `prismal/agents/extension/ports.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X6-01 | Definir `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` como `Protocol` con `@runtime_checkable` | 0.5d | — | ☐ |
| X6-02 | Helper `conforms_to(obj, port)` con `isinstance(obj, port)` | 0.1d | X6-01 | ☐ |
| X6-03 | Tests de conformidad: `AsyncSqliteSaver` ⊨ `CheckpointPort`, `AuditLogger` ⊨ `AuditPort`, embeddings de Chroma ⊨ `EmbeddingsPort`, `BaseTool` ⊨ `ToolPort` | 0.5d | X6-01 | ☐ |
| X6-04 | Docstring de cada Protocol con ejemplos de implementaciones que cumplen | 0.3d | X6-01 | ☐ |
| X6-05 | Test que un mock que NO cumple → `conforms_to` retorna False | 0.2d | X6-02 | ☐ |

**Criterios Globales X6:**
- Las implementaciones existentes cumplen sin cambios (regresión zero).
- Coverage ≥ 90% (módulo pequeño y declarativo).

---

### FASE X7 — Docs + Ejemplos + Plugin Template

**Duración:** 1 semana

#### X7-01 — Documentación
**Estimación:** 2 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X7-01-01 | `docs/extension.md` — Quickstart: hello world en ≤ 15 min | 0.5d | X2 | ☐ |
| X7-01-02 | `docs/extension.md` — Recetario: router custom, gate, post-processor, supervisor wrapper | 0.5d | X3 | ☐ |
| X7-01-03 | `docs/extension.md` — Plugin lifecycle: declaración, instalación, allowlist, troubleshooting | 0.4d | X4 | ☐ |
| X7-01-04 | `docs/extension.md` — LangChain migration guide | 0.4d | X5 | ☐ |
| X7-01-05 | `docs/extension.md` — Ports y adapters: cómo sustituir checkpointer | 0.2d | X6 | ☐ |

#### X7-02 — Ejemplos ejecutables
**Estimación:** 1.5 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X7-02-01 | `examples/custom_node.py` — hello world `@prismal_node` con LLM mockeado | 0.3d | X2 | ☐ |
| X7-02-02 | `examples/custom_subgraph.py` — `PrismalStateGraphBuilder` end-to-end | 0.4d | X3 | ☐ |
| X7-02-03 | `examples/langchain_migration.py` — `AgentExecutor` → nodo via adapter | 0.4d | X5 | ☐ |
| X7-02-04 | `examples/discover_plugins.py` — `discover_plugins()` con plugin in-memory | 0.4d | X4 | ☐ |

#### X7-03 — Plugin Template
**Estimación:** 1.5 días

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X7-03-01 | Decidir `cookiecutter` vs `copier` | 0.1d | — | ☐ |
| X7-03-02 | `examples/plugin_template/` con `pyproject.toml`, `src/{{name}}/`, `tests/`, `README.md` | 0.5d | X7-03-01 | ☐ |
| X7-03-03 | Template incluye `register_<name>()` ejemplo con `PrismalStateGraphBuilder` | 0.3d | X7-03-02 | ☐ |
| X7-03-04 | README del template con instrucciones de uso | 0.2d | X7-03-02 | ☐ |
| X7-03-05 | Test que el template genera un paquete instalable + descubierto por `discover_plugins()` | 0.4d | X7-03-03 | ☐ |

**Criterios Globales X7:**
- Cada ejemplo se ejecuta con `python examples/<name>.py` sin error.
- Quickstart de docs reproduce ejemplo en ≤ 15 min.
- Plugin template genera paquete instalable.

---

### FASE X8 — Settings + Métricas + Audit

**Duración:** 0.2 semana | **Archivo:** `prismal/core/config.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| X8-01 | Añadir `plugins_*` settings con env vars `PRISMAL_PLUGINS_*` | 0.3d | — | ☐ |
| X8-02 | Añadir `extension_default_*` settings | 0.2d | — | ☐ |
| X8-03 | `env.example` actualizado con nuevas variables | 0.1d | X8-01 | ☐ |
| X8-04 | Tests validación de settings (Pydantic constraints) | 0.2d | X8-01 | ☐ |
| X8-05 | Métricas registradas en `monitoring/` (counters + histogramas) | 0.3d | X4, X5 | ☐ |

---

### HARDENING — Coverage, Bench, Plugins Piloto

**Duración:** 0.8 semana

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| H-01 | Coverage audit: módulos nuevos ≥ 85% | 0.5d | X1..X8 | ☐ |
| H-02 | `bandit -r prismal -c pyproject.toml` HIGH=0 MEDIUM=0 | 0.2d | X1..X8 | ☐ |
| H-03 | Benchmark publicado en `docs/extension.md` con overhead del decorator | 0.2d | X2-04 | ☐ |
| H-04 | Plugin piloto `prismal-x-hello` publicado a TestPyPI | 0.5d | X4, X7 | ☐ |
| H-05 | Plugin piloto `prismal-x-financial-extra` publicado a TestPyPI | 0.5d | X4, X7 | ☐ |
| H-06 | Test integration: ambos plugins se instalan + descubren en venv limpio | 0.3d | H-04, H-05 | ☐ |
| H-07 | `pytest -m "not live_api"` pasa al 100% (~828 tests esperados) | 0.2d | X1..X8 | ☐ |
| H-08 | `ruff check .` + `mypy prismal --strict` clean | 0.2d | X1..X8 | ☐ |
| H-09 | Actualizar `CLAUDE.md` con sección "Extension surface" | 0.2d | X1..X8 | ☐ |
| H-10 | Actualizar `README.md` con features + sección extension | 0.3d | X1..X8 | ☐ |
| H-11 | Actualizar `CHANGELOG.md` con entrada Fase X | 0.1d | — | ☐ |
| H-12 | Code review interno (1 reviewer aprueba PR) | 0.8d | H-01..11 | ☐ |
| H-13 | Merge a `main` | 0.1d | H-12 | ☐ |

---

## 4. Dependencias Inter-Tareas

```
X1 (re-export) ─┐
                ▶ X2 (decorator) ─┬──▶ X3 (builder) ──┐
                                  │                    │
                                  │                    ▶ X7 (docs+examples) ─┐
                                  │                    │                       │
                                  ▶ X5 (adapter) ─────┘                       │
                                                                                │
X4 (plugins) ───────────────────────────────────────────────────────────────────┤
                                                                                │
X6 (ports) ─────────────────────────────────────────────────────────────────────┤
                                                                                │
X8 (settings) ──────────────────────────────────────────────────────────────────┤
                                                                                ▼
                                                                         HARDENING
                                                                              │
                                                                              ▼
                                                                            MERGE
```

- X2 → X3 (builder usa decorator para auto-wrap).
- X2 → X5 (adapter aplica `@prismal_node` al output).
- X4 puede arrancar día 1 en paralelo (sólo depende de stdlib).
- X6 puede arrancar día 1 (sólo declaraciones de Protocol).
- X7 espera X2 + X3 + X4 + X5 mínimo.
- X8 puede arrancar día 1 (settings nuevos sin consumidores hasta X4/X2).

---

## 5. Matriz de Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación | Owner |
|---|---|---|---|---|
| Overhead del decorator > 5 ms | Media | Medio | Bench en CI; opt-out granular por param; cache de spans | Engineer |
| Plugin malicioso compromete startup | Media | Crítico | Allowlist en producción; audit log; documentar política | Tech Lead |
| Entry points API cambia en Python | Baja | Alto | Pin min Python 3.13; usar interfaz estable `importlib.metadata.entry_points(group=)` | Engineer |
| LangChain `Runnable` API rompe entre versiones | Media | Medio | Pin min LangChain en pyproject; CI smoke test por release | Engineer |
| Conflictos de nombres entre plugins | Media | Medio | Registry detecta y rechaza; convención `<vendor>_<name>` recomendada | Tech Lead |
| Plugins instalados sin saberlo (autodiscover True) | Media | Alto | Toggle visible en startup; doc recomienda allowlist en prod | DX Lead |
| Backward compat del decorator se rompe | Baja | Alto | `@frozen_api` interno; CI valida firmas; deprecation cycle obligatorio | Engineer |
| Adapter LangChain mapea mal output complejo | Media | Medio | Casos cubiertos: AIMessage/str/dict; doc explícita; error claro si fail | Engineer |
| Plugin template diverge de prácticas reales | Baja | Bajo | Template usado por ambos plugins piloto en H-04/H-05; doble validación | DX Lead |
| Coverage de plugins piloto baja | Media | Bajo | Cada plugin piloto incluye tests; usado como referencia para autores | Engineer |

---

## 6. Definición de Done (Global de Fase X)

- [ ] `prismal.langgraph` con 7 símbolos + `VERSION`.
- [ ] `@prismal_node` decorator + middleware chain (8 middlewares) + `list_registered_nodes()`.
- [ ] `PrismalStateGraphBuilder` con fluent API completa.
- [ ] `discover_plugins()` + CLI + entry points para 4 grupos.
- [ ] `LangChainRunnableAdapter` con soporte de 4 tipos comunes.
- [ ] 4 `Protocol`s (`CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`) + verificación de conformidad existente.
- [ ] 4 ejemplos ejecutables en `examples/`.
- [ ] `docs/extension.md` (quickstart + recetario + migration + ports).
- [ ] Plugin template generador (cookiecutter/copier) en `examples/plugin_template/`.
- [ ] 2 plugins piloto publicados en TestPyPI (`prismal-x-hello`, `prismal-x-financial-extra`).
- [ ] `uv run pytest -m "not live_api"` pasa al 100% (688 previos + ~140 nuevos = ~828+).
- [ ] Coverage ≥ 85% en `prismal/agents/extension/` y `prismal/langgraph.py`.
- [ ] `uv run ruff check .` sin errores.
- [ ] `uv run mypy prismal` sin errores en strict mode.
- [ ] `uv run bandit -r prismal -c pyproject.toml` sin HIGH/CRITICAL.
- [ ] Benchmark del decorator publicado: ≤ 5 ms p95 documentado.
- [ ] `CLAUDE.md`, `README.md`, `CHANGELOG.md` actualizados.
- [ ] PR mergeado a `main` con 1 reviewer aprobado.

---

## 7. Estimación de Esfuerzo por Sub-Fase

| Sub-Fase | Sub-tareas | Días | Semanas |
|---|---|---|---|
| X1 — Re-export | 5 | 1 | 0.2 |
| X2 — Decorator + middleware | 24 | 5 | 1 |
| X3 — Builder | 9 | 4 | 0.8 |
| X4 — Plugin discovery | 18 | 5 | 1 |
| X5 — LangChain adapter | 8 | 2.5 | 0.5 |
| X6 — Ports | 5 | 2 | 0.5 |
| X7 — Docs + examples + template | 13 | 5 | 1 |
| X8 — Settings + métricas | 5 | 1 | 0.2 |
| Hardening | 13 | 4 | 0.8 |
| **Total** | **~100** | **~30** | **~6** |

*Estimación basada en 1 engineer senior. Con 2 engineers: X1+X4+X6+X8 pueden ir en paralelo desde día 1.*

---

## 8. Métricas de Éxito Operacionales

Tras merge a `main`, monitorear primeras 4 semanas:

- `prismal_plugins_loaded_total{status="success"}` por deployment — confirma adopción.
- `prismal_plugins_loaded_total{status="error"}` — alerta si >0% por plugin.
- `prismal_custom_nodes_invocations_total` por nodo — visibilidad de uso de extensiones.
- `prismal_custom_nodes_latency_seconds` p95 — overhead aceptable.
- Tiempo de "hello world" (encuesta DX a primeros 5 plugin authors) — target ≤ 15 min.
- Issues en GitHub etiquetados `extension-api` — backlog de feedback.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Versión inicial — 100 sub-tareas en 9 fases, 6 semanas |
