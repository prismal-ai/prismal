# Prismal — Runtime Composition Root (inyección y configuración unificada del host)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Fase** | R — Runtime Composition Root (capstone de Fase Y + Fase Z) |
| **Depende de** | Fase Y (`specs/tool-provider-injection/`), Fase Z (`specs/vector-store-port/`) |

---

## 1. Resumen Ejecutivo

Las Fases Y y Z invierten dos dependencias del núcleo en puertos hexagonales: `ToolProviderPort` (MCP + Skills) y `VectorStorePort` (base vectorial intercambiable). Pero cada una expone su propio punto de inyección (`set_tool_provider()`, `set_vector_store_provider()`/`VectorStoreFactory`), su propia config y su propio ciclo de vida. Hoy **no existe un único punto donde el host (`prismal-server`) componga todo el runtime** a partir de `settings` + contexto de tenant. Sin ese punto, construir los componentes que faltan del ecosistema (`prismal-server`, `prismal-dashboard`) implica recablear a mano cada puerto en cada componente.

Esta fase define un **Runtime Composition Root** en el núcleo: `build_runtime(settings, *, org_id=None) -> RuntimeContext`, un único *facade* que **compone e inyecta** todos los puertos del core — tool provider (Y), vector store provider (Z), embeddings, checkpointer y audit — leyendo las fuentes de config existentes (`config/mcp_servers.yaml`, estado de skills, `vector_store_backend`) y aplicando resolución por tenant (`org_id`). Es el contrato que el `prismal-server` invoca en su *lifespan* y que el `prismal-dashboard` usa para leer/editar config.

El cambio es **aditivo y opt-in**: quien no use el composition root sigue con los puntos de inyección individuales de Y/Z o con los defaults. El `RuntimeContext` no reemplaza a los puertos — los **orquesta**. Resultado: `prismal-server` arranca con **una llamada**, el multi-tenant queda formalizado (aislamiento de colección por `org_id`), y el ecosistema tiene un único contrato de composición versionado.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- **Inyección fragmentada.** Tras Y/Z hay ≥ 5 piezas que el host debe cablear por separado: `set_tool_provider(build_default_tool_provider(settings))`, vector store (factory o `set_vector_store_provider`), embeddings (`EmbeddingsFactory`), checkpointer (`build_checkpointer`), audit (`AuditLogger`). No hay un ensamblado único.
- **Config dispersa.** MCP se lee de `config/mcp_servers.yaml`; skills de directorios; vector store de env/`settings`; cada subsistema con su loader. El host debe conocerlos todos.
- **Multi-tenant informal.** El roadmap pide aislamiento de colecciones Chroma por `org_id`, pero no hay un punto que derive el `collection_name` por tenant de forma consistente para RAG **y** memoria.
- **Componentes faltantes bloqueados.** `prismal-server` y `prismal-dashboard` están "Planned". Sin un contrato de composición, cada uno reinventaría el wiring → divergencia y bugs.
- **El ciclo de vida no tiene dueño único.** Conexión MCP, apertura del vector store, checkpointer: hoy se inicializan en momentos distintos sin un *teardown* coordinado.

### 2.2 Problema

1. **Sin composición unificada**, el host repite y puede equivocar el wiring de 5+ puertos.
2. **Sin loaders centralizados**, la config (yaml MCP, skills, backend vectorial, overrides por org) se interpreta de forma inconsistente entre server y dashboard.
3. **Sin resolución de tenant**, el aislamiento por `org_id` se implementa ad-hoc en cada call site.
4. **Sin ciclo de vida coordinado**, no hay `startup`/`shutdown` limpio (conexiones colgadas, recursos no liberados).

### 2.3 Oportunidad

