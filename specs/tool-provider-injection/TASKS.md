# Prismal Tool Provider Injection — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/tool-provider-injection/PLAN.md` |
| **Architecture** | `specs/tool-provider-injection/ARCHITECTURE.md` |
| **SPEC** | `specs/tool-provider-injection/SPEC.md` |

---

## 1. Resumen de Implementación

La Fase Y invierte la dependencia entre la capa de agentes y los subsistemas MCP/Skills introduciendo un `ToolProviderPort` y proveedores concretos que el host (`prismal-sdk`/`prismal-web`) compone e inyecta. El trabajo se concentra en:

- **Aditivo:** `ports.py` (+1 Protocol), `providers.py` (nuevo), settings, exceptions, docs, ejemplos, tests.
- **Refactor de bajo riesgo:** `tool_registry.py` (un archivo) pasa de importar MCP/Skills a delegar en el proveedor.
- **Opt-in:** variante B (multi-tenant) toca `graph.py` solo si se activa `tool_provider_mode="context"`.

Principio rector: **paridad de comportamiento**. Con el composite por defecto, `get_tools_for_agent` debe producir resultados idénticos a hoy (orden, dedupe, caps, fixed-tool agents). Verificado por test de paridad.

---

## 2. Pre-requisitos

- Fase X (Extension Surface) implementada: `prismal/agents/extension/ports.py` con `ToolPort` y `conforms_to`. ✅ Presente en repo.
- Acceso a `MCPClientManager.get_all_langchain_tools(capabilities=...)` y `get_server_status()`. ✅ Presente.
- Acceso a `SkillsManager().get_active_tools()`. ✅ Presente.
- `tool_registry.py` actual como referencia de paridad (stub_map, `_MAX_MCP_TOOLS`, `_MAX_TOTAL_TOOLS`, `_FIXED_TOOL_AGENTS`). ✅ Presente.

---

## 3. Fases de Implementación

### FASE Y1 — `ToolProviderPort`

#### Y1-01 — Declarar el puerto
- [x] Añadir `ToolProviderPort` (`@runtime_checkable Protocol`) a `prismal/agents/extension/ports.py` con `get_tools(*, agent_name, capabilities=None)`.
- [x] Añadir a `__all__` de `ports.py`.
- **Done:** `conforms_to(obj, ToolProviderPort)` distingue objetos con/ sin `get_tools`. ✅ (`tests/unit/agents/extension/test_tool_provider_port.py`)

#### Y1-02 — Re-export
- [x] Re-exportar `ToolProviderPort` desde `prismal/agents/extension/__init__.py`.
- **Done:** `from prismal.agents.extension import ToolProviderPort` funciona. ✅

---

### FASE Y2 — Proveedores concretos

#### Y2-01 — `StubToolProvider`
- [x] Crear `prismal/agents/extension/providers.py`.
- [x] Implementar `StubToolProvider` migrando el `stub_map` de `get_tools_for_agent` (imports de `tools.py`, `SANDBOX_TOOLS`, `ML_PIPELINE_TOOLS` diferidos).
- **Done:** devuelve el set correcto por agente; agentes desconocidos → `[]`. ✅ (`tests/unit/agents/extension/test_providers.py`)

#### Y2-02 — `McpToolProvider`
- [x] Implementar wrapper de `MCPClientManager` con cap `max_tools=60`; import diferido; captura de excepciones → `[]`.
- **Done:** paridad con `get_mcp_tools(capabilities=...)[:60]`. ✅

#### Y2-03 — `SkillToolProvider`
- [x] Implementar wrapper de `SkillsManager.get_active_tools()`; manager perezoso; captura → `[]`.
- **Done:** paridad con `get_skill_tools()`. ✅

#### Y2-04 — `CompositeToolProvider`
- [x] Implementar la estrategia de merge completa (fixed-tool agents → solo stubs; live = MCP+Skills; filtrar stubs por nombre; truncar a `max_total=120`; log `tool_provider.tools_resolved`).
- [x] Captura por sub-proveedor (`tool_provider.subprovider_error`).
- **Done:** test de paridad contra implementación actual pasa. ✅ (paridad completa byte-a-byte en Y8-02)

#### Y2-05 — `FakeToolProvider`
- [x] Implementar proveedor determinista para tests (`mapping` + `default`).
- **Done:** sin I/O, sin imports pesados. ✅

