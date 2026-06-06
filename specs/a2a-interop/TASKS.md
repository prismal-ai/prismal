# Prismal A2A Interoperability — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **Architecture** | `specs/a2a-interop/ARCHITECTURE.md` |
| **SPEC** | `specs/a2a-interop/SPEC.md` |
| **Relacionado** | Fase X, Fase Y, Fase R, `specs/agent-identity-governance/` |

---

## 1. Resumen de Implementación

Fase I añade interop A2A bidireccional en un subpaquete nuevo `prismal/a2a/` (extra `[a2a]`), reutilizando el adaptador-como-nodo (Fase X), el `ToolProviderPort` (Fase Y) y el connection-manager de `mcp/`. **Aditivo, gated (`a2a_enabled=False`), todo lo remoto pasa por L1–L5.** El HTTP lo monta `prismal-server`; el core provee handler + tipos + cliente.

---

## 2. Pre-requisitos

- Fase X: `@prismal_node`, patrón adaptador-como-nodo. (extension implementado)
- Fase Y: `ToolProviderPort` + `CompositeToolProvider` (para `A2AToolProvider`).
- Fase R: `build_runtime` (para componer A2A + multi-tenant) — recomendado.
- `specs/agent-identity-governance/`: DID (mínimo para el card) — coordinar.
- Decisión: usar SDK A2A oficial vs subset propio (ver PA-2).

---

## 3. Fases de Implementación

### FASE I1 — Tipos de dominio
- [ ] `prismal/a2a/types.py`: `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart` (Pydantic v2, conformes v0.3.x).
- **Done:** round-trip `model_dump`/`model_validate` contra ejemplos de la spec.

### FASE I2 — Agent Card
- [ ] `card.py::build_agent_card(settings, registry, org_id=)` deriva skills del registro + allowlist `a2a_published_skills`; auth + did + modalidades; cache por org.
- **Done:** card válido (conformidad) para un registro de prueba.

### FASE I3 — Inbound (server handler)
- [ ] `server.py::A2AServerHandler.handle_rpc` (`message/send`, `tasks/get`, `tasks/cancel`).
- [ ] `_run_task`: sanitize -> invoke graph(thread=task_id) -> SSE artifacts -> audit.
- [ ] Mapeo skill_id -> subgrafo/entrada.
- **Done:** fake JSON-RPC -> SSE artifacts esperados; auth obligatoria en estricto.

### FASE I4 — Outbound (client + node)
- [ ] `client.py::A2AClient` (discover, send_task SSE, cancel, auth).
- [ ] `A2AConnectionManager` (allowlist, pool, retry) — espejo de `mcp/connection.py`.
- [ ] `A2AAgentNode.as_node()` (@prismal_node; map state<->task; error=True sin romper).
- **Done:** fake A2A server -> nodo integra artifacts en el estado; allowlist enforced.

### FASE I5 — A2AToolProvider (Fase Y)
- [ ] `provider.py::A2AToolProvider.get_tools` expone skills remotas como `BaseTool`; conforma `ToolProviderPort`; import diferido; captura -> [].
- **Done:** componible en `CompositeToolProvider`; `react_loop` ejecuta una skill remota.

### FASE I6 — Seguridad e identidad
- [ ] Auth out (OAuth client-credentials / mTLS / bearer) + in (validación de llamante).
- [ ] DID en el card (consume `agent-identity-governance`).
- [ ] Allowlist deny-all en estricto; todo artefacto remoto -> `InputSanitizer`/`SecurePromptBuilder`; tool-calls -> `ActionInterceptor`.
- [ ] `AuditLogger.log_event` por tarea (in/out), sin secretos.
- **Done:** test de inyección remota neutralizada; test de tool peligrosa bloqueada.

### FASE I7 — Settings + Fase R
- [ ] Settings `a2a_*` (default off).
- [ ] `A2AError` / `A2AAgentUnavailable`.
- [ ] `build_runtime`: compone `A2AToolProvider` (si outbound) y expone `A2AServerHandler` (si inbound) en `RuntimeContext`; card por org.

