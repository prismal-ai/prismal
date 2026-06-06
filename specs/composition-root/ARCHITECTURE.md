# Prismal Runtime Composition Root — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN Relacionado** | `specs/composition-root/PLAN.md` |
| **SPEC Relacionado** | `specs/composition-root/SPEC.md` |
| **TASKS** | `specs/composition-root/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Contexto

Tras Fase Y (`ToolProviderPort` para MCP+Skills) y Fase Z (`VectorStorePort` para base vectorial intercambiable), el núcleo expone varios puntos de inyección independientes. El host que falta (`prismal-server`, FastAPI, multi-tenant por `org_id`) necesitaría cablearlos uno a uno. Este documento describe la **Fase R — Runtime Composition Root**: un *facade* de composición (`build_runtime`) que orquesta todos los puertos del core bajo un contrato único (`RuntimeContext`), con loaders de config y resolución de tenant. Es coherente con la familia de puertos de Fase X/Y/Z y con el modelo de capas del ecosistema (core → server → sdk → dashboard) documentado en las notas de Obsidian.

---

## 2. Objetivos Técnicos

- **OT-1:** Un único `build_runtime(settings, *, org_id=None)` que compone tools (Y), vector store (Z), embeddings, checkpoint y audit.
- **OT-2:** No duplicar lógica: orquestar `build_default_tool_provider`, `VectorStoreFactory`/provider, `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.
- **OT-3:** Formalizar multi-tenant por aislamiento de **colección** (`org_id`), no por backend.
- **OT-4:** Ciclo de vida coordinado (`aclose()` / async context manager).
- **OT-5:** Dos modos: global (inyecta singletons) y context (contexto por sesión sin estado global).
- **OT-6:** Mantener backward-compat: la inyección individual de Y/Z sigue válida.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel

```
                        prismal-server (FastAPI, lifespan)         ← HOST
                                   │  await build_runtime(settings, org_id=?)
                                   ▼
        ┌──────────────────────  prismal/composition.py  ──────────────────────┐
        │  build_runtime():                                                     │
        │    1. RuntimeConfig = resolve(settings, org_id, overrides)            │
        │    2. tool_provider   = build_default_tool_provider(settings)   [Y]   │
        │    3. vstore_provider = VectorStoreFactory / provider(settings) [Z]   │
        │    4. embeddings      = EmbeddingsFactory.create(settings)            │
        │    5. checkpointer    = build_checkpointer(settings)                  │
        │    6. audit           = AuditLogger(...)                              │
        │    7. (modo global) set_tool_provider(...) ; set_vector_store_provider│
        │       (modo context) deja todo dentro del RuntimeContext             │
        │    → RuntimeContext(tool_provider, vstore_provider, embeddings,       │
        │                     checkpointer, audit, org_id, aclose())           │
        └───────────────────────────────────────────────────────────────────────┘
                                   │ consume
                  prismal core (agents, rag, memory)  ← solo usa los puertos
```

### 3.2 Diagrama de Capas (ecosistema)

```
┌───────────────────────────────────────────────────────────┐
│ prismal-dashboard (Reflex)  → EDITA config (MCP/skills/    │
│                               settings/backend vectorial)  │
└───────────────┬───────────────────────────────────────────┘
                │ persiste config
┌───────────────▼───────────────────────────────────────────┐
│ prismal-server (FastAPI)  → COMPONE: build_runtime(...)    │
│   lifespan startup → build_runtime ; shutdown → aclose()   │
│   multi-tenant: build_runtime(org_id=...)                  │
└───────────────┬───────────────────────────────────────────┘
                │ build_runtime
┌───────────────▼───────────────────────────────────────────┐
│ prismal core                                              │
│   composition.py (R) ── orquesta ──► Y (tools) · Z (vstore)│
│                                       · embeddings · ckpt  │
│                                       · audit              │
│   puertos en extension/ports.py ; consumidores en agents/  │
│   rag/ memory/ (sin cambios de firma)                     │
└───────────────────────────────────────────────────────────┘
        prismal-sdk = CLIENTE de la API (no compone, no inyecta)
```

### 3.3 Componentes

#### R1 — `RuntimeContext` / `RuntimeConfig` (`prismal/composition.py`)
- `RuntimeConfig`: vista inmutable resuelta — `mcp_config_path`, `skills_source`, `vector_store_backend`, `collection_base`, `org_id`, overrides aplicados.
- `RuntimeContext`: dataclass con `tool_provider: ToolProviderPort`, `vector_store_provider`, `embeddings: EmbeddingsPort`, `checkpointer: CheckpointPort`, `audit: AuditPort`, `org_id: str | None`, y `async aclose()`.