#### Y2-06 — `build_default_tool_provider`
- [x] Implementar el ensamblado async estándar (MCP opcional con `load_from_config`, Skills, Stubs → Composite).
- [x] Loguear sub-proveedores activos (paridad con `mcp_initialized`).
- **Done:** `provider = await build_default_tool_provider(settings)` retorna un `CompositeToolProvider` válido. ✅

#### Y2-07 — Re-exports de providers
- [x] Re-exportar las 5 clases + `build_default_tool_provider` desde `extension/__init__.py`. ✅

---

### FASE Y3 — Inyección global (variante A) + refactor del registry

#### Y3-01 — Estado y setters
- [x] Reemplazar `_mcp_manager`/`_mcp_initialized`/`_mcp_lock` por `_provider: ToolProviderPort | None`.
- [x] Implementar `set_tool_provider()` / `get_tool_provider()`.
- [x] Añadir fallback de stubs (`_get_default_stub_provider()` — singleton perezoso para no importar `extension/` en import-time del registry).

#### Y3-02 — Delegación en `get_tools_for_agent`
- [x] Reescribir el cuerpo: delegar en `_provider`; fallback a stubs + warning `tool_registry.no_provider`; `raise ToolProviderNotConfigured` si `tool_provider_strict`.
- [x] **Mantener la firma intacta** (`agent_name`, `required_capabilities`).
- **Done:** los 20+ nodos siguen funcionando sin tocarlos. ✅ (test de firma + 1519 tests agents/mcp/core verdes)

#### Y3-03 — Eliminar imports de mcp/skills
- [x] Quitar `from prismal.mcp.client import MCPClientManager` y `from prismal.skills.manager import SkillsManager` de `tool_registry.py`.
- **Done:** `grep` no encuentra `prismal.mcp`/`prismal.skills` en `tool_registry.py`. ✅

#### Y3-04 — Shims deprecados
- [x] `init_mcp`, `get_mcp_tools`, `get_skill_tools` emiten `DeprecationWarning` y delegan en proveedores (`init_mcp` inyecta el composite default si no hay proveedor; los getters resuelven el sub-proveedor Mcp/Skill dentro del composite).
- [x] Los tests del shim usan `pytest.warns(DeprecationWarning)` (no hizo falta tocar filterwarnings).
- **Done:** llamadas viejas siguen funcionando con warning. ✅

#### Y3-05 — Test de arquitectura
- [x] `tests/unit/agents/extension/test_no_mcp_skills_imports.py`: AST-walk de `prismal/agents/**` (excluyendo `extension/providers.py` y `skill_manager.py` — ver nota) → falla si importa `prismal.mcp`/`prismal.skills`.
- **Done:** test verde tras el refactor. ✅
- **Nota:** `skill_manager.py` (agente de administración de skills) importa `prismal.skills` por diseño — administrar el subsistema ES su función; está fuera del alcance de Fase Y ("solo se envuelven, no se reescriben") y queda eximido explícitamente con justificación en el test.

---

### FASE Y4 — Inyección por contexto (variante B, opt-in)

#### Y4-01 — Config del grafo
- [x] `get_async_compiled_graph(..., tool_provider=None)`: si `mode=="context"`, devuelve el grafo singleton **bound** vía `with_config({"configurable": {"tool_provider": ...}})` (vista por sesión; el grafo y el checkpointer siguen siendo singleton). En modo `global` el parámetro se ignora con warning `tool_provider_ignored_global_mode`.

#### Y4-02 — Resolución por nodo
- [x] `resolve_provider(config)` lee del config o cae al global (vive en `tool_registry.py` para evitar ciclo de imports; re-exportado desde `graph.py` como dicta la SPEC).
- [x] Variante de acceso para nodos en modo context (helper `get_tools_for_agent_ctx(agent_name, config, required_capabilities)`).

#### Y4-03 — Test de aislamiento
- [x] Dos proveedores en paralelo no comparten tools; sin estado global compartido.
- **Done:** test de aislamiento verde. ✅ (`tests/unit/agents/extension/test_context_provider.py` — 14 tests: resolve, ctx helper, aislamiento con `asyncio.gather`, bindings independientes por sesión)

---

### FASE Y5 — Settings + Exceptions

#### Y5-01 — Settings
- [x] `tool_provider_mode: Literal["global","context"] = "global"`.
- [x] `tool_provider_strict: bool = False`.

