# Prismal A2A Interoperability — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **SPEC** | `specs/a2a-interop/SPEC.md` |
| **TASKS** | `specs/a2a-interop/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Contexto

Prismal interopera a nivel de **herramientas** (MCP) pero no a nivel de **agentes**. A2A (Agent2Agent, Linux Foundation, Apache 2.0) es el estándar de interop agente-a-agente: descubrimiento por **Agent Card** (`/.well-known/agent-card.json`), transporte **JSON-RPC over HTTP(S) + SSE**, identidad **W3C DID**, auth declarada (OAuth/mTLS). Este documento describe la **Fase I — Interop**, que añade A2A **bidireccional** reutilizando la superficie de extensión (Fase X), el `ToolProviderPort` (Fase Y) y el composition root (Fase R), con la seguridad L1–L5 aplicada a todo lo que entra/sale.

---

## 2. Objetivos Técnicos

- **OT-1:** Exponer prismal como agente A2A (inbound): Agent Card + handler JSON-RPC/SSE.
- **OT-2:** Consumir agentes A2A remotos (outbound): como nodo del grafo y como tools.
- **OT-3:** Reutilizar patrones existentes (adaptador-como-nodo de Fase X; `ToolProviderPort` de Fase Y; connection manager de `mcp/`).
- **OT-4:** Tratar todo contenido remoto como no confiable (L1–L5) y auditar toda delegación.
- **OT-5:** Gated y multi-tenant vía settings + Fase R; default off.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel

```
            EXTERNAL A2A WORLD (Google/MS/ERP/...)
                 ▲ inbound                 │ outbound
   GET /.well-known/agent-card.json        │  discover remote card
   POST /a2a  (JSON-RPC + SSE)             ▼
┌──────────────────────────────────────────────────────────────┐
│ prismal-server (FastAPI)  — monta el endpoint A2A             │
│   /.well-known/agent-card.json  ->  build_agent_card(...)     │
│   /a2a  ->  A2AServerHandler(graph)                           │
└───────────────┬───────────────────────────────┬──────────────┘
                │ inbound                         │ outbound
┌───────────────▼─────────────┐   ┌───────────────▼──────────────┐
│ prismal/a2a/server.py       │   │ prismal/a2a/client.py        │
│  A2AServerHandler           │   │  A2AClient · A2AConnectionMgr │
│  task -> graph/subgraph     │   │  A2AAgentNode (as_node)       │
│  -> A2A artifacts (SSE)     │   │  A2AToolProvider (ToolPort)   │
└───────────────┬─────────────┘   └───────────────┬──────────────┘
                │  (L1-L5 security + audit on both directions)   │
┌───────────────▼────────────────────────────────▼──────────────┐
│ prismal core: graph (supervisor) · @prismal_node · security    │
│ tool_registry (ToolProviderPort) · AuditLogger · ports.py      │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 Componentes

#### I1 — Tipos (`prismal/a2a/types.py`)
Pydantic models conformes a A2A v0.3.x: `AgentCard` (name, description, url, version, `skills: list[AgentSkill]`, `capabilities`, `securitySchemes`, `provider`, `did`), `AgentSkill` (id, name, description, tags, inputModes, outputModes), `A2ATask` (id, status, history), `A2AMessage` (role, parts), `A2AArtifact` (parts), `A2APart` (text/file/data).

#### I2 — Agent Card (`prismal/a2a/card.py`)
`build_agent_card(settings, registry, *, org_id=None)`:
- Itera el registro de agentes/subgrafos + `DEFAULT_CAPABILITY_MAP` → un `AgentSkill` por capacidad/subgrafo expuesto (allowlist de qué se publica).
- Rellena endpoint (`settings.a2a_base_url`), auth (`securitySchemes`), `did` (de `agent-identity-governance`), modalidades I/O (texto; +media si `multimodal_enabled`).
- Cacheado por `org_id`.

#### I3 — Inbound (`prismal/a2a/server.py`)
`A2AServerHandler(graph)`:
- `handle(jsonrpc_request) -> SSE | response` para `message/send`, `tasks/get`, `tasks/cancel`.
- Mapea `message/send` → construir `HumanMessage` (sanitizado) → invocar el grafo (o el subgrafo/skill nombrado en `params.skillId`) con un `thread_id`/`task_id` → emitir artefactos A2A por SSE conforme el grafo *streamea*.
- El **HTTP** lo monta el host; el core entrega el handler (desacople, igual que el resto de puertos).