#### R2 — `build_runtime` (composition root)
`async def build_runtime(settings=None, *, org_id=None, overrides=None, mode=None) -> RuntimeContext`. Resuelve config (R3), compone sub-puertos reusando los builders de Y/Z y los factories existentes, y según `mode`:
- **global:** `set_tool_provider(tp)` + `set_vector_store_provider(vsp)` (singletons del proceso).
- **context:** no toca globals; el `RuntimeContext` se pasa a `get_async_compiled_graph(tool_provider=..., vector_store_provider=...)` (bound por sesión).

#### R3 — Config loaders (`prismal/composition/config_sources.py`)
- `load_mcp_config(path) -> McpConfig`: parsea `config/mcp_servers.yaml`.
- `resolve_skills_source(settings)`: directorios/estado de skills activas.
- `resolve_vector_store(settings, org_id)`: backend + `collection_name` derivado.
- `apply_org_overrides(settings, org_id, overrides) -> Settings`: settings efectivos por tenant.

#### R4 — Resolución de tenant
`collection_for(base, org_id) -> str` = `base` si `org_id is None`, si no `f"{base}_{org_id}"`. Se aplica **igual** en RAG (`RAGEngine`) y memoria (`LongTermMemory`) para que un tenant vea su colección en ambos.

#### R5 — Modos
`settings.runtime_mode: Literal["global","context"] = "global"`. Espejo de los modos de Fase Y/Z; el composition root los unifica en un único parámetro.

#### R6 — Ciclo de vida
`RuntimeContext.aclose()` cierra MCP (desconecta servers), vector store (cierra conexiones servidor si aplica) y checkpointer. `build_runtime` también es usable como `async with`.

### 3.4 Flujos de Datos

#### Flujo R-A: Arranque global (prismal-server lifespan)
```
1. startup → ctx = await build_runtime(get_settings())     # mode=global
2. build_runtime compone Y+Z+emb+ckpt+audit
3. set_tool_provider / set_vector_store_provider (singletons)
4. get_async_compiled_graph() usa los providers inyectados
5. shutdown → await ctx.aclose()
```

#### Flujo R-B: Per-tenant (context)
```
1. request org=acme → ctx = await build_runtime(settings, org_id="acme")   # mode=context
2. resolve_vector_store → collection_name = "<base>_acme"
3. graph = await get_async_compiled_graph(tool_provider=ctx.tool_provider,
                                          vector_store_provider=ctx.vector_store_provider)
4. ejecución aislada; otro tenant en paralelo no comparte estado
5. fin de sesión → await ctx.aclose()  (o reutilizar recursos compartibles)
```

---

## 4. Decisiones de Diseño

### DD-CR-001: Orquestar, no reimplementar
`build_runtime` **llama** a `build_default_tool_provider` (Y) y a `VectorStoreFactory`/provider (Z); no reproduce su lógica. Un test verifica que no hay duplicación (los sub-builders son los puntos de verdad).

### DD-CR-002: `RuntimeContext` como contenedor, no como God-object
El contexto solo **agrupa referencias** a puertos ya compuestos + `aclose()`. No añade comportamiento de negocio; los patrones RAG/agentes siguen consumiendo los puertos directamente.

### DD-CR-003: Multi-tenant por colección, backend por proceso
Aislamiento de datos por `org_id` = `collection_name` derivado (barato, ya soportado por los constructores). El backend vectorial se fija por proceso (Fase Z). Backend-por-tenant queda fuera de alcance (recurso con estado, memoria singleton).

### DD-CR-004: Dos modos heredados de Y/Z, unificados
En vez de exponer `tool_provider_mode` y un futuro `vector_store_mode` por separado, el composition root expone **un** `runtime_mode` que propaga a ambos. Menos superficie de config.

### DD-CR-005: El feature vive en el core, el server solo lo llama
`prismal/composition.py` es del núcleo (publicable); `prismal-server` aporta el *lifespan* y la config persistida. Así el contrato existe antes que el server (que está "Planned").

### DD-CR-006: Backward-compat total
Quien ya usa `set_tool_provider`/`VectorStoreFactory` directamente sigue igual. `build_runtime` es **opt-in**; no cambia defaults ni firmas de nodos/patrones.

### DD-CR-007: Ciclo de vida explícito
`aclose()` evita conexiones colgadas (MCP, Qdrant/pg). Async context manager para uso ergonómico en tests y scripts.

