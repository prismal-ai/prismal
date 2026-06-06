# Prismal Runtime Composition Root — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/composition-root/PLAN.md` |
| **Architecture** | `specs/composition-root/ARCHITECTURE.md` |
| **SPEC** | `specs/composition-root/SPEC.md` |
| **Depende de** | Fase Y (`specs/tool-provider-injection/`), Fase Z (`specs/vector-store-port/`) |

---

## 1. Resumen de Implementación

Fase R añade un *facade* de composición (`build_runtime`) que orquesta los puertos ya provistos por Y y Z más embeddings/checkpoint/audit, con loaders de config y resolución de tenant. **Aditivo y opt-in**: no cambia firmas de nodos ni los defaults; quien no lo use sigue con la inyección individual.

Principio rector: **orquestar, no reimplementar**. Los puntos de verdad son `build_default_tool_provider` (Y) y `VectorStoreFactory`/provider (Z).

---

## 2. Pre-requisitos

- Fase Y implementada: `ToolProviderPort`, `build_default_tool_provider`, `set_tool_provider`. (Estado en `specs/tool-provider-injection/TASKS.md`.)
- Fase Z implementada: `VectorStorePort`, `VectorStoreFactory`, `set_vector_store_provider`, `get_async_compiled_graph(vector_store_provider=...)`.
- Existentes: `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`, `get_settings()`.

> Si Y/Z aún no están al 100%, R se especifica ahora pero su implementación va detrás.

---

## 3. Fases de Implementación

### FASE R1 — `RuntimeConfig` / `RuntimeContext`
#### R1-01 — Tipos
- [ ] Crear `prismal/composition.py` con `RuntimeConfig` (frozen) y `RuntimeContext` (dataclass + `aclose` + async context manager).
- **Done:** `RuntimeContext` agrupa los 5 puertos + `org_id`; `aclose` idempotente.

### FASE R2 — `build_runtime`
#### R2-01 — Composición global
- [ ] Implementar `build_runtime(settings, *, org_id, overrides, mode)` reutilizando builders de Y/Z + EmbeddingsFactory + build_checkpointer + AuditLogger.
- [ ] Modo global: `set_tool_provider` + `set_vector_store_provider`.
- [ ] En fallo: `aclose()` de lo creado + `RuntimeCompositionError`.
#### R2-02 — Modo context
- [ ] Modo context: no toca globals; el contexto se pasa a `get_async_compiled_graph(...)`.
- [ ] `build_test_runtime(...)` con fakes.
- **Done:** `ctx = await build_runtime(settings)` retorna contexto con 5 puertos no nulos.

### FASE R3 — Config loaders
#### R3-01 — `config_sources.py`
- [ ] `load_mcp_config`, `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`, `collection_for`.
- **Done:** loaders puros (sync), sin conexión, testeados.

### FASE R4 — Resolución de tenant
#### R4-01 — collection_for en RAG y memoria
- [ ] `collection_for(base, org_id)` aplicado consistentemente al construir RAG (`RAGEngine`) y memoria (`LongTermMemory`) desde el runtime.
- **Done:** mismo tenant → misma colección en RAG y memoria; distinto tenant → distinta.

### FASE R5 — Settings (modo unificado)
#### R5-01 — `runtime_mode`
- [ ] `settings.runtime_mode: Literal["global","context"] = "global"`; `build_runtime` lo propaga a Y y Z.
- [ ] Retrocompat: derivar de `tool_provider_mode` si está seteado.

### FASE R6 — Ciclo de vida
#### R6-01 — aclose + context manager
- [ ] `aclose()` cierra MCP/vstore/checkpointer; `async with build_runtime(...)`.
- **Done:** test de teardown verifica las llamadas de cierre.

### FASE R7 — Excepción + integración grafo
#### R7-01 — `RuntimeCompositionError`
- [ ] En `core/exceptions.py`.
#### R7-02 — graph acepta providers del contexto
- [ ] Confirmar/ajustar `get_async_compiled_graph(tool_provider=, vector_store_provider=)` (consistencia con Z).

