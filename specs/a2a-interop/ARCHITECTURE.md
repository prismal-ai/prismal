# Prismal A2A Interoperability — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **SPEC** | `specs/a2a-interop/SPEC.md` |
| **TASKS** | `specs/a2a-interop/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Context

Prismal interoperates at the **tool** level (MCP) but not at the **agent** level. A2A (Agent2Agent, Linux Foundation, Apache 2.0) is the agent-to-agent interop standard: discovery via **Agent Card** (`/.well-known/agent-card.json`), **JSON-RPC over HTTP(S) + SSE** transport, **W3C DID** identity, declared auth (OAuth/mTLS). This document describes **Phase I — Interop**, which adds **bidirectional** A2A reusing the extension surface (Phase X), the `ToolProviderPort` (Phase Y), and the composition root (Phase R), with the L1–L5 security applied to everything that enters/leaves.

---

## 2. Technical Goals

- **OT-1:** Expose prismal as an A2A agent (inbound): Agent Card + JSON-RPC/SSE handler.
- **OT-2:** Consume remote A2A agents (outbound): as a graph node and as tools.
- **OT-3:** Reuse existing patterns (adapter-as-node from Phase X; `ToolProviderPort` from Phase Y; connection manager from `mcp/`).
- **OT-4:** Treat all remote content as untrusted (L1–L5) and audit every delegation.
- **OT-5:** Gated and multi-tenant via settings + Phase R; default off.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram

```
            EXTERNAL A2A WORLD (Google/MS/ERP/...)
                 ▲ inbound                 │ outbound
   GET /.well-known/agent-card.json        │  discover remote card
   POST /a2a  (JSON-RPC + SSE)             ▼
┌──────────────────────────────────────────────────────────────┐
│ prismal-server (FastAPI)  — mounts the A2A endpoint          │
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

### 3.2 Components

#### I1 — Types (`prismal/a2a/types.py`)
Pydantic models compliant with A2A v0.3.x: `AgentCard` (name, description, url, version, `skills: list[AgentSkill]`, `capabilities`, `securitySchemes`, `provider`, `did`), `AgentSkill` (id, name, description, tags, inputModes, outputModes), `A2ATask` (id, status, history), `A2AMessage` (role, parts), `A2AArtifact` (parts), `A2APart` (text/file/data).

#### I2 — Agent Card (`prismal/a2a/card.py`)
`build_agent_card(settings, registry, *, org_id=None)`:
- Iterates the agent/subgraph registry + `DEFAULT_CAPABILITY_MAP` → one `AgentSkill` per exposed capability/subgraph (allowlist of what is published).
- Fills in endpoint (`settings.a2a_base_url`), auth (`securitySchemes`), `did` (from `agent-identity-governance`), I/O modalities (text; +media if `multimodal_enabled`).
- Cached by `org_id`.

#### I3 — Inbound (`prismal/a2a/server.py`)
`A2AServerHandler(graph)`:
- `handle(jsonrpc_request) -> SSE | response` for `message/send`, `tasks/get`, `tasks/cancel`.
- Maps `message/send` → build a `HumanMessage` (sanitized) → invoke the graph (or the subgraph/skill named in `params.skillId`) with a `thread_id`/`task_id` → emit A2A artifacts over SSE as the graph *streams*.
- The **HTTP** is mounted by the host; the core delivers the handler (decoupled, like the rest of the ports).

#### I4 — Outbound (`prismal/a2a/client.py`)
- `A2AClient(card_url, auth)`: discovers the Agent Card, `send_task`, consumes SSE, handles auth and errors.
- `A2AConnectionManager(allowlist, ...)`: pool/retries/allowlist (mirror of `mcp/connection.py`).
- `A2AAgentNode(card_url|client).as_node(name, capabilities)`: wraps the remote agent as `async (state) -> state_update` decorated with `@prismal_node` (security/otel/audit for free); maps `state["messages"]` ↔ A2A task.

