# Prismal — Qué falta implementar (jul 2026): Loops, Harness y Guardrails

| Campo | Valor |
|---|---|
| **Fecha** | 2026-07-04 |
| **Base** | `prismal` v3.5.0 (repo local), `specs/` (17 carpetas), `CHANGELOG.md`, `README.md`, `docs/competitive-analysis.md` (2026-06-06) + investigación web actual |
| **Alcance** | Comparar el estado real del repo contra tendencias 2026 en (1) *agentic loops*, (2) *agent harness engineering*, (3) nuevas implementaciones de Guardrails |

---

## 1. Punto de partida: casi todo el roadmap interno ya está cerrado

El `CHANGELOG.md` es explícito: *"All spec-complete phases under `specs/` are now implemented. New work starts here."* Verificado contra el código (no solo contra la documentación):

- Los 4 specs que **no** aparecen en `CLAUDE.md` (por ser posteriores a su última edición) — `agent-eval-harness` (Phase V), `agent-identity-governance` (Phase IDN), `runtime-hardening` (Phase H) y `dependency-security-remediation` — están **implementados de verdad**: hay paquetes reales (`prismal/eval/`, `prismal/identity/`, `prismal/security/hardening_run.py`), tests dedicados, commits mergeados y entradas en el CHANGELOG (v3.2.0–v3.5.0).
- `docs/competitive-analysis.md` (2026-06-06) listaba P0–P2 como pendientes (Vector Store Port, Composition Root, A2A, Identity, Eval Harness, Budget); **todos** están hoy en estado `✅ implementado` según el propio `README.md` (sección "Roadmap"). Ese documento de competitividad está desactualizado y debería refrescarse.
- Único punto de higiene documental encontrado: `specs/agent-identity-governance/TASKS.md` marca cada tarea individual como `TODO` (30 filas) aunque la cabecera dice "Phases ID1–ID7 are DONE" y el código/tests lo confirman — es un desfase de bookkeeping, no una brecha funcional.

Conclusión: la pregunta "qué falta" ya casi no tiene respuesta *interna* (no hay backlog propio pendiente relevante); las brechas reales hoy vienen de comparar contra el estado del arte externo — que es justo lo que pidió la investigación.

### 1.1 Lo poco que sigue explícitamente pendiente en el propio README

1. **Capa de host/despliegue** (`prismal-server`, `prismal-sdk`, `prismal-dashboard`, `prismal-tui`, `prismal-webchat`, `prismal-chatbot`): son repos "planned/early-stage" — **no existen todavía**. `prismal` es solo el motor embebible; sin el server no hay producto desplegable, REST/WS/SSE, ni endpoint `/a2a` real.
2. **"Polish" (Phase 8, sin spec todavía)**: UI de observabilidad propia (o integración profunda con LangSmith/Langfuse) y **type-safety por nodo** (validación Pydantic de entrada/salida de cada nodo; `AgentState` sigue siendo un `TypedDict` sin ese contrato).
3. **`ID6-02` diferido**: vincular `PermissionManager` a la identidad DID (marcado explícitamente "deferred" en el README).
4. **Auditoría de dependencias con ~1 mes de antigüedad** (`specs/dependency-security-remediation`, fechado 2026-06-05): 7 alertas en estado `MITIGATE` y 4 `SUPPLY-CHAIN` siguen abiertas por diseño (p. ej. `ecdsa` CVE-2024-23342 "won't-fix", deuda de migración a PyJWT registrada pero no ejecutada; `chromadb` CVE-2026-45829 sin parche upstream, solo mitigado). Conviene re-correr `pip-audit`/Trivy: puede haber CVEs nuevas desde entonces.

---

## 2. "Loops" — patrones de bucle agentic: brechas frente al estado del arte 2026

Prismal ya cubre una superficie inusualmente amplia de patrones de loop (ReAct vía `react_loop`, Plan-Execute vía `planner` + `dev_pipeline`, Reflection, Tree-of-Thoughts, LATS/MCTS, Debate, Mixture-of-Agents, LLM-Compiler, Swarm) — más que la mayoría de frameworks comparables. Las brechas están en la **mecánica del loop**, no en el catálogo de patrones:

- **Sin compactación/gestión de contexto**: no se encontró ningún mecanismo de *trimming*, resumen o compactación del historial de mensajes (`grep` sobre `prismal/memory/*` y `agents/graph.py` no arrojó ningún resultado para "compact/trim/summarize/context window"). Es el ítem #1 de la checklist "harness engineering 2026" (memory compaction) y hoy el `AgentState.messages` crece sin límite salvo por el checkpointer.
- **Sin aprovisionamiento dinámico de herramientas por fase de tarea**: `ToolProviderPort`/`tool_registry` resuelve el set de tools de forma estática por `agent_name` + `capabilities`, no por fase dentro de una misma ejecución (la técnica de "logits masking"/tool-gating dinámico que documentan LangChain/Anthropic en sus reportes de harness 2026).
- **Sin paso de verificación explícito y desacoplado de la reflexión**: los patrones de reflexión (`reflection_loop`, Constitutional) mezclan crítica y verificación; el patrón 2026 más citado (PRAR: Percepción→Razonamiento→Acción→**Reflexión**, con verificación de resultado como paso propio, p. ej. ejecutar tests/linters antes de declarar éxito) no está modelado como primitiva reusable.
- **Replay/resumibilidad más allá del checkpoint**: hay `AsyncSqliteSaver`/Postgres para checkpointing, pero no un mecanismo explícito de "stateful/resumable harness" con reanudación tras crash a nivel de *sub-tarea* (los workshops 2026 de Mastra et al. lo listan como estándar emergente).