#### Y5-02 — Exception
- [x] `ToolProviderNotConfigured` en `core/exceptions.py` (hereda de `ExtensionError(PrismalError)` — familia Fase X/Y; nombre sin sufijo `Error` mandado por SPEC-TPI-010, con `noqa: N818`).

---

### FASE Y6 — Observabilidad

#### Y6-01 — Métricas
- [x] `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total`, `prismal_tool_provider_subprovider_errors_total{provider}` — registradas en `OTelManager._register_standard_metrics` (convención OTel del repo: `prismal.<nombre>_total`).

#### Y6-02 — Spans + logs
- [x] Span `prismal.tools.resolve` con `prismal.agent`, `prismal.tool_provider` (label `composite|mcp|skill|stub|fake`), `prismal.n_tools`, `prismal.fallback` y `prismal.capabilities` — emitido por `_observed_get_tools()` en `tool_registry` (cubre variante A, fallback y la rama ctx de variante B).
- [x] Log `tool_provider.tools_resolved` con paridad de campos (`agent`, `live`, `stubs_kept`, `total`) — verificado con `structlog.testing.capture_logs`.
- **Done:** ✅ (`tests/unit/agents/extension/test_observability.py` — 8 tests)

---

### FASE Y7 — Docs + Ejemplos

#### Y7-01 — Documentación
- [x] `docs/tool-providers.md`: quickstart de composición, variante A vs B (tabla comparativa), proveedor custom, mock en tests (`FakeToolProvider` + fixture de reset), tabla de paridad, shims deprecados y observabilidad. Enlazado desde `docs/extension.md` (tabla de ports) y `examples/README.md`.

#### Y7-02 — Ejemplos ejecutables
- [x] `examples/tool_provider_host.py` — composición tipo host + `set_tool_provider` (variante A) + toolsets por sesión (variante B); MCP opcional vía `EXAMPLE_USE_MCP=1` para que corra offline por defecto. ✅ ejecutado
- [x] `examples/tool_provider_custom.py` — proveedor propio que conforma `ToolProviderPort` estructuralmente (+ `conforms_to`). ✅ ejecutado

---

### FASE Y8 — Tests + Paridad

#### Y8-01 — Unit de proveedores
- [x] `test_providers.py`: cada proveedor en aislamiento con managers mock (34 tests, `providers.py` al 100% de coverage).

#### Y8-02 — Paridad del registry
- [x] `test_registry_delegation.py`: salida de `get_tools_for_agent` con composite por defecto == golden list derivada de la implementación actual (orden, dedupe, caps, fixed agents) — `TestParityWithDefaultComposite` + `TestPolicyConstantsParity`.

#### Y8-03 — Puerto
- [x] `test_tool_provider_port.py` + `test_providers.py::TestConformanceAndReExports`: conformidad estructural de los 5 proveedores.

#### Y8-04 — Fallback / strict
- [x] Sin proveedor → stubs + warning; `strict=True` → `ToolProviderNotConfigured` (`test_registry_delegation.py::TestFallback`).

---

### HARDENING — Coverage, Migración del host, Audit

- [x] Coverage ≥ 85% en `providers.py` y las rutas nuevas de `tool_registry.py` — `providers.py` **100%**, `tool_registry.py` **85%** (las líneas restantes son ramas edge del `react_loop` preexistente; todo el código de Fase Y cubierto).
- [ ] Migrar el arranque de `prismal-sdk`/`prismal-web` de `init_mcp()` a `build_default_tool_provider + set_tool_provider` (coordinado fuera de este repo; receta documentada en `docs/tool-providers.md` §1 y §5 — shims con `DeprecationWarning` mantienen el arranque viejo 1 minor).
- [x] `ruff` + `mypy --strict` (239 archivos) + `bandit` (0 Medium/High por severidad, paridad con baseline) clean en `prismal/` y `tests/`. (Quedan 20 issues ruff preexistentes en `examples/{multimodal,rag,subgraphs}` — anteriores a Fase Y, fuera de scope.)
- [x] `uv run pytest -m "not live_api"` — **2604 passed**; los ~50 fallos restantes son preexistentes/flaky (memory/mongodb, rag/crag, scheduler, security), verificado vía `git stash` que fallan idéntico sin los cambios de Fase Y. Cero regresiones introducidas.
- [x] Actualizar `CLAUDE.md` (sección "Tool provider injection (Fase Y)" + regla crítica 9) y `README.md` (feature bullet, sección Fase Y, tabla de ports, árbol de arquitectura).

