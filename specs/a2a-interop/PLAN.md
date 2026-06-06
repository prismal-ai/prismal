# Prismal — A2A / Agent Cards Interoperability (protocolo agente-a-agente)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Fase** | I — Interop (A2A / Agent Cards) |
| **Relacionado con** | Fase X (Extension Surface), Fase Y (Tool Provider Injection), `specs/agent-identity-governance/` |

---

## 1. Resumen Ejecutivo

Prismal habla **MCP** (interoperabilidad de *herramientas*) pero **no habla A2A** (interoperabilidad *agente-a-agente*). A2A (Agent2Agent), creado por Google y donado a la Linux Foundation (Apache 2.0), es el estándar de 2026 para que agentes de distintos vendors y frameworks se **descubran, deleguen tareas y coordinen trabajo** vía **JSON-RPC sobre HTTP(S) + SSE**, publicando un **Agent Card** en `/.well-known/agent-card.json` (nombre, *skills*, endpoint, formatos I/O, métodos de auth) e identidad por **W3C DID**. Lo soportan ya 150+ organizaciones (Google, Microsoft, AWS, Salesforce, SAP, ServiceNow, IBM…). Frameworks rivales (Microsoft Agent Framework, Google ADK) lo traen nativo.

Esta fase añade **interoperabilidad A2A bidireccional**, reusando los patrones ya establecidos en prismal:

- **Inbound (A2A Server):** exponer el grafo/supervisor de prismal como un agente A2A — publicar su **Agent Card** (derivado del registro de agentes y subgrafos), aceptar tareas JSON-RPC, transmitir resultados por SSE, y aplicar auth + las capas de seguridad L1–L5.
- **Outbound (A2A Client):** consumir agentes A2A remotos como **nodos del grafo** (`A2AAgentNode`, análogo a `LangChainRunnableAdapter`) y/o como **herramientas** vía un `A2AToolProvider` que conforma el `ToolProviderPort` (Fase Y). Descubrimiento por Agent Card, delegación de tarea, *streaming* de resultados al estado.

Es **opt-in, aditivo y gated por settings**: sin habilitar A2A, el núcleo se comporta idéntico. Cierra la brecha de interop más visible frente a MS Agent Framework / Google ADK y posiciona a prismal como **ciudadano del ecosistema multi-agente**, no como una isla.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- Prismal integra herramientas externas vía **MCP** (`prismal/mcp/`) y orquesta agentes **internos** vía el supervisor LangGraph. No hay forma de:
  - exponer prismal a **otros** sistemas agénticos como un agente invocable estándar, ni
  - delegar a **agentes remotos** de terceros como parte de un flujo.
- La Fase X (Extension Surface) ya da el patrón para envolver ejecutables como nodos (`LangChainRunnableAdapter`), y la Fase Y da el `ToolProviderPort`. A2A encaja como un nuevo adaptador/proveedor sobre esa base.
- El registro de agentes (`tool_registry`, `DEFAULT_CAPABILITY_MAP`) ya enumera capacidades por agente — material directo para generar *skills* del Agent Card.

### 2.2 Problema

1. **Aislamiento del ecosistema.** Sin A2A, prismal no participa en redes multi-vendor de agentes; no puede ser orquestado por, ni orquestar a, agentes de Google/MS/Salesforce/etc.
2. **Sin descubrimiento estándar.** No hay Agent Card; otros agentes no pueden descubrir las capacidades de prismal de forma interoperable.
3. **Delegación remota imposible.** Un flujo de prismal no puede delegar un sub-objetivo a un agente especializado externo (p. ej. un agente de facturación de un ERP).
4. **Brecha competitiva.** Es una casilla que MS Agent Framework y Google ADK ya marcan; su ausencia descalifica a prismal en RFPs enterprise multi-agente.

### 2.3 Oportunidad

- A2A es **complementario a MCP** (MCP = tools; A2A = agentes), y prismal ya tiene MCP — el modelo mental y la infraestructura HTTP/SSE/auth se reutilizan.
- Los **patrones de Fase X/Y** (adaptadores, puertos, `@prismal_node`, seguridad downstream) hacen el outbound casi mecánico.
- El **Agent Card** se autogenera del registro de agentes — bajo esfuerzo, alto valor.
- Sienta la base de **identidad de agente (DID)**, que enlaza con `specs/agent-identity-governance/`.

