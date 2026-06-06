# Prismal — Análisis competitivo y roadmap a producción

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Fecha** | 2026-06-06 |
| **Estado** | `DRAFT` |
| **Base** | Estructura del repo `prismal` (v3.0.0, ~52k LOC, 191 archivos de test) + specs en `specs/` + notas Obsidian `Documentacion/Prismal` |
| **Alcance** | Posicionamiento frente a frameworks de agentes 2026, debilidades, y roadmap por features priorizado |

> Salvedad: este análisis se basa en la estructura del repo, los specs y las notas — no en correr la suite ni auditar cada capacidad end-to-end. Los specs describen el diseño *previsto*; la madurez real puede ser menor.

---

## 1. Qué es prismal y a qué se parece

Prismal es un **"LangGraph con baterías incluidas"**: envuelve LangGraph (motor de grafos de estado) y suma, en un solo paquete, lo que normalmente se ensambla de varias librerías — supervisor + 26 agentes, 7 motores RAG, 9 patrones, 12 subgrafos, seguridad de 5 capas, sandbox, MCP, skills, scheduler y multimodal.

Comparables 2026 por pieza:

| Framework | Solapamiento con prismal | Diferencia clave |
|---|---|---|
| **LangGraph** (LangChain Inc.) | Es el sustrato que prismal envuelve | LangGraph es el motor; prismal añade las baterías |
| **Microsoft Agent Framework** (GA v1.0) | Workflows de grafo + features enterprise | **A2A + MCP nativos**, OTel, Entra ID, runtime gestionado |
| **Google ADK** | Multi-agente + **model-agnostic vía LiteLLM** (igual que prismal) | **A2A con Agent Cards** nativo |
| **Claude Agent SDK** | Supervisor + subagentes + hooks + MCP + skills | Anthropic-native, runtime y memoria gestionados |
| **CrewAI** | Multi-agente por roles | DSL de roles, baja curva |
| **AutoGen / AG2** | Patrones de conversación/debate | Diálogo entre agentes |
| **LlamaIndex** | RAG-first | Profundidad en datos indexados |
| **Pydantic AI** | — | Type-safety y salidas estructuradas |

**Ningún framework único hace exactamente lo mismo**: prismal es una *meta-ensambladura* integrada. Lo más parecido en filosofía es Microsoft Agent Framework (grafo + enterprise + A2A/MCP) y Google ADK (multi-agente + LiteLLM + A2A).

---

## 2. ¿Es el más completo?

- **Por amplitud integrada en un solo codebase: está en el tope.** Casi ningún OSS reúne supervisor + RAG avanzado (7 engines) + seguridad 5-capas + sandbox + MCP + skills + scheduler + multimodal de fábrica.
- **Por madurez de producto/ecosistema: no.** Completitud en 2026 también se mide por runtime gestionado, observabilidad de primera parte (LangSmith), **interop estándar (A2A/Agent Cards)**, **gobernanza de identidad**, eval de sistema y comunidad. Prismal es **beta (v3.0.0), prácticamente de un mantenedor**, con toda la capa superior (`prismal-server`, `prismal-dashboard`, `prismal-sdk`) en estado **"Planned"**.

**Veredicto: el más completo como *biblioteca de capacidades*, no como *plataforma de producción*.**

---

## 3. Debilidades (cruzadas con las brechas del sector 2026)

1. **Brecha experimentación→producción.** Fallo #1 del sector y de prismal: falta la capa de despliegue/servicio (server/dashboard/SDK "Planned").
2. **Sin interop A2A / Agent Cards.** Tiene MCP (interop de *tools*) pero no protocolo agente-a-agente; MS Agent Framework y Google ADK ya lo traen nativo (150+ orgs lo soportan). Riesgo de aislamiento del ecosistema multi-agente.
3. **Identidad/gobernanza de agentes.** Brecha más citada en 2026. Tiene `PermissionManager` + `AuditLogger`, pero no identidad por agente (DID), credenciales OAuth-on-behalf ni IAM para agentes autónomos.
4. **Sin harness de evaluación a nivel de sistema** (el "scaffold gap": evaluar el modelo aislado no predice el sistema agéntico).
5. **Coste/latencia sin gobernanza.** Patrones debate/ToT/LATS/MoA son caros; sin presupuesto/cap por run ni circuit-breakers de coste.
6. **Type-safety parcial.** `AgentState` es `TypedDict` sin validación I/O por nodo.
7. **Observabilidad sin UI propia** (OTel/Langfuse; depende de terceros).
8. **Deuda de cadena de dependencias/seguridad** (18 alertas Dependabot; churn del stack LangChain).
9. **Acoplamientos pendientes:** vector store atado a Chroma; multi-tenant no real; inyección de MCP/Skills/vector sin terminar.
10. **Bus factor / comunidad** mínima frente a frameworks de vendor.

---

## 4. Estado de las especificaciones (qué falta implementar)

| Spec | Fase | Estado real |
|---|---|---|
| `extension-surface` | X | ✅ IMPLEMENTED |
| `advanced-architectures` | A/B/C | ✅ IMPLEMENTED |
| `multimodal-agents` | F | ✅ IMPLEMENTED |
| `tool-provider-injection` | Y | 🟡 En curso (Y1–Y5 con código; cerrar Y6–Y8 + marcar IMPLEMENTED) |
| `vector-store-port` | Z | 🔲 Especificado, sin implementar |
| `composition-root` | R | 🔲 Especificado, sin implementar |
| `dependency-security-remediation` | — | ✅ IMPLEMENTED (18/18 alertas en estado terminal: cerradas/remediadas/mitigadas) |

---

## 5. Roadmap por features priorizado