#### I5 — `A2AToolProvider` (`prismal/a2a/provider.py`)
Exposes each *skill* of allowlisted remote agents as a `BaseTool` (`name=f"a2a__{agent}__{skill}"`), conforming to `ToolProviderPort.get_tools(...)`. The host adds it to the `CompositeToolProvider` (Phase Y). Deferred import; capture → `[]`.

#### I6 — Security/identity
- Outbound: auth from the card's `securitySchemes` (OAuth client-credentials / mTLS); own identity via DID.
- Inbound: validate the caller's auth; reject if missing in strict mode.
- Every remote `A2AArtifact`/`A2AMessage` → `InputSanitizer.sanitize` + `SecurePromptBuilder` before touching the state; `ActionInterceptor.check` if it triggers tools.
- `AuditLogger.log_event("a2a.inbound"/"a2a.outbound", {agent, skill, task_id, status})`.

#### I7 — Settings + Phase R
`a2a_enabled`, `a2a_inbound_enabled`, `a2a_outbound_enabled`, `a2a_base_url`, `a2a_outbound_allowlist`, `a2a_auth`. `build_runtime` (Phase R) composes `A2AToolProvider` and exposes the inbound handler when `a2a_enabled`.

### 3.3 Flows

#### Flow A — Inbound (prismal as an A2A agent)
```
1. Remote orchestrator GET /.well-known/agent-card.json -> build_agent_card()
2. POST /a2a {method:"message/send", params:{message, skillId?}}
3. A2AServerHandler: auth check -> sanitize -> invoke graph(thread=task_id)
4. stream graph outputs -> SSE A2A artifacts ; audit
```

#### Flow B — Outbound as a node
```
1. A2AAgentNode(card_url) discovers card (allowlist check)
2. node(state): build A2A task from state.messages -> A2AClient.send_task (auth)
3. consume SSE artifacts -> sanitize -> merge into state.messages ; audit
```

#### Flow C — Outbound as tools
```
1. A2AToolProvider.get_tools(agent_name): list remote skills as BaseTool
2. composed into CompositeToolProvider (Phase Y) -> agent calls a2a__billing__invoice as a tool
3. react_loop executes -> A2AClient under the hood -> result (sanitized) as ToolMessage
```

---

## 4. Design Decisions

### DD-A2A-001: A2A is *agent* interop, complementary to MCP (*tools*)
`prismal/mcp/` is kept for tools and `prismal/a2a/` is added for agents. They are not merged: different semantics (an A2A agent is autonomous and stateful; an MCP tool is a function).

### DD-A2A-002: Outbound reuses the adapter-as-node pattern (Phase X) and the `ToolProviderPort` (Phase Y)
`A2AAgentNode` is to A2A what `LangChainRunnableAdapter` is to LangChain. `A2AToolProvider` conforms to the Phase Y port → zero new wiring code in agents.

### DD-A2A-003: The HTTP is mounted by the host; the core provides handler + types
Consistent with the core/host boundary (the core does not run servers). `prismal-server` mounts `/.well-known/agent-card.json` and `/a2a`.

### DD-A2A-004: Everything remote is untrusted
A2A crosses a trust boundary; responses go through L1–L5 like any user input. This is non-negotiable and is tested.

### DD-A2A-005: Identity delegated to `agent-identity-governance`
The DID and credential issuance live in their own feature; A2A **consumes** them (declares DID in the card, uses credentials for auth). Avoids coupling two large efforts.

### DD-A2A-006: Gated and multi-tenant
`a2a_enabled=False` by default; Agent Card and auth per `org_id` via Phase R. Without enabling, zero new surface.

### DD-A2A-007: Versioned conformance
Types pinned to A2A v0.3.x; conformance tests of the Agent Card and the JSON-RPC handler against spec examples.

---

## 5. Code Structure