---

## 3. Usuarios Objetivo

### Persona 1: Ecosystem Integrator
- **Necesidad:** Que un orquestador externo (MS/Google/ERP) descubra e invoque a prismal como un agente A2A estándar.
- **Frecuencia:** Por integración.

### Persona 2: Flow Author (interno)
- **Necesidad:** Delegar un sub-objetivo a un agente A2A remoto especializado dentro de un grafo de prismal.
- **Frecuencia:** Por diseño de flujo.

### Persona 3: Platform Host (`prismal-server`)
- **Necesidad:** Servir el endpoint A2A (Agent Card + JSON-RPC + SSE) con auth y multi-tenant, reusando el composition root (Fase R).
- **Frecuencia:** Arranque.

### Persona 4: Security/Compliance Lead
- **Necesidad:** Allowlist de agentes remotos, auth (OAuth/mTLS/DID), y auditoría de toda delegación entrante/saliente.
- **Frecuencia:** Configuración + revisión.

---

## 4. Objetivos y Métricas de Éxito

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Inbound A2A | prismal publica Agent Card válido + responde JSON-RPC/SSE | Conforme a A2A v0.3.x | Fase I |
| Outbound A2A | delegar a agente remoto como nodo del grafo | `A2AAgentNode` funcional | Fase I |
| Interop como tools | agentes remotos como tools vía `ToolProviderPort` | `A2AToolProvider` conforme | Fase I |
| Seguridad | auth (OAuth/mTLS) + allowlist + auditoría de delegaciones | 100% de delegaciones auditadas | Fase I |
| Backward-compat | sin habilitar A2A, comportamiento idéntico | 100% | Global |
| Cobertura | branch coverage módulos nuevos | ≥ 85% | Global |

---

## 5. Alcance

### 5.1 In Scope (Fase I)

**I1 — Modelo de dominio A2A (`prismal/a2a/types.py`):**
- [ ] `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact` (Pydantic), conformes a la spec A2A.

**I2 — Generación del Agent Card:**
- [ ] `build_agent_card(settings, registry)` deriva *skills* del registro de agentes/subgrafos + `DEFAULT_CAPABILITY_MAP`; declara endpoint, formatos I/O, auth, y DID.

**I3 — Inbound (A2A Server adapter, `prismal/a2a/server.py`):**
- [ ] Handler JSON-RPC (`message/send`, `tasks/get`, `tasks/cancel`) + streaming SSE.
- [ ] Mapea una tarea A2A entrante → invocación del grafo compilado (o de un subgrafo/skill concreto) → artefactos A2A.
- [ ] El endpoint HTTP lo monta el host (`prismal-server`); el core provee el handler.

**I4 — Outbound (A2A Client, `prismal/a2a/client.py`):**
- [ ] `A2AClient` (descubre Agent Card, envía tarea, consume SSE, maneja auth).
- [ ] `A2AAgentNode(card_url, ...).as_node(name=...)` — envuelve un agente remoto como nodo prismal (`@prismal_node`, security downstream).
- [ ] `A2AConnectionManager` (allowlist, pool, reintentos) — espejo conceptual de `mcp/connection.py`.

**I5 — Interop como herramientas (`prismal/a2a/provider.py`):**
- [ ] `A2AToolProvider` que expone agentes remotos (sus skills) como `BaseTool`, conformando el `ToolProviderPort` (Fase Y) → el host puede componerlo en el `CompositeToolProvider`.

**I6 — Seguridad e identidad:**
- [ ] Auth saliente y entrante: OAuth2 / mTLS; identidad por **DID** (enlaza con `agent-identity-governance`).
- [ ] Allowlist/denylist de agentes remotos por settings; toda respuesta remota es **contenido no confiable** → pasa por L1 (`InputSanitizer`/`SecurePromptBuilder`) y `ActionInterceptor`.
- [ ] `AuditLogger.log_event` por cada tarea A2A entrante/saliente (sin contenido sensible).

**I7 — Settings + ciclo de vida:**
- [ ] `settings.a2a_enabled: bool = False`, `a2a_inbound_enabled`, `a2a_outbound_enabled`, allowlist, auth config.
- [ ] Integración con el composition root (Fase R): `build_runtime` puede componer el `A2AToolProvider` y exponer el handler inbound.