### FASE R8 — Tests + Docs + Ejemplo
#### R8-01 — Tests
- [ ] Composición (5 puertos), no-duplicación (usa builders Y/Z), tenant (collection_for), aislamiento context (`asyncio.gather`), lifecycle (aclose), backward-compat (sin build_runtime).
#### R8-02 — Docs + ejemplo
- [ ] `docs/composition-root.md` (lifespan server + contrato dashboard); `examples/composition_root.py`.

### HARDENING
- [ ] Coverage ≥ 85% en `composition*`; `ruff`/`mypy --strict`/`bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CLAUDE.md` + `README.md` + notas Obsidian actualizadas con el composition root.

---

## 4. Dependencias Inter-Tareas

```
(Fase Y + Fase Z implementadas)
   └─▶ R1 (tipos)
         └─▶ R2 (build_runtime)  ──┬─▶ R4 (tenant)   ──▶ R8 (tests)
                                   ├─▶ R5 (runtime_mode)
                                   └─▶ R6 (lifecycle)
   R3 (loaders) ──▶ R2 (build_runtime los usa)
   R7 (excepción + graph) ──▶ R2/R8
   R8 (docs/ejemplo) [último]
```

Ruta crítica: **Y+Z → R1 → R3 → R2 → R8**.

---

## 5. Matriz Tareas ↔ Requisitos

| Tarea | RF cubiertos |
|---|---|
| R1 | RF-CR-001 |
| R2 | RF-CR-002, RF-CR-003, RF-CR-004, RF-CR-009, RF-CR-010 |
| R3 | RF-CR-005 |
| R4 | RF-CR-006 |
| R5 | RF-CR-004, RF-CR-008 |
| R6 | RF-CR-007 |
| R7 | RF-CR-002 (errores), RF-CR-008 |
| R8 | RF-CR-009, RF-CR-011, RF-CR-012 |

Cobertura: RF-CR-001..012 mapeados.

---

## 6. Matriz de Riesgos

| Riesgo | Mitigación | Tarea |
|---|---|---|
| Duplicar lógica de Y/Z | Orquestar builders; test de no-duplicación | R2, R8 |
| Fuga entre tenants | Modo context sin globals; aislamiento por colección; test | R2, R4, R8 |
| Recursos colgados | aclose + context manager; test teardown | R6, R8 |
| Acople con server inexistente | Feature en core; server solo llama; contrato documentado | R8 |
| Y/Z incompletas | Especificar ahora, implementar detrás de Y/Z | Pre-requisitos |

---

## 7. Definición de Done (Global de Fase R)

- [ ] `RuntimeContext`/`RuntimeConfig`/`build_runtime` implementados; compone 5 puertos sin duplicar Y/Z.
- [ ] Modo global y context; `runtime_mode` en settings; `collection_for` por `org_id` en RAG+memoria.
- [ ] `aclose()` + async context manager; `RuntimeCompositionError`.
- [ ] `build_test_runtime` con fakes; backward-compat (inyección individual sigue válida).
- [ ] Contrato host (`prismal-server` lifespan) y dashboard documentados.
- [ ] `docs/composition-root.md` + `examples/composition_root.py`.
- [ ] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + notas Obsidian actualizadas.
- [ ] PR mergeado con review.

---

## 8. Estimación de Esfuerzo

| Sub-fase | Esfuerzo |
|---|---|
| R1 Tipos | 0.4 sem |
| R2 build_runtime | 0.6 sem |
| R3 Loaders | 0.5 sem |
| R4 Tenant | 0.3 sem |
| R5 Settings | 0.2 sem |
| R6 Lifecycle | 0.3 sem |
| R7 Excepción + grafo | 0.3 sem |
| R8 Tests + docs | 0.5 sem |
| Hardening | 0.4 sem |
| **Total** | **~3.5 sem** |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Plan de implementación inicial — composition root |