#### I4 — Outbound (`prismal/a2a/client.py`)
- `A2AClient(card_url, auth)`: descubre Agent Card, `send_task`, consume SSE, maneja auth y errores.
- `A2AConnectionManager(allowlist, ...)`: pool/reintentos/allowlist (espejo de `mcp/connection.py`).
- `A2AAgentNode(card_url|client).as_node(name, capabilities)`: envuelve el agente remoto como `async (state) -> state_update` decorado con `@prismal_node` (security/otel/audit gratis); mapea `state["messages"]` ↔ tarea A2A.

#### I5 — `A2AToolProvider` (`prismal/a2a/provider.py`)
Expone cada *skill* de agentes remotos allowlisted como un `BaseTool` (`name=f"a2a__{agent}__{skill}"`), conformando `ToolProviderPort.get_tools(...)`. El host lo añade al `CompositeToolProvider` (Fase Y). Import diferido; captura → `[]`.

#### I6 — Seguridad/identidad
- Outbound: auth desde `securitySchemes` del card (OAuth client-credentials / mTLS); identidad propia por DID.
- Inbound: validar auth del llamante; rechazar si falta en modo estricto.
- Todo `A2AArtifact`/`A2AMessage` remoto → `InputSanitizer.sanitize` + `SecurePromptBuilder` antes de tocar el estado; `ActionInterceptor.check` si dispara tools.
- `AuditLogger.log_event("a2a.inbound"/"a2a.outbound", {agent, skill, task_id, status})`.

#### I7 — Settings + Fase R
`a2a_enabled`, `a2a_inbound_enabled`, `a2a_outbound_enabled`, `a2a_base_url`, `a2a_outbound_allowlist`, `a2a_auth`. `build_runtime` (Fase R) compone `A2AToolProvider` y expone el handler inbound cuando `a2a_enabled`.

### 3.3 Flujos

#### Flujo A — Inbound (prismal como agente A2A)
```
1. Remote orchestrator GET /.well-known/agent-card.json -> build_agent_card()
2. POST /a2a {method:"message/send", params:{message, skillId?}}
3. A2AServerHandler: auth check -> sanitize -> invoke graph(thread=task_id)
4. stream graph outputs -> SSE A2A artifacts ; audit
```

#### Flujo B — Outbound como nodo
```
1. A2AAgentNode(card_url) discovers card (allowlist check)
2. node(state): build A2A task from state.messages -> A2AClient.send_task (auth)
3. consume SSE artifacts -> sanitize -> merge into state.messages ; audit
```

#### Flujo C — Outbound como tools
```
1. A2AToolProvider.get_tools(agent_name): list remote skills as BaseTool
2. composed into CompositeToolProvider (Fase Y) -> agent calls a2a__billing__invoice as a tool
3. react_loop executes -> A2AClient under the hood -> result (sanitized) as ToolMessage
```

---

## 4. Decisiones de Diseño

### DD-A2A-001: A2A es interop de *agentes*, complementario a MCP (*tools*)
Se mantiene `prismal/mcp/` para tools y se añade `prismal/a2a/` para agentes. No se fusionan: distinta semántica (un agente A2A es autónomo y stateful; una tool MCP es una función).

### DD-A2A-002: Outbound reutiliza el patrón adaptador-como-nodo (Fase X) y el `ToolProviderPort` (Fase Y)
`A2AAgentNode` es a A2A lo que `LangChainRunnableAdapter` es a LangChain. `A2AToolProvider` conforma el puerto de Fase Y → cero código nuevo de cableado en agentes.

### DD-A2A-003: El HTTP lo monta el host; el core provee handler + tipos
Coherente con la frontera núcleo/host (el core no levanta servidores). `prismal-server` monta `/.well-known/agent-card.json` y `/a2a`.

### DD-A2A-004: Todo lo remoto es no confiable
A2A cruza un límite de confianza; las respuestas pasan por L1–L5 igual que cualquier entrada de usuario. Esto es no-negociable y se testea.

### DD-A2A-005: Identidad delegada a `agent-identity-governance`
El DID y la emisión de credenciales viven en su propia feature; A2A los **consume** (declara DID en el card, usa credenciales para auth). Evita acoplar dos esfuerzos grandes.

### DD-A2A-006: Gated y multi-tenant
`a2a_enabled=False` por defecto; Agent Card y auth por `org_id` vía Fase R. Sin habilitar, cero superficie nueva.

### DD-A2A-007: Conformidad versionada
Tipos pinneados a A2A v0.3.x; tests de conformidad del Agent Card y del handler JSON-RPC contra ejemplos de la spec.

---

## 5. Estructura del Código

