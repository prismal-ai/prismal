# Prismal — Agent Evaluation & Reliability Harness

## Strategic Plan / Product Requirements Document (PLAN) — *PRD semilla*

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` (PRD semilla; faltan ARCHITECTURE/SPEC/TASKS) |
| **Versión** | 0.1 |
| **Fecha** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, QA Lead |
| **Prioridad** | P2 (fiabilidad) |
| **Relacionado** | `skill-creator` (evals de skills), `monitoring/`, `agents/graph.py` |

---

## 1. Resumen Ejecutivo

La investigación de 2026 identifica el **"scaffold gap"**: evaluar el modelo aislado (API directa) **no predice** el comportamiento del sistema agéntico compuesto (con tools, RAG, memoria, multi-turno). Prismal tiene evals a nivel de *skill* (`skill-creator`) pero **no un harness de evaluación a nivel de sistema** del grafo completo: trayectorias, uso de tools, fidelidad de RAG, robustez ante adversario, y **regresión** entre versiones. Esta feature añade un harness de evaluación reproducible para medir y proteger la fiabilidad del agente como sistema.

---

## 2. Contexto y Problema

- **Sin medición de sistema:** no hay forma estándar de correr un conjunto de casos contra el grafo y obtener métricas (tasa de éxito por tarea, pasos, tool-error rate, groundedness de RAG, coste/latencia).
- **Sin regresión:** un cambio (prompt, modelo, patrón, dependencia) puede degradar la calidad sin que ningún test lo detecte (los tests actuales son unitarios/estructurales, no de comportamiento del agente).
- **Sin evaluación adversaria a nivel sistema:** la seguridad L1–L5 se testea unitariamente, pero no hay un *red-team* automatizado de inyección/abuso de tools sobre flujos reales.
- **Sin trazabilidad de calidad por versión:** no hay *scorecards* por release.

---

## 3. Usuarios Objetivo

- **AI Engineer:** correr un eval-set y ver métricas/fallos por caso; comparar dos versiones.
- **QA / Release Manager:** gate de release por umbral de calidad/regresión.
- **Security Lead:** suite adversaria (inyección, exfiltración, tool-abuse) sobre flujos reales.
- **Maintainer:** scorecards por release; detección de drift al subir deps/modelos.

---

## 4. Objetivos y Métricas de Éxito

| Objetivo | Métrica | Target |
|---|---|---|
| Eval de sistema | Correr eval-set contra el grafo y producir métricas | Implementado |
| Métricas de trayectoria | success rate, steps, tool-error rate, RAG groundedness, coste/latencia | Reportadas por caso |
| Regresión | Comparar run vs baseline; gate por umbral | Integrable en CI |
| Suite adversaria | red-team automatizado de seguridad sobre flujos | ≥ N escenarios |
| Reproducibilidad | seeds + fakes; sin no-determinismo evitable | Deterministic donde aplique |
| Backward-compat | aditivo; no toca runtime de agentes | 100% |

---

## 5. Alcance (propuesto)

### In Scope
- **`EvalCase` / `EvalSet`** (entrada, criterios de éxito, asserts: exact/semantic/LLM-judge/tool-usage/groundedness).
- **`EvalRunner`** que ejecuta el grafo (o subgrafo) por caso, captura la **trayectoria** (mensajes, tool-calls, nodos visitados, coste/latencia vía `monitoring/`) y evalúa.
- **Métricas y scorecard** (JSON + Markdown); comparación contra **baseline** (regresión) con gate por umbral.
- **Suite adversaria**: catálogo de escenarios (prompt injection, tool-abuse, exfiltración, jailbreak) ejecutados contra flujos reales; assert de que L1–L5 contiene.
- **Integración CI** (`pytest -m eval` y/o CLI `prismal eval`), con fakes (sin `live_api`) y modo `live_api` opcional.
- LLM-as-judge con rúbricas; reutiliza `providers/` (model-agnostic).

### Out of Scope
- Plataforma de anotación humana / UI de eval (futuro; o integración con LangSmith/Langfuse evals).
- Benchmark público propio (se pueden importar datasets existentes).
- Fine-tuning a partir de resultados.

---

## 6. Requisitos Funcionales (resumen)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-EVL-001 | Modelo `EvalCase`/`EvalSet` con criterios componibles | `MUST` |
| RF-EVL-002 | `EvalRunner` ejecuta el grafo y captura trayectoria + coste/latencia | `MUST` |
| RF-EVL-003 | Asserts: exact, semantic, LLM-judge, tool-usage, RAG groundedness | `MUST` |
| RF-EVL-004 | Scorecard (JSON+MD) + comparación vs baseline (regresión) con gate | `MUST` |
| RF-EVL-005 | Suite adversaria de seguridad sobre flujos reales | `SHOULD` |
| RF-EVL-006 | CLI `prismal eval` + marcador `pytest -m eval`; fakes por defecto | `MUST` |
| RF-EVL-007 | Reproducibilidad (seeds, fakes); modo `live_api` opcional | `SHOULD` |

---

## 7. Riesgos y Mitigaciones (resumen)

| Riesgo | Mitigación |
|---|---|
| No-determinismo del LLM falsea regresión | Seeds + fakes + umbrales con tolerancia; LLM-judge con rúbrica estable |
| Coste de correr evals con modelos reales | Default fakes; `live_api` opt-in; muestreo |
| Evals que no representan producción (scaffold gap) | Ejecutar contra el **grafo real**, no la API directa |
| Mantener eval-sets actualizados | Versionar eval-sets junto al código; gate en CI |

---

## 8. Dependencias

- `agents/graph.py` (ejecutar el grafo), `monitoring/` (coste/latencia/trazas), `providers/` (judge model-agnostic), `security/` (assert de contención adversaria).
- Opcional: integración con Langfuse/LangSmith evals.

---

## 9. Próximos Pasos

Expandir a set SDD completo: diseño de `EvalRunner` y captura de trayectoria desde el *stream* de LangGraph, formato de eval-set, rúbricas de LLM-judge, gate de regresión en CI, y catálogo adversario.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | PRD semilla — harness de evaluación de sistema |