Las piezas ya existen: `build_default_tool_provider` (Y), `VectorStoreFactory`/provider (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`, `get_settings()`. Falta **un ensamblador** que las una bajo un contrato (`RuntimeContext`) con loaders y resolución de tenant. Esfuerzo bajo (un módulo facade que orquesta lo ya existente), impacto alto: desbloquea `prismal-server`/`prismal-dashboard` y formaliza el multi-tenant.

---

## 3. Usuarios Objetivo

### Persona 1: `prismal-server` (Platform Host)
- **Necesidad:** Arrancar el runtime con una llamada en el *lifespan*; obtener el grafo listo y el contexto inyectado; cerrar limpio en *shutdown*.
- **Frecuencia:** 1 vez por arranque (modo global) o 1 por sesión/tenant (modo context).

### Persona 2: Multi-Tenant Operator
- **Necesidad:** Que cada `org_id` quede aislado (colección vectorial propia, providers acordes) sin estado compartido entre tenants.
- **Frecuencia:** Por request/tenant.

### Persona 3: `prismal-dashboard` (Config UI)
- **Necesidad:** Leer/editar la config que el composition root consume (MCP servers, skills activas, backend vectorial, settings) con un esquema estable.
- **Frecuencia:** Interacción de admin.

### Persona 4: Library User / Test Author
- **Necesidad:** Componer un runtime de prueba con fakes (`FakeToolProvider`, `FakeVectorStore`) en una llamada, sin levantar servicios.
- **Frecuencia:** Diaria.

---

## 4. Objetivos y Métricas de Éxito

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Arranque del host en 1 llamada | LoC de wiring en `prismal-server` lifespan | ≤ 5 | Fase R |
| Composición unificada | Puertos cableados por `build_runtime` | tools, vector store, embeddings, checkpoint, audit | Fase R |
| Multi-tenant formal | Aislamiento de colección por `org_id` para RAG **y** memoria | Sí | Fase R |
| Ciclo de vida coordinado | `RuntimeContext` con `aclose()`/teardown | Sí | Fase R |
| Backward compatibility | Inyección individual de Y/Z sigue válida | 100% | Global |
| Testabilidad | Runtime de test con fakes en 1 llamada | Sí | Fase R |
| Cobertura | Branch coverage del módulo nuevo | ≥ 85% | Global |

---

## 5. Alcance

### 5.1 In Scope (Fase R)

**R1 — `RuntimeConfig` + `RuntimeContext` (`prismal/composition.py`):**
- [ ] `RuntimeContext`: contenedor de los puertos compuestos (tool provider, vector store provider, embeddings, checkpointer, audit) + `org_id` + `aclose()`.
- [ ] `RuntimeConfig`: vista resuelta de config (paths, backend, mcp config path, skills source, overrides por org).

**R2 — `build_runtime` (composition root):**
- [ ] `async build_runtime(settings=None, *, org_id=None, overrides=None) -> RuntimeContext` que compone todos los puertos reusando `build_default_tool_provider` (Y), `VectorStoreFactory`/provider (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.
- [ ] Inyecta los providers globales (`set_tool_provider`, `set_vector_store_provider`) o devuelve un contexto bound por sesión (modo context).

**R3 — Config loaders centralizados (`prismal/composition/config_sources.py`):**
- [ ] `load_mcp_config(path)`, `resolve_skills_source(settings)`, `resolve_vector_store(settings, org_id)`, `apply_org_overrides(settings, org_id, overrides)`.

**R4 — Resolución de tenant:**
- [ ] Derivar `collection_name` por `org_id` para RAG y memoria de forma consistente (`f"{base}_{org_id}"`).
- [ ] Política de providers por tenant (tools/skills) opt-in.

**R5 — Modos global vs context:**
- [ ] `runtime_mode: Literal["global","context"]`: global = inyecta singletons; context = `RuntimeContext` por sesión sin estado global (alineado con Fase Y var. B y Fase Z var. B).

**R6 — Ciclo de vida:**
- [ ] `RuntimeContext.aclose()` cierra MCP, vector store, checkpointer; `build_runtime` usable como async context manager.

**R7 — Contrato del host y del dashboard:**
- [ ] Documentar el *lifespan* de `prismal-server` y el esquema de config que el `prismal-dashboard` lee/edita.

**R8 — Tests + ejemplo:**
- [ ] `build_test_runtime(...)` con fakes; ejemplo `examples/composition_root.py`; docs `docs/composition-root.md`.

### 5.2 Out of Scope

- Implementar `prismal-server` / `prismal-dashboard` en sí (viven en otros paquetes; aquí solo el contrato que consumen).
- Reescribir Y/Z (se reutilizan; este feature los orquesta).
- Per-tenant con **backend vectorial distinto por org** (caro; se mantiene backend por proceso, aislamiento por colección).
- Gestión de secretos/credenciales del host (responsabilidad del server).

### 5.3 Futuras Consideraciones

- Pool de runtimes por tenant con *eviction* (si se requiere backend por-org).
- Hot-reload de config (MCP/skills) sin reinicio.
- `prismal.composition` como entry point para que plugins aporten sub-providers.

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-CR-001 | `RuntimeContext` agrupa tool provider, vector store provider, embeddings, checkpointer, audit | `MUST` |
| RF-CR-002 | `build_runtime(settings, *, org_id=None)` compone todos los puertos en una llamada | `MUST` |
| RF-CR-003 | Reutiliza `build_default_tool_provider` (Y) y `VectorStoreFactory`/provider (Z), sin duplicar lógica | `MUST` |
| RF-CR-004 | Modo global (inyecta singletons) y modo context (contexto por sesión) | `MUST` |
| RF-CR-005 | Loaders centralizados de MCP yaml, skills, vector store, overrides por org | `MUST` |
| RF-CR-006 | Resolución de `collection_name` por `org_id` para RAG y memoria | `MUST` |
| RF-CR-007 | `RuntimeContext.aclose()` libera recursos (MCP, vector store, checkpointer) | `MUST` |
| RF-CR-008 | `runtime_mode` + `org_id` en settings/parámetros | `SHOULD` |
| RF-CR-009 | `build_test_runtime` con fakes para tests | `SHOULD` |
| RF-CR-010 | Backward-compat: la inyección individual de Y/Z sigue funcionando | `MUST` |
| RF-CR-011 | Contrato documentado para `prismal-server` (lifespan) y `prismal-dashboard` (config) | `SHOULD` |
| RF-CR-012 | Observabilidad: span/log de composición con backend/providers/org | `SHOULD` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- `build_runtime` global: 1 vez por arranque (coste de conexión MCP + apertura store).
- Modo context: reutilizar recursos compartibles (embeddings, checkpointer) entre tenants; solo el `collection_name` cambia.

### Seguridad
- Aislamiento estricto entre tenants: ningún `RuntimeContext` de un `org_id` expone datos de otro (colección separada; sin estado global compartido en modo context).
- Credenciales de backends servidor (Qdrant/pg) y MCP no se loguean.
- Las capas L1–L5 siguen aplicándose downstream (el composition root solo compone, no ejecuta tools).

### Compatibilidad
- `prismal/` namespace PEP 420. Aditivo: sin tocar la firma de los nodos ni de los patrones RAG.
- `filterwarnings=error`: imports de subsistemas opcionales diferidos.

### Mantenibilidad
- Coverage ≥ 85%; `ruff`/`mypy --strict`/`bandit` clean.
- `RuntimeContext`/`build_runtime` son API pública versionada (SemVer).

---

## 8. Restricciones y Dependencias

| Dependencia | Tipo | Uso |
|---|---|---|
| Fase Y — `build_default_tool_provider`, `set_tool_provider` | Pre-requisito | Sub-composición de tools |
| Fase Z — `VectorStoreFactory`, `set_vector_store_provider` | Pre-requisito | Sub-composición de vector store |
| `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger` | Existente | Sub-composición |
| `config/mcp_servers.yaml`, dirs de skills, `settings` | Existente | Fuentes de config |

Restricción de orden: **Fase R requiere Y y Z** (al menos sus puertos y factories). Puede especificarse en paralelo, pero la implementación va después.

---

## 9. User Stories

**US-CR-001:** Como `prismal-server`, arranco el runtime en el lifespan con una llamada.
```python
async def lifespan(app):
    ctx = await build_runtime(get_settings())     # compone e inyecta todo
    app.state.graph = await get_async_compiled_graph()
    yield
    await ctx.aclose()
```

**US-CR-002:** Como Multi-Tenant Operator, cada tenant queda aislado.
```python
ctx = await build_runtime(settings, org_id="acme")   # collection = "<base>_acme"
```

**US-CR-003:** Como dashboard, leo/edito la config que el runtime consume.
- [ ] Esquema estable de MCP servers / skills / vector_store_backend / settings.

**US-CR-004:** Como Test Author, compongo un runtime con fakes.
```python
ctx = build_test_runtime(tool_provider=FakeToolProvider({...}),
                         vector_store=FakeVectorStore({...}))
```

---

## 10. Riesgos y Mitigaciones

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| El composition root duplica lógica de Y/Z | Media | Medio | Estrictamente orquesta; reutiliza `build_default_*`/factories; test de no-duplicación |
| Fuga entre tenants | Baja | Crítico | Modo context sin estado global; aislamiento por colección; test de aislamiento |
| Recursos no liberados | Media | Medio | `aclose()` + context manager; test de teardown |
| Acople con `prismal-server` inexistente | Media | Bajo | El feature vive en el core; el server solo lo llama. Contrato documentado |
| Backend por-tenant tienta a sobre-ingeniería | Media | Medio | Out of scope explícito; aislamiento por colección cubre el roadmap |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| R1 — Context/Config | 0.4 sem | `RuntimeContext`, `RuntimeConfig` |
| R2 — build_runtime | 0.6 sem | composición global + context |
| R3 — Loaders | 0.5 sem | config_sources centralizados |
| R4 — Tenant resolution | 0.3 sem | collection_name por org (RAG+memoria) |
| R5 — Modos | 0.2 sem | global/context |
| R6 — Lifecycle | 0.3 sem | aclose + context manager |
| R7 — Contratos host/dashboard | 0.3 sem | docs de contrato |
| R8 — Tests + ejemplo | 0.5 sem | fakes + ejemplo + docs |
| Hardening | 0.4 sem | coverage, mypy/bandit, validación |
| **Total** | **~3.5 sem** | composition root listo para `prismal-server`/`dashboard` |

---

## 12. Definición de Done (Global de Fase R)

- [ ] `RuntimeContext` + `RuntimeConfig` + `build_runtime` implementados.
- [ ] Compone tools (Y), vector store (Z), embeddings, checkpointer, audit — sin duplicar lógica.
- [ ] Modo global y context; resolución de `collection_name` por `org_id` (RAG + memoria).
- [ ] `aclose()` libera recursos; usable como async context manager.
- [ ] `build_test_runtime` con fakes; inyección individual de Y/Z sigue válida.
- [ ] Contrato documentado para `prismal-server` (lifespan) y `prismal-dashboard` (config).
- [ ] `docs/composition-root.md` + `examples/composition_root.py`.
- [ ] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + notas Obsidian actualizadas.
- [ ] PR mergeado con review.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Versión inicial — composition root que unifica Fase Y + Z |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
| Security Lead | — | | ☐ Pendiente |