**I8 — Docs, ejemplos, tests:**
- [ ] `docs/a2a.md` (exponer prismal como A2A; consumir agentes remotos).
- [ ] `examples/a2a_server.py`, `examples/a2a_remote_node.py`.
- [ ] Tests con un servidor A2A *fake* (sin red real).

### 5.2 Out of Scope

- Implementar el HTTP server en sí (lo monta `prismal-server`; el core provee handler + tipos).
- Registro/descubrimiento federado de agentes (catálogo A2A) — futuro.
- A2A *push notifications* / webhooks de tareas de larga duración — fase 2.
- Emisión/gestión completa de DIDs (vive en `agent-identity-governance`; aquí se consume).

### 5.3 Futuras Consideraciones

- A2A push notifications para tareas largas.
- Catálogo/registro de agentes (discovery federado).
- Negociación de modalidades (texto/imagen/audio) con la capa multimodal.
- Telemetría de delegaciones (latencia, coste) → enlaza con `cost-budget-governance`.

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-A2A-001 | Modelo de dominio A2A (AgentCard, AgentSkill, Task, Message, Artifact) | `MUST` |
| RF-A2A-002 | `build_agent_card` deriva skills del registro de agentes + capability map | `MUST` |
| RF-A2A-003 | Inbound: handler JSON-RPC (`message/send`, `tasks/get`, `tasks/cancel`) + SSE | `MUST` |
| RF-A2A-004 | Inbound mapea tarea → grafo/subgrafo → artefactos A2A | `MUST` |
| RF-A2A-005 | Outbound: `A2AClient` (discover card, send task, consume SSE, auth) | `MUST` |
| RF-A2A-006 | `A2AAgentNode.as_node()` envuelve agente remoto como nodo del grafo | `MUST` |
| RF-A2A-007 | `A2AToolProvider` conforma `ToolProviderPort` (Fase Y) | `SHOULD` |
| RF-A2A-008 | Auth OAuth2/mTLS + identidad DID (in/out) | `MUST` |
| RF-A2A-009 | Allowlist/denylist de agentes remotos; respuestas remotas pasan por L1–L5 | `MUST` |
| RF-A2A-010 | Auditoría de toda tarea A2A (entrante/saliente) | `MUST` |
| RF-A2A-011 | Settings `a2a_enabled`/inbound/outbound/allowlist/auth; integración con Fase R | `MUST` |
| RF-A2A-012 | Fake A2A server para tests; docs + ejemplos | `SHOULD` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- Overhead del Agent Card: generación cacheada (1 vez por arranque/tenant).
- Streaming SSE sin bloquear el event loop; backpressure razonable.

### Seguridad
- Toda respuesta de agente remoto es **no confiable** → L1 + `ActionInterceptor` antes de inyectar al estado/prompt.
- Auth obligatoria para inbound en producción; mTLS/OAuth; rechazo de Agent Cards sin auth declarada en modo estricto.
- Allowlist por defecto deny-all en outbound estricto.
- Auditoría inmutable de delegaciones (hash-chained, como el resto de `AuditLogger`).

### Compatibilidad
- Conforme a A2A v0.3.x (JSON-RPC over HTTP(S) + SSE, Agent Card en `/.well-known/agent-card.json`).
- `prismal/` namespace PEP 420; A2A gated por `a2a_enabled=False` por defecto.
- `filterwarnings=error`: deps A2A/HTTP opcionales, imports diferidos, extra `[a2a]`.

### Escalabilidad
- Multi-tenant: Agent Card y auth por `org_id` (vía Fase R); aislamiento de delegaciones por tenant.

### Observabilidad
- Spans `prismal.a2a.inbound`, `prismal.a2a.outbound`; métricas de tareas/errores/latencia por agente remoto.

---

## 8. Restricciones y Dependencias

| Dependencia | Tipo | Uso |
|---|---|---|
| Fase X (Extension Surface) | Pre-requisito | `@prismal_node`, adaptador-como-nodo |
| Fase Y (Tool Provider) | Recomendado | `A2AToolProvider` conforma `ToolProviderPort` |
| Fase R (Composition Root) | Recomendado | Componer A2A en el runtime + multi-tenant |
| `specs/agent-identity-governance/` | Acoplado | DID / identidad de agente |
| `mcp/connection.py` | Referencia | Patrón de connection manager (pool/allowlist/retry) |
| Lib A2A / httpx / sse | Nueva (opcional) | Cliente/servidor A2A; extra `[a2a]` |