### FASE I8 — Docs + ejemplos + tests
- [ ] `docs/a2a.md` (exponer prismal; consumir remotos; seguridad).
- [ ] `examples/a2a_server.py`, `examples/a2a_remote_node.py`.
- [ ] Fake A2A server para tests (httpx mock); conformidad de card/handler.

### HARDENING
- [ ] Coverage ≥ 85% en `prismal/a2a/**`.
- [ ] `ruff`/`mypy --strict`/`bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] Extra `[a2a]` en `pyproject.toml`; imports diferidos.
- [ ] `CLAUDE.md` + `README.md` + notas Obsidian.

---

## 4. Dependencias Inter-Tareas

```
I1 (types)
 ├─▶ I2 (card) ──────────────▶ I3 (inbound)
 └─▶ I4 (client/node) ─▶ I5 (provider, needs Fase Y)
I6 (security/identity) ─▶ I3, I4, I5
I7 (settings + Fase R) ─▶ compose all
I8 (docs/tests) [last]
```

Ruta crítica: **I1 → I4 → I6 → I8** (outbound primero, sin exponer nada). Inbound (I3) tras tener host.

---

## 5. Matriz Tareas ↔ Requisitos

| Tarea | RF cubiertos |
|---|---|
| I1 | RF-A2A-001 |
| I2 | RF-A2A-002 |
| I3 | RF-A2A-003, RF-A2A-004 |
| I4 | RF-A2A-005, RF-A2A-006 |
| I5 | RF-A2A-007 |
| I6 | RF-A2A-008, RF-A2A-009, RF-A2A-010 |
| I7 | RF-A2A-011 |
| I8 | RF-A2A-012 |

Cobertura: RF-A2A-001..012 mapeados.

---

## 6. Matriz de Riesgos

| Riesgo | Mitigación | Tarea |
|---|---|---|
| Inyección desde agente remoto | L1 + ActionInterceptor sobre artefactos | I6 |
| Inbound sin auth expuesto | Auth obligatoria; default off; estricto | I6, I7 |
| Delegación a agente no autorizado | Allowlist deny-all (estricto) | I4, I6 |
| Drift de la spec A2A (v0.x) | Pin v0.3.x; tests de conformidad | I1, I3 |
| Tareas largas bloquean | Timeout + tasks/cancel | I3, I4 |
| Coste descontrolado de delegaciones | Enlazar `cost-budget-governance` | (cross) |

---

## 7. Definición de Done (Global de Fase I)

- [ ] Tipos A2A conformes v0.3.x; `build_agent_card` válido.
- [ ] Inbound (handler JSON-RPC + SSE) montable por `prismal-server`.
- [ ] Outbound (`A2AClient` + `A2AAgentNode` + connection manager).
- [ ] `A2AToolProvider` conforma `ToolProviderPort`.
- [ ] Auth + DID + allowlist + auditoría; remoto pasa por L1–L5.
- [ ] Settings `a2a_*` (default off); integración con Fase R.
- [ ] `docs/a2a.md` + 2 ejemplos + fake server tests; coverage ≥ 85%.
- [ ] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + Obsidian actualizados; PR mergeado.

---

## 8. Estimación de Esfuerzo

| Sub-fase | Esfuerzo |
|---|---|
| I1 Tipos | 0.4 sem |
| I2 Card | 0.4 sem |
| I3 Inbound | 1.0 sem |
| I4 Outbound | 1.0 sem |
| I5 Provider | 0.4 sem |
| I6 Seguridad/identidad | 0.6 sem |
| I7 Settings + Fase R | 0.3 sem |
| I8 Docs + tests | 0.6 sem |
| Hardening | 0.5 sem |
| **Total** | **~5.2 sem** |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Plan de implementación inicial — A2A interop |