---

## 5. Estructura del Código

```
prismal/
├── composition.py                 # NUEVO: build_runtime, RuntimeContext, RuntimeConfig
├── composition/                   # (si crece) submódulo
│   └── config_sources.py          # NUEVO: loaders MCP/skills/vstore/overrides
├── agents/
│   ├── extension/providers.py     # Y: build_default_tool_provider (reusado)
│   └── graph.py                   # acepta vector_store_provider en context (Z)
├── rag/
│   ├── vector_store_factory.py    # Z: VectorStoreFactory (reusado)
│   └── ...
├── core/
│   ├── config.py                  # + runtime_mode (unifica modos)
│   └── exceptions.py              # + RuntimeCompositionError
docs/composition-root.md           # NUEVO
examples/composition_root.py       # NUEVO
tests/unit/composition/            # tests del composition root
```

### Patrones Aplicados
- **Composition Root** (patrón DI clásico: un único lugar ensambla el grafo de objetos).
- **Facade** (`build_runtime` sobre builders existentes).
- **Hexagonal** (orquesta puertos; no conoce implementaciones concretas más allá de los builders).

### Manejo de Errores
- Falla de un sub-builder → `RuntimeCompositionError` con la causa (qué puerto falló), tras intentar `aclose()` de lo ya creado (no dejar recursos colgando).
- Config inválida (yaml MCP malo, backend desconocido) → error claro del loader correspondiente (reusa los de Y/Z).

---

## 6. Seguridad

- **Aislamiento entre tenants:** en modo context, dos `RuntimeContext` no comparten estado; las colecciones vectoriales están separadas por `org_id`. Test de aislamiento obligatorio.
- **Credenciales:** DSN/keys de backends servidor y MCP no se loguean; `RuntimeConfig` marca campos sensibles como secretos.
- **Las barreras L1–L5 no se mueven:** el composition root compone; la ejecución de tools sigue pasando por `react_loop` + middleware de `@prismal_node`.

---

## 7. Observabilidad

- Span `prismal.composition.build_runtime` con `mode`, `org_id`, `vector_store_backend`, `n_mcp_servers`, `n_skills`.
- Log `composition.runtime_built` (paridad con `mcp_initialized`/`vector_store.created`).
- Métrica `prismal_runtime_built_total{mode}`, `prismal_runtime_active{}` (gauge), `prismal_runtime_teardown_total`.

---

## 8. Testing Strategy

- **Composición:** `build_runtime` produce un `RuntimeContext` con los 5 puertos no nulos; cada sub-puerto es el que producen los builders de Y/Z (no una reimplementación).
- **Tenant:** `collection_for(base, org)` correcto; RAG y memoria del mismo tenant ven la misma colección; tenants distintos, distinta.
- **Aislamiento (context):** dos runtimes en paralelo (`asyncio.gather`) no comparten providers ni colección.
- **Lifecycle:** `aclose()` cierra MCP/vstore/checkpointer (mocks que verifican la llamada); async context manager.
- **Backward-compat:** usar `set_tool_provider`/`VectorStoreFactory` sin `build_runtime` sigue funcionando.
- **Fakes:** `build_test_runtime` arma un contexto con `FakeToolProvider`/`FakeVectorStore`.

---

## 9. Plan de Rollout

1. R1–R2 (context + build_runtime global) — aditivo; el server puede empezar a usarlo.
2. R3–R4 (loaders + tenant) — formaliza config y multi-tenant.
3. R5–R6 (modos + lifecycle).
4. R7–R8 (contratos + tests + docs).

Backout: `build_runtime` es opt-in; quitar su uso vuelve a la inyección individual de Y/Z.

---

## 10. Preguntas Abiertas

- **PA-1:** ¿`embeddings`/`checkpointer` se comparten entre tenants en modo context (recomendado) o se aíslan? (Propuesta: compartir; solo la colección cambia.)
- **PA-2:** ¿`RuntimeContext` debe exponer también el grafo compilado, o el server lo pide aparte? (Propuesta: aparte; el contexto agrupa puertos, no el grafo.)
- **PA-3:** ¿`config_sources` debe soportar overrides por org desde DB (server) o solo desde settings/env? (Propuesta: aceptar `overrides` dict; el server decide la fuente.)
- **PA-4:** ¿Pool de runtimes por tenant para reuso? (Futuro; depende de carga real.)

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Diseño técnico inicial — composition root |