---

## 9. User Stories

**US-A2A-001:** Como Ecosystem Integrator, descubro e invoco prismal vía su Agent Card.
```
GET https://prismal.example.com/.well-known/agent-card.json
POST /a2a  {jsonrpc, method:"message/send", params:{...}}  -> SSE artifacts
```

**US-A2A-002:** Como Flow Author, delego a un agente remoto como nodo.
```python
from prismal.a2a import A2AAgentNode
node = A2AAgentNode("https://billing.acme/.well-known/agent-card.json").as_node(name="billing_agent")
builder.add_node("billing_agent", node)
```

**US-A2A-003:** Como Host, expongo prismal como A2A en el lifespan.
```python
from prismal.a2a import build_agent_card, A2AServerHandler
card = build_agent_card(settings, registry)
handler = A2AServerHandler(graph)   # montar en FastAPI: /.well-known/agent-card.json + /a2a
```

**US-A2A-004:** Como Security Lead, restrinjo y audito.
```python
settings.a2a_outbound_allowlist = ["billing.acme", "*.trusted.org"]
# toda delegación -> AuditLogger; respuestas remotas -> InputSanitizer
```

---

## 10. Riesgos y Mitigaciones

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Respuesta de agente remoto maliciosa (prompt injection) | Alta | Alto | L1 `InputSanitizer`/`SecurePromptBuilder` + `ActionInterceptor` antes de inyectar |
| Endpoint inbound sin auth expuesto | Media | Crítico | Auth obligatoria en prod; modo estricto rechaza cards sin auth; default disabled |
| Delegación a agente no autorizado | Media | Alto | Allowlist deny-all por defecto en outbound estricto |
| Spec A2A evoluciona (v0.x) | Media | Medio | Pin de versión; tipos versionados; tests de conformidad |
| Tareas largas bloquean recursos | Media | Medio | Timeouts + cancel (`tasks/cancel`); push notifications (fase 2) |
| Coste de delegaciones descontrolado | Media | Medio | Enlazar con `cost-budget-governance` (cap por run) |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| I1 — Tipos A2A | 0.4 sem | Modelo de dominio |
| I2 — Agent Card | 0.4 sem | `build_agent_card` |
| I3 — Inbound handler | 1.0 sem | JSON-RPC + SSE + mapeo a grafo |
| I4 — Outbound client/node | 1.0 sem | `A2AClient`, `A2AAgentNode`, connection manager |
| I5 — A2AToolProvider | 0.4 sem | conforma `ToolProviderPort` |
| I6 — Seguridad/identidad | 0.6 sem | auth + DID + allowlist + audit |
| I7 — Settings + Fase R | 0.3 sem | toggles + composición |
| I8 — Docs + ejemplos + tests | 0.6 sem | fake server + ejemplos |
| Hardening | 0.5 sem | coverage, conformidad, mypy/bandit |
| **Total** | **~5.2 sem** | Interop A2A bidireccional |

---

## 12. Definición de Done (Global de Fase I)

- [ ] Modelo de dominio A2A conforme a v0.3.x.
- [ ] `build_agent_card` genera card válido desde el registro.
- [ ] Inbound: handler JSON-RPC + SSE mapea tareas al grafo; montable por `prismal-server`.
- [ ] Outbound: `A2AClient` + `A2AAgentNode` + connection manager (allowlist/retry).
- [ ] `A2AToolProvider` conforma `ToolProviderPort`.
- [ ] Auth (OAuth/mTLS) + DID + allowlist + auditoría de delegaciones.
- [ ] Respuestas remotas pasan por L1–L5.
- [ ] Settings `a2a_*` (default off); integración con Fase R.
- [ ] `docs/a2a.md` + 2 ejemplos + tests con fake server; coverage ≥ 85%.
- [ ] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + notas Obsidian actualizadas.
- [ ] PR mergeado con review.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Versión inicial — interoperabilidad A2A / Agent Cards |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
| Security Lead | — | | ☐ Pendiente |