Prioridad por **desbloqueo de producción** y **riesgo**. Las features marcadas "spec ✔" ya tienen artefactos SDD; las nuevas se generan en `specs/` para próximo desarrollo.

### P0 — Cerrar la última milla a producción (specs ya existentes)
| # | Feature | Spec | Por qué P0 |
|---|---|---|---|
| 1 | **Tool Provider Injection** (Fase Y) | `specs/tool-provider-injection/` ✔ | Cerrar Y6–Y8; desacopla MCP/Skills del núcleo |
| 2 | **Vector Store Port** (Fase Z) | `specs/vector-store-port/` ✔ | Quita lock-in de Chroma; reduce superficie (CVE chromadb) |
| 3 | **Runtime Composition Root** (Fase R) | `specs/composition-root/` ✔ | Desbloquea `prismal-server`/`dashboard` (la capa que falta) |
| 4 | **Dependency Security Remediation** | `specs/dependency-security-remediation/` ✅ | Ya ejecutado: 18/18 alertas en estado terminal + incidente trivy cerrado |

### P1 — Interop y gobernanza (production blockers, specs nuevas)
| # | Feature | Spec | Por qué P1 |
|---|---|---|---|
| 5 | **A2A / Agent Cards interop** (agente-a-agente) | `specs/a2a-interop/` 🆕 (full) | Estándar de interop 2026 (150+ orgs); evita aislamiento; complementa MCP |
| 6 | **Agent Identity & Access Governance** | `specs/agent-identity-governance/` 🆕 (PRD) | Brecha #1 enterprise; DID + credenciales por agente; base de confianza para A2A |

### P2 — Fiabilidad y coste (specs nuevas)
| # | Feature | Spec | Por qué P2 |
|---|---|---|---|
| 7 | **Agent Evaluation Harness** | `specs/agent-eval-harness/` 🆕 (PRD) | Cierra el "scaffold gap"; regresión de fiabilidad del sistema |
| 8 | **Cost & Budget Governance** | `specs/cost-budget-governance/` 🆕 (PRD) | Cap de coste/llamadas por run; circuit-breakers (patrones caros) |

### P3 — Pulido (sin spec aún)
- **Observabilidad UI** de primera parte (o integración profunda LangSmith/Langfuse).
- **Type-safety por nodo** (validación Pydantic de I/O de nodos; evolución de `AgentState`).

---

## 5.1 ¿Framework o host? (dónde vive cada feature)

Regla: **contrato/lógica → framework (`prismal/`); servir HTTP, autenticar, mostrar, persistir config → host (`prismal-server` / `prismal-dashboard`).** A2A e Identity quedan partidos.

| # | Feature | Framework (`prismal/`) | Host (`prismal-server` / `dashboard`) |
|---|---|---|---|
| 1 | Tool Provider (Fase Y) | ports/providers (`agents/extension`) | compone e inyecta al arranque |
| 2 | Vector Store Port (Fase Z) | `rag/stores/` + `VectorStorePort` | elige backend por config |
| 3 | Composition Root (Fase R) | `composition.py` / `build_runtime()` | lo llama en el lifespan |
| 4 | Cost & Budget Governance | guard en `react_loop` + patrones | cuotas por tenant |
| 5 | A2A / Agent Cards (Fase I) | tipos · card · client · `A2AToolProvider` · handler | **endpoint HTTP (`/a2a`, `/.well-known/agent-card.json`) + auth** |
| 6 | Agent Identity & Governance | `PolicyEngine` + puerto de identidad (`security/`) | **IdP/OAuth + bóveda de credenciales + DID** |
| 7 | Agent Eval Harness | motor de eval (módulo) | herramienta dev/CI (o paquete aparte) |
| 8 | Pulido | type-safety por nodo (`AgentState`) | observabilidad UI |

El framework define puertos y lógica; el host los compone y expone. Por eso A2A e Identity tienen una mitad en el núcleo (contrato) y otra en el host (servir/autenticar).

---

## 6. Secuencia recomendada

```
P0 (Y -> Z -> R -> security)   →  capa de producción viable (prismal-server/dashboard)
        │
        ▼
P1 (A2A interop  +  Agent Identity)   →  interoperable y gobernado (ecosistema + enterprise)
        │
        ▼
P2 (Eval harness  +  Cost governance) →  fiable y con coste acotado
        │
        ▼
P3 (Observabilidad UI, type-safety)   →  pulido competitivo
```

Razonamiento: sin **P0** no hay producto desplegable; **A2A + Identity (P1)** es lo que cierra la brecha más visible frente a MS Agent Framework / Google ADK y habilita confianza enterprise; **Eval + Cost (P2)** convierte "funciona" en "es fiable y predecible"; **P3** es diferenciación.

---

## 7. Artefactos generados con este análisis

- `docs/competitive-analysis.md` (este documento).
- `specs/a2a-interop/` — set SDD completo (PLAN, ARCHITECTURE, SPEC, TASKS).
- `specs/agent-identity-governance/PLAN.md` — PRD semilla.
- `specs/agent-eval-harness/PLAN.md` — PRD semilla.
- `specs/cost-budget-governance/PLAN.md` — PRD semilla.

Los tres PRD semilla pueden expandirse a sets SDD completos (ARCHITECTURE/SPEC/TASKS) en la siguiente iteración.

---

## Fuentes

- AI Agent Frameworks Compared 2026 — PE Collective, Alice Labs, Turing.
- Microsoft Agent Framework (Microsoft Learn, GitHub microsoft/agent-framework); Morph "8 SDKs, ACP".
- State of AI Agents 2026 (Lovelytics); Runtime Governance for AI Agents (arXiv); The AI Agent Identity Crisis (Strata).
- A2A Protocol: a2a-protocol.org, github.com/a2aproject/A2A, IBM Think, Atlan.