Fuentes: [Agentic Reasoning Patterns: ReAct, Reflexion & ToT (2026)](https://servicesground.com/blog/agentic-reasoning-patterns/) · [Agentic AI Design Patterns in 2026](https://www.innovatrixinfotech.com/blog/agentic-ai-design-patterns-react-reflection-tool-use) · [Agentic Design Patterns: The 2026 Guide](https://www.sitepoint.com/the-definitive-guide-to-agentic-design-patterns-in-2026/) · [Architecting Resilient LLM Agents: Plan-then-Execute](https://arxiv.org/pdf/2509.08646) · [Choose a design pattern for agentic AI (Google Cloud)](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system)

---

## 3. "Harness" — evaluación y control-plane del agente

Prismal **ya tiene** un harness de evaluación propio (Phase V, `prismal/eval/`: `EvalRunner`, trayectorias, aserciones exactas/semánticas/LLM-judge, regresión con gate de CI, suite red-team). Comparado con el estado del arte 2026:

- **"Agent = Model + Harness"** se consolidó como marco conceptual en 2026 (LangChain, Anthropic, OpenAI, HumanLayer, Thoughtworks); sus componentes canónicos son permisos, sandboxing, observabilidad, memoria y control loop. Prismal cubre permisos (`PermissionManager`/`ActionInterceptor`/`PolicyEngine`), sandbox (`SandboxExecutor`) y observabilidad (OTel/Langfuse) — pero **memoria durable tipo `AGENTS.md`** (un archivo que el propio agente lee/escribe entre sesiones para retener aprendizajes) no existe como primitiva (`memory/` es historial conversacional + long-term PII-safe, no "notas del agente para sí mismo").
- **Reward hacking / hardening de benchmarks**: en abril de 2026 UC Berkeley reportó que un agente automatizado "rompió" los 8 benchmarks principales de agentes por *reward hacking*; la industria está endureciendo verificadores y trazas. El red-team suite de Prismal (V5) valida contención L1–L5, pero no hay un verificador anti-reward-hacking específico (p. ej. detectar que el agente "hackeó" su propio criterio de éxito en vez de resolver la tarea).
- **Harness-effect benchmarking**: existe investigación 2026 (*Harness-Bench*) que mide cómo el mismo modelo rinde distinto según el harness que lo rodea. Prismal no tiene un mecanismo para comparar su propio harness contra otros (out of scope probablemente, pero es la frontera de la disciplina).
- **Trayectoria + verificadores externos**: el patrón emergente de "trajectory evaluation" (no solo el resultado final, sino la cadena de pensamiento-acción) ya está cubierto por `eval/trajectory.py` — este punto está al día.

Fuentes: [Agent Harness Engineering — The Rise of the AI Control Plane](https://medium.com/@adnanmasood/agent-harness-engineering-the-rise-of-the-ai-control-plane-938ead884b1d) · [Harness Engineering: Building Reliable AI Agents (2026)](https://happycapy.ai/blog/harness-engineering-guide) · [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering) · [Agent Harness Engineering Guide 2026 — QubitTool](https://qubittool.com/blog/agent-harness-evaluation-guide) · [Harness-Bench: Measuring Harness Effects across Models](https://arxiv.org/html/2605.27922v1) · [AgentLens: Revealing The Lucky Pass Problem in SWE-Agent Evaluation](https://arxiv.org/pdf/2605.12925)

---

## 4. Guardrails — brecha más concreta y accionable

Esta es la brecha más nítida encontrada. El stack actual de Prismal es:

- **L1** `InputSanitizer` (control chars, unicode, longitud).
- **L2** `GuardrailsEngine` — **regex propio** contra `security/patterns/injection_patterns.yaml` (sin componente de clasificación semántica/ML).
- **L3** `NemoRailsLayer` — NeMo Guardrails **v0.21.0** (versión reciente y correcta, `>=0.10.1` en `pyproject.toml`), pero solo con rieles de diálogo/tópicos propios; no hay un `config.yml` que enganche un modelo clasificador de seguridad (Llama Guard, Nemotron content-safety, jailbreak-detection) — la librería instalada trae ejemplos listos para eso (`llama_guard/`, `jailbreak_detection/`, `content_safety_reasoning/`) que Prismal no usa.
- **L4** `ActionInterceptor` (permisos pre-tool) + `OutputValidator` (HTML-escape, detección de shell metachars).
- **L5** `AuditLogger`.

Lo que la industria 2026 añade y Prismal **no tiene**:

1. **Guardrails AI (el paquete `guardrails-ai`) no está integrado.** La palabra "guardrails" solo aparece como *keyword* de PyPI en `pyproject.toml`, no como dependencia real. Guardrails AI en 2026 es el estándar para *validación de salida estructurada* (schemas Pydantic + reintento automático/"re-ask" cuando la salida no cumple el schema) y trae un Hub con 50+ validadores comunitarios (detección de alucinaciones/provenance, PII, toxicidad, competidores mencionados, etc.). El `OutputValidator` de Prismal es útil pero es una validación ad-hoc (regex + escape), no un framework de schema-enforcement con reintentos.
2. **Sin clasificador de seguridad tipo Llama Guard / Nemotron** como capa dedicada — la detección de jailbreak/contenido dañino depende hoy 100% de regex (L2) más lo que NeMo haga en Colang; no hay un modelo de safety classification real conectado (aunque la infraestructura de NeMo ya lo soporta out-of-the-box).
3. **Sin arquitectura de "3 capas" que hoy se considera de referencia**: *LLM Guard* (scanner rápido de PII/prompt-injection en <50ms) → *NeMo* (control de diálogo) → *Guardrails AI* (enforcement de salida). Prismal cubre la capa 2 muy bien, la capa 1 de forma parcial (regex, no ML) y la capa 3 no la cubre.
4. **"Constitutional classifiers"** (el enfoque de Anthropic de clasificadores entrenados sobre una constitución explícita) no aparece como técnica en las fuentes 2026 más recientes — el mercado se movió hacia clasificadores de razonamiento (Nemotron reasoning-enabled safety, con explicabilidad vía `/think`) en su lugar; si se buscaba paridad con ese enfoque específico, la referencia de mercado ya cambió.

Fuentes: [Best AI Guardrails in 2026 — General Analysis](https://generalanalysis.com/guides/best-ai-guardrails) · [AI Guardrails Compared: NeMo vs Guardrails AI vs Llama Guard](https://particula.tech/blog/ai-guardrails-compared-nemo-guardrails-ai-llama-guard) · [NeMo Guardrails 2026: NVIDIA's LLM Safety Toolkit](https://appsecsanta.com/nemo-guardrails) · [LLM Guardrails: Setup Guide 2026](https://aiworkflowlab.dev/article/llm-guardrails-production-defense-in-depth-safety-systems-nemo-guardrails-ai-openai) · [guardrails-ai · PyPI](https://pypi.org/project/guardrails-ai/) · [Guardrails AI — Generate Structured Data](https://www.guardrailsai.com/docs/how_to_guides/generate_structured_data) · [8 Best AI Agent Guardrails Solutions in 2026 — Galileo](https://galileo.ai/blog/best-ai-agent-guardrails-solutions)

---

## 5. Lista priorizada de lo que faltaría implementar hoy

| # | Brecha | Categoría | Esfuerzo aprox. | Por qué importa |
|---|---|---|---|---|
| 1 | Capa host (`prismal-server`/SDK/dashboard) | Producto | Grande | Sin esto no hay despliegue real; todo lo demás vive solo como librería |
| 2 | Config NeMo con clasificador de seguridad (Llama Guard / Nemotron content-safety) en vez de solo Colang propio | Guardrails | Pequeño-medio | Cierra la brecha ML vs. regex-only en L2/L3; la librería ya soporta el ejemplo |
| 3 | Integrar `guardrails-ai` para output structurado con reintento (schema Pydantic + Hub validators) | Guardrails | Medio | Es hoy el estándar de facto para "structured output guarding"; hoy solo hay regex/escape |
| 4 | Compactación/resumen del historial de mensajes en loops largos | Loops | Medio | Evita degradación de contexto y costo descontrolado en tareas largas (React/Debate/Skynet) |
| 5 | Type-safety por nodo (`AgentState` con validación Pydantic de I/O) | Polish (P3, ya en README) | Medio | Ya identificado por el propio equipo, sin spec todavía |
| 6 | UI de observabilidad propia o integración profunda LangSmith/Langfuse | Polish (P3, ya en README) | Medio-grande | Ya identificado por el propio equipo |
| 7 | Aprovisionamiento dinámico de tools por fase de tarea (tool-gating) | Loops/Harness | Medio | Reduce superficie de ataque y ruido de contexto dentro de una misma ejecución |
| 8 | Refrescar auditoría de dependencias (`pip-audit`/Trivy) — la matriz tiene ~1 mes | Seguridad | Pequeño | `ecdsa` y `chromadb` siguen en estado mitigado, no resuelto |
| 9 | `ID6-02`: enlazar `PermissionManager` a identidad DID | Identity (deferred) | Pequeño | Explícitamente diferido en el propio README |
| 10 | Higiene: actualizar `specs/agent-identity-governance/TASKS.md` (filas `TODO`→`DONE`) y refrescar `docs/competitive-analysis.md` (fechado 2026-06-06, ya obsoleto) | Documentación | Trivial | Evita que un lector externo (o un agente) crea que hay trabajo pendiente que ya se hizo |

---

## 6. Nota metodológica

Los hallazgos de la Sección 1 se verificaron leyendo código real (no solo metadatos de specs): existencia y tamaño de `prismal/eval/*.py`, `prismal/identity/*.py`, `prismal/security/hardening_run.py`; historial de git (`git log`); y conteo de tests dedicados (`tests/unit/identity/`, `tests/unit/eval/`). Los hallazgos de las Secciones 2–4 provienen de búsquedas web de julio de 2026 cruzadas contra `grep`/lectura directa de `prismal/security/*.py`, `prismal/memory/*.py`, `pyproject.toml` y `uv.lock` (para confirmar, por ejemplo, que `nemoguardrails` resuelve a 0.21.0 y que `guardrails-ai` no es una dependencia real pese a aparecer como *keyword*).

---

## 7. Artefactos SDD generados a partir de este análisis (2026-07-04)

Cada fila de la tabla de la Sección 5 quedó registrada como artefacto de Spec-Driven Design en `specs/`, listo para arrancar implementación. Todos usan `Status: DRAFT` y tareas en `TODO` (nada de esto está construido todavía); cada `SPEC.md`/`PLAN.md` referencia esta misma tabla por número de ítem.

| # (§5) | Ítem | Spec creado | Tipo de artefacto |
|---|---|---|---|
| 1 | Capa host | [`specs/reference-host-bootstrap/PLAN.md`](../specs/reference-host-bootstrap/PLAN.md) | PRD seed (solo PLAN — ARCHITECTURE/SPEC/TASKS quedan para el futuro repo `prismal-server`, ver §9 de ese PLAN) |
| 2 | Clasificador de seguridad NeMo | [`specs/guardrails-modernization/`](../specs/guardrails-modernization/) (Phase `GRD`, fase GRD1) | SDD completo (PLAN/ARCHITECTURE/SPEC/TASKS) |
| 3 | `guardrails-ai` estructurado | [`specs/guardrails-modernization/`](../specs/guardrails-modernization/) (Phase `GRD`, fase GRD2) | SDD completo — mismo spec que el ítem 2 (agrupados por ser ambos de la capa Guardrails) |
| 4 | Compactación de contexto | [`specs/loop-hardening/`](../specs/loop-hardening/) (Phase `LH`, fase LH1) | SDD completo |
| 5 | Type-safety por nodo | [`specs/node-io-typesafety/`](../specs/node-io-typesafety/) (Phase `NTS`) | SDD completo |
| 6 | Observabilidad / LangSmith-Langfuse | [`specs/observability-integration/`](../specs/observability-integration/) (Phase `OBS`) | SDD completo |
| 7 | Tool-gating dinámico por fase | [`specs/loop-hardening/`](../specs/loop-hardening/) (Phase `LH`, fase LH2) | SDD completo — mismo spec que el ítem 4 (agrupados por ser ambos de mecánica de loop) |
| 8 | Refresco de auditoría de dependencias | [`specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md`](../specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md) | Addendum a spec ya implementado |
| 9 | `ID6-02` PermissionManager-DID | [`specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md`](../specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md) | Addendum a spec ya implementado |
| 10 | Higiene documental | Aplicado directamente: `specs/agent-identity-governance/TASKS.md` (filas `TODO`→`DONE`, `ID6-02`→`DEFERRED`) y banner de actualización en `docs/competitive-analysis.md` | Edición directa (no requería un spec propio) |

Criterio de agrupación: los ítems 2+3 comparten spec porque ambos modifican la misma capa de seguridad (Guardrails L2/L3/L4) y se despliegan juntos; los ítems 4+7 comparten spec porque ambos son mecánica del bucle agentic (`react_loop`/`supervisor_node`) y se prueban con el mismo snapshot test de "byte-for-byte sin cambios cuando está apagado". Los ítems 1, 8 y 9 recibieron un tratamiento más ligero (PRD seed o addendum) porque el 1 vive fuera de este repositorio y el 8/9 son continuaciones acotadas de specs que ya están `IMPLEMENTED`, no features nuevas.