---

## 4. Dependencias Inter-Tareas

```
Y1 (puerto)
  └─▶ Y2 (proveedores)
        ├─▶ Y3 (variante A + refactor registry)  ──▶ Y3-05 (test arquitectura)
        │      └─▶ Y8-02 (paridad)
        ├─▶ Y4 (variante B)  [requiere Y5-01 settings]
        └─▶ Y2-06 build_default  ──▶ HARDENING (migración host)
Y5 (settings + exception) ──▶ Y3-02 (strict), Y4-01 (mode)
Y6 (observabilidad)  [tras Y3]
Y7 (docs/ejemplos)   [tras Y2, Y3]
Y8 (tests)           [transversal; paridad tras Y3]
```

Ruta crítica: **Y1 → Y2 → Y3 → Y8-02 (paridad)**. La variante B (Y4) y la observabilidad (Y6) son paralelizables tras Y3.

---

## 5. Matriz de Riesgos y Mitigaciones

| Riesgo | Mitigación | Tarea |
|---|---|---|
| Pérdida de paridad en el merge | Golden test byte-a-byte | Y8-02 |
| Import residual de mcp/skills en el núcleo | Test de arquitectura AST | Y3-05 |
| Regresión por falta de proveedor | Fallback a stubs + warning; strict opt-in | Y3-02, Y5 |
| `DeprecationWarning` rompe `filterwarnings=error` | Ignorar solo el warning propio en los tests del shim | Y3-04 |
| Fuga multi-tenant | Sin estado global; test de aislamiento | Y4-03 |
| Host no migra arranque | Shims deprecados 1 minor | Y3-04, HARDENING |

---

## 6. Definición de Done (Global de Fase Y)

- [x] `ToolProviderPort` declarado, re-exportado, con conformidad de los 5 proveedores.
- [x] `providers.py` completo (Mcp/Skill/Stub/Composite/Fake + `build_default_tool_provider`).
- [x] `tool_registry.get_tools_for_agent` delega; firma intacta; sin imports mcp/skills.
- [x] Test de arquitectura (sin imports prohibidos) verde.
- [x] Test de paridad verde (salida idéntica con composite por defecto).
- [x] `_FIXED_TOOL_AGENTS` y caps de tokens preservados.
- [x] Variante B disponible (opt-in) + test de aislamiento.
- [x] Settings + `ToolProviderNotConfigured`.
- [x] Métricas + span + log de paridad.
- [x] `docs/tool-providers.md` + 2 ejemplos ejecutables (verificados).
- [x] Coverage ≥ 85% en módulos nuevos (providers 100%, tool_registry 85%).
- [x] Suite verde en el alcance de la fase: 2604 passed; ~50 fallos preexistentes ajenos a Fase Y (verificado contra baseline con `git stash`). `ruff`/`mypy --strict`/`bandit` clean en `prismal/` y `tests/`.
- [x] `CLAUDE.md` + `README.md` actualizados.
- [ ] PR mergeado con review aprobado.
- [ ] Migración del host (`prismal-sdk`/`prismal-web`) — repo externo; receta en `docs/tool-providers.md`.

---

## 7. Estimación de Esfuerzo por Sub-Fase

| Sub-fase | Esfuerzo |
|---|---|
| Y1 — Puerto | 0.2 sem |
| Y2 — Proveedores | 1.0 sem |
| Y3 — Variante A + refactor | 0.8 sem |
| Y4 — Variante B | 1.0 sem |
| Y5 — Settings + exception | 0.2 sem |
| Y6 — Observabilidad | 0.3 sem |
| Y7 — Docs + ejemplos | 0.6 sem |
| Y8 — Tests + paridad | 0.5 sem |
| Hardening | 0.5 sem |
| **Total** | **~5 sem** |

---

## 8. Métricas de Éxito Operacionales

- 0 imports de `prismal.mcp`/`prismal.skills` en `prismal/agents/**` (excl. `extension/providers.py`).
- 0 regresiones en la suite existente.
- 100% de paridad en `get_tools_for_agent` con el composite por defecto.
- `prismal_tool_provider_fallback_total == 0` en despliegues con host migrado.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Plan de implementación inicial — inyección de tool providers |
