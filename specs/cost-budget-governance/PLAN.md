# Prismal — Cost & Budget Governance

## Strategic Plan / Product Requirements Document (PLAN) — *PRD semilla*

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` (PRD semilla; faltan ARCHITECTURE/SPEC/TASKS) |
| **Versión** | 0.1 |
| **Fecha** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, FinOps |
| **Prioridad** | P2 (predictibilidad de coste) |
| **Relacionado** | `agents/tool_registry.py` (`react_loop`), `agents/patterns/`, `monitoring/`, `providers/` |

---

## 1. Resumen Ejecutivo

Los patrones avanzados de prismal son **caros por diseño**: debate (N agentes × M rondas), Tree-of-Thoughts, LATS (MCTS), Mixture-of-Agents y parallel dispatch multiplican las llamadas al LLM (un debate 4×5 = 20+ llamadas mínimo). Hoy no hay **presupuesto por ejecución** ni **circuit-breakers de coste/llamadas/tokens**: un flujo mal configurado o un bucle pueden disparar el gasto sin tope. Esta feature añade **gobernanza de coste**: presupuesto por run/sesión/tenant, medición de coste/tokens en tiempo real, y cortes (soft/hard) cuando se exceden los límites — convirtiendo "funciona" en "funciona dentro de un presupuesto predecible".

---

## 2. Contexto y Problema

- **Sin tope por ejecución:** `react_loop` limita iteraciones (`_MAX_REACT_ITERATIONS`) y el supervisor enruta, pero no hay un presupuesto agregado de **tokens/coste/llamadas** por turno o por sesión.
- **Patrones multiplicadores:** debate/ToT/LATS/MoA/parallel pueden explotar el número de llamadas; el coste no se acota ni se reporta por adelantado.
- **Sin atribución de coste:** no se mide coste por agente/patrón/tenant; FinOps no tiene visibilidad.
- **Sin circuit-breakers:** ante un bucle o un agente remoto (A2A) costoso, no hay corte automático.
- **Multi-tenant:** sin cuota por `org_id`, un tenant puede consumir el presupuesto de otros.

---

## 3. Usuarios Objetivo

- **FinOps / Operator:** fijar presupuestos por tenant/sesión; ver coste atribuido; alertas.
- **Flow Author:** declarar un `budget` por patrón costoso (debate/ToT) y degradar con gracia al excederlo.
- **Platform Host (`prismal-server`):** enforcement de cuota por `org_id` (vía Fase R); rechazo/cola al exceder.
- **SRE:** circuit-breakers ante bucles/coste runaway.

---

## 4. Objetivos y Métricas de Éxito

| Objetivo | Métrica | Target |
|---|---|---|
| Presupuesto por run | Cap de tokens/coste/llamadas por turno/sesión | Configurable |
| Medición en tiempo real | Coste/tokens acumulados por run (vía `monitoring/`) | Disponible |
| Circuit-breakers | Corte soft (avisa/degrada) y hard (aborta) al exceder | Implementado |
| Atribución | Coste por agente/patrón/tenant | Reportado |
| Cuota multi-tenant | Límite por `org_id` (Fase R) | Soportado |
| Backward-compat | Sin límites configurados, comportamiento actual | 100% |

---

## 5. Alcance (propuesto)

### In Scope
- **`Budget`** (tokens, coste USD estimado, nº de llamadas LLM, wall-clock) por **scope** (turn / session / tenant).
- **`CostMeter`** que acumula uso desde los callbacks de `providers/` (LiteLLM usage) y `react_loop`; estimación de coste por modelo (tabla de precios configurable).
- **`BudgetGuard`** integrado en `react_loop` y en los patrones costosos: chequeo pre-llamada; **soft cap** (degradar: menos rondas/ramas, modelo más barato, terminar con mejor-esfuerzo) y **hard cap** (`BudgetExceeded` → abort con respuesta parcial auditada).
- **Atribución** por agente/patrón/tenant en spans/métricas (`monitoring/`).
- **Cuota por tenant** vía Fase R (presupuesto resuelto por `org_id`).
- Settings `budget_*`; degradación configurable por patrón.

### Out of Scope
- Facturación/chargeback real (se exportan métricas; la facturación es del host).
- Precios en tiempo real de proveedores (tabla configurable; actualización manual/periódica).
- Optimización automática de prompts para reducir coste (futuro).

---

## 6. Requisitos Funcionales (resumen)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-CST-001 | `Budget` por scope (turn/session/tenant) con límites de tokens/coste/llamadas | `MUST` |
| RF-CST-002 | `CostMeter` acumula uso real desde `providers/` + estimación por tabla de precios | `MUST` |
| RF-CST-003 | `BudgetGuard` con soft cap (degradar) y hard cap (`BudgetExceeded`) en `react_loop` y patrones | `MUST` |
| RF-CST-004 | Atribución de coste por agente/patrón/tenant (métricas/spans) | `SHOULD` |
| RF-CST-005 | Cuota por `org_id` vía Fase R | `SHOULD` |
| RF-CST-006 | Settings `budget_*`; degradación configurable por patrón | `MUST` |
| RF-CST-007 | Auditoría de cortes (qué se abortó/degradó y por qué) | `SHOULD` |

---

## 7. Riesgos y Mitigaciones (resumen)

| Riesgo | Mitigación |
|---|---|
| Estimación de coste imprecisa | Usar usage real de LiteLLM; tabla de precios versionada; marcar estimaciones |
| Hard cap corta respuestas útiles | Soft cap primero (degradar); hard cap con respuesta parcial + aviso claro |
| Overhead de medición | Acumulación O(1) por llamada; sin I/O en el hot path |
| Patrones que ignoran el guard | `BudgetGuard` inyectado en el factory de patrones; test de enforcement |

---

## 8. Dependencias

- `agents/tool_registry.py::react_loop` (punto de chequeo principal).
- `agents/patterns/` (debate, ToT, LATS, MoA, parallel) — integrar el guard.
- `providers/` (usage real de LiteLLM), `monitoring/` (métricas/spans), `core/config.py`.
- `specs/composition-root/` (cuota por tenant), `specs/a2a-interop/` (coste de delegaciones remotas).

---

## 9. Próximos Pasos

Expandir a set SDD completo: diseño de `CostMeter`/`BudgetGuard`, puntos de integración exactos en `react_loop` y en el factory de patrones, tabla de precios, estrategias de degradación por patrón, y enforcement multi-tenant.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | PRD semilla — gobernanza de coste y presupuesto |