```
prismal/
└── a2a/                         # NEW subpackage ([a2a] extra)
    ├── __init__.py              # public re-exports
    ├── types.py                 # AgentCard, AgentSkill, Task, Message, Artifact (Pydantic)
    ├── card.py                  # build_agent_card(settings, registry, org_id=)
    ├── server.py                # A2AServerHandler (inbound JSON-RPC/SSE)
    ├── client.py                # A2AClient, A2AConnectionManager, A2AAgentNode
    └── provider.py              # A2AToolProvider (conforms to ToolProviderPort)
prismal/core/config.py           # MODIFIED: a2a_* settings
prismal/core/exceptions.py       # MODIFIED: A2AError, A2AAgentUnavailable
docs/a2a.md                      # NEW
examples/a2a_server.py           # NEW
examples/a2a_remote_node.py      # NEW
tests/unit/a2a/                  # fake A2A server + conformance
```

### Applied Patterns
- **Adapter** (`A2AAgentNode`, `A2AToolProvider`) on top of Phase X/Y.
- **Hexagonal** (handler/provider decoupled from HTTP).
- **Connection manager** (mirror of `mcp/connection.py`).

### Error Handling
- Remote agent unavailable / outside allowlist → `A2AAgentUnavailable` (the node returns a state with `error=True`, does not break the graph).
- Inbound auth failure → 401/JSON-RPC error; audit the attempt.
- Task timeout → `tasks/cancel` + message to the state.

---

## 6. Security

### 6.1 Attack Surface
- **Inbound exposed without auth:** mitigated with mandatory auth in prod + default off + strict mode.
- **Malicious remote response (prompt injection / tool abuse):** L1 + `ActionInterceptor` + tool-call dedupe.
- **Exfiltration via delegation to an untrusted agent:** deny-all allowlist in strict outbound + auditing.

### 6.2 Cross-Cutting Rules
- Auth (OAuth/mTLS) and DID in both directions.
- L1–L5 over every remote artifact, before touching state/prompt.
- Hash-chained auditing of each task (without sensitive content).
- Multi-tenant: card/auth per `org_id`; no leakage between tenants.

---

## 7. Observability

- Spans `prismal.a2a.inbound{skill}`, `prismal.a2a.outbound{agent,skill}`.
- Metrics `prismal_a2a_tasks_total{dir,agent,status}`, `prismal_a2a_task_latency_seconds`, `prismal_a2a_denied_total{reason}`.
- Log `a2a.task` with `task_id`, `agent`, `skill`, `status`.

---

## 8. Testing Strategy

- **Conformance:** Agent Card and JSON-RPC responses validated against A2A v0.3.x examples.
- **Inbound:** fake JSON-RPC request → handler → expected SSE artifacts; mandatory auth.
- **Outbound:** fake A2A server (httpx mock) → `A2AAgentNode` integrates the result into the state; allowlist enforced.
- **Provider:** `A2AToolProvider` conforms to `ToolProviderPort`; composed into `CompositeToolProvider`.
- **Security:** remote artifact with an injection payload → neutralized by L1; dangerous tool-call → blocked by `ActionInterceptor`.
- **No real network:** everything with fakes; no `live_api`.

---

## 9. Rollout Plan

1. I1–I2 (types + card) — additive.
2. I4–I5 (outbound + provider) — immediate value (delegate to remote agents) without exposing anything.
3. I3 (inbound) — requires a host; expose prismal.
4. I6–I7 (security/identity + settings + Phase R).
5. I8 (docs/tests).

Backout: `a2a_enabled=False` disables everything; the subpackage is opt-in (`[a2a]` extra).

---

## 10. Open Questions

- **PA-1:** Expose the full graph as a single A2A skill, or one skill per selected subgraph/agent? (Proposal: allowlist of published skills; by default a few high-level ones.)
- **PA-2:** Use an existing A2A lib (official SDK) or implement the JSON-RPC/SSE subset? (Evaluate in I3/I4; prefer the SDK if stable.)
- **PA-3:** Push notifications for long tasks in this phase or phase 2? (Proposal: phase 2.)
- **PA-4:** Coupling with `agent-identity-governance`: what minimal subset of DID is needed for the card? (Coordinate specs.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Initial technical design — bidirectional A2A |