```
prismal/
└── a2a/                         # NUEVO subpaquete (extra [a2a])
    ├── __init__.py              # re-exports públicos
    ├── types.py                 # AgentCard, AgentSkill, Task, Message, Artifact (Pydantic)
    ├── card.py                  # build_agent_card(settings, registry, org_id=)
    ├── server.py                # A2AServerHandler (inbound JSON-RPC/SSE)
    ├── client.py                # A2AClient, A2AConnectionManager, A2AAgentNode
    └── provider.py              # A2AToolProvider (conforma ToolProviderPort)
prismal/core/config.py           # MODIFICADO: settings a2a_*
prismal/core/exceptions.py       # MODIFICADO: A2AError, A2AAgentUnavailable
docs/a2a.md                      # NUEVO
examples/a2a_server.py           # NUEVO
examples/a2a_remote_node.py      # NUEVO
tests/unit/a2a/                  # fake A2A server + conformidad
```

### Patrones Aplicados
- **Adapter** (`A2AAgentNode`, `A2AToolProvider`) sobre Fase X/Y.
- **Hexagonal** (handler/provider desacoplados del HTTP).
- **Connection manager** (espejo de `mcp/connection.py`).

### Manejo de Errores
- Agente remoto no disponible / fuera de allowlist → `A2AAgentUnavailable` (el nodo devuelve un estado con `error=True`, no rompe el grafo).
- Auth fallida inbound → 401/JSON-RPC error; auditar intento.
- Timeout de tarea → `tasks/cancel` + mensaje al estado.

---

## 6. Seguridad

### 6.1 Superficie de Ataque
- **Inbound expuesto sin auth:** mitigado con auth obligatoria en prod + default off + modo estricto.
- **Respuesta remota maliciosa (prompt injection / tool abuse):** L1 + `ActionInterceptor` + dedupe de tool-calls.
- **Exfiltración por delegación a agente no confiable:** allowlist deny-all en outbound estricto + auditoría.

### 6.2 Reglas Transversales
- Auth (OAuth/mTLS) y DID en ambos sentidos.
- L1–L5 sobre todo artefacto remoto, antes de tocar estado/prompt.
- Auditoría hash-chained de cada tarea (sin contenido sensible).
- Multi-tenant: card/auth por `org_id`; sin fuga entre tenants.

---

## 7. Observabilidad

- Spans `prismal.a2a.inbound{skill}`, `prismal.a2a.outbound{agent,skill}`.
- Métricas `prismal_a2a_tasks_total{dir,agent,status}`, `prismal_a2a_task_latency_seconds`, `prismal_a2a_denied_total{reason}`.
- Log `a2a.task` con `task_id`, `agent`, `skill`, `status`.

---

## 8. Testing Strategy

- **Conformidad:** Agent Card y respuestas JSON-RPC validadas contra ejemplos de A2A v0.3.x.
- **Inbound:** fake JSON-RPC request → handler → SSE artifacts esperados; auth obligatoria.
- **Outbound:** fake A2A server (httpx mock) → `A2AAgentNode` integra resultado en el estado; allowlist enforced.
- **Provider:** `A2AToolProvider` conforma `ToolProviderPort`; se compone en `CompositeToolProvider`.
- **Seguridad:** artefacto remoto con payload de inyección → neutralizado por L1; tool-call peligrosa → bloqueada por `ActionInterceptor`.
- **Sin red real:** todo con fakes; sin `live_api`.

---

## 9. Plan de Rollout

1. I1–I2 (tipos + card) — aditivo.
2. I4–I5 (outbound + provider) — valor inmediato (delegar a agentes remotos) sin exponer nada.
3. I3 (inbound) — requiere host; exponer prismal.
4. I6–I7 (seguridad/identidad + settings + Fase R).
5. I8 (docs/tests).

Backout: `a2a_enabled=False` desactiva todo; el subpaquete es opt-in (extra `[a2a]`).

---

## 10. Preguntas Abiertas

- **PA-1:** ¿Exponer el grafo completo como una sola skill A2A, o una skill por subgrafo/agente seleccionado? (Propuesta: allowlist de skills publicadas; por defecto pocas de alto nivel.)
- **PA-2:** ¿Usar una lib A2A existente (SDK oficial) o implementar el subset JSON-RPC/SSE? (Evaluar en I3/I4; preferir SDK si estable.)
- **PA-3:** ¿Push notifications para tareas largas en esta fase o fase 2? (Propuesta: fase 2.)
- **PA-4:** Acople con `agent-identity-governance`: ¿qué subset de DID se necesita mínimamente para el card? (Coordinar specs.)

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Diseño técnico inicial — A2A bidireccional |
