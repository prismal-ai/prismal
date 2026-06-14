# Prismal — A2A / Agent Cards Interoperability (agent-to-agent protocol)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Phase** | I — Interop (A2A / Agent Cards) |
| **Target package version** | `3.5.0` (SemVer **minor** — new functionality; ships after Phase IDN `3.4.0`, whose DID it consumes) |
| **Related to** | Phase X (Extension Surface), Phase Y (Tool Provider Injection), `specs/agent-identity-governance/` |

---

## 1. Executive Summary

Prismal speaks **MCP** (*tool* interoperability) but **does not speak A2A** (*agent-to-agent* interoperability). A2A (Agent2Agent), created by Google and donated to the Linux Foundation (Apache 2.0), is the 2026 standard for agents from different vendors and frameworks to **discover each other, delegate tasks, and coordinate work** via **JSON-RPC over HTTP(S) + SSE**, publishing an **Agent Card** at `/.well-known/agent-card.json` (name, *skills*, endpoint, I/O formats, auth methods) and identity via **W3C DID**. It is already supported by 150+ organizations (Google, Microsoft, AWS, Salesforce, SAP, ServiceNow, IBM…). Rival frameworks (Microsoft Agent Framework, Google ADK) ship it natively.

This phase adds **bidirectional A2A interoperability**, reusing the patterns already established in prismal:

- **Inbound (A2A Server):** expose prismal's graph/supervisor as an A2A agent — publish its **Agent Card** (derived from the agent and subgraph registry), accept JSON-RPC tasks, stream results over SSE, and apply auth + the L1–L5 security layers.
- **Outbound (A2A Client):** consume remote A2A agents as **graph nodes** (`A2AAgentNode`, analogous to `LangChainRunnableAdapter`) and/or as **tools** via an `A2AToolProvider` that conforms to the `ToolProviderPort` (Phase Y). Discovery via Agent Card, task delegation, *streaming* of results to the state.

It is **opt-in, additive, and gated by settings**: without enabling A2A, the core behaves identically. It closes the most visible interop gap versus MS Agent Framework / Google ADK and positions prismal as a **citizen of the multi-agent ecosystem**, not as an island.

---

## 2. Context and Problem

### 2.1 Current Situation

- Prismal integrates external tools via **MCP** (`prismal/mcp/`) and orchestrates **internal** agents via the LangGraph supervisor. There is no way to:
  - expose prismal to **other** agentic systems as a standard invokable agent, nor
  - delegate to third-party **remote agents** as part of a flow.
- Phase X (Extension Surface) already provides the pattern for wrapping executables as nodes (`LangChainRunnableAdapter`), and Phase Y provides the `ToolProviderPort`. A2A fits as a new adapter/provider on top of that base.
- The agent registry (`tool_registry`, `DEFAULT_CAPABILITY_MAP`) already enumerates capabilities per agent — direct material for generating Agent Card *skills*.

### 2.2 Problem

1. **Ecosystem isolation.** Without A2A, prismal does not participate in multi-vendor agent networks; it cannot be orchestrated by, nor orchestrate, agents from Google/MS/Salesforce/etc.
2. **No standard discovery.** There is no Agent Card; other agents cannot discover prismal's capabilities interoperably.
3. **Remote delegation impossible.** A prismal flow cannot delegate a sub-goal to a specialized external agent (e.g. a billing agent from an ERP).
4. **Competitive gap.** It is a box that MS Agent Framework and Google ADK already check; its absence disqualifies prismal in enterprise multi-agent RFPs.

### 2.3 Opportunity

- A2A is **complementary to MCP** (MCP = tools; A2A = agents), and prismal already has MCP — the mental model and the HTTP/SSE/auth infrastructure are reused.
- The **Phase X/Y patterns** (adapters, ports, `@prismal_node`, downstream security) make the outbound side almost mechanical.
- The **Agent Card** is auto-generated from the agent registry — low effort, high value.
- It lays the foundation for **agent identity (DID)**, which links to `specs/agent-identity-governance/`.

---

## 3. Target Users

### Persona 1: Ecosystem Integrator
- **Need:** Have an external orchestrator (MS/Google/ERP) discover and invoke prismal as a standard A2A agent.
- **Frequency:** Per integration.

### Persona 2: Flow Author (internal)
- **Need:** Delegate a sub-goal to a specialized remote A2A agent within a prismal graph.
- **Frequency:** Per flow design.

### Persona 3: Platform Host (`prismal-server`)
- **Need:** Serve the A2A endpoint (Agent Card + JSON-RPC + SSE) with auth and multi-tenancy, reusing the composition root (Phase R).
- **Frequency:** Startup.

### Persona 4: Security/Compliance Lead
- **Need:** Allowlist of remote agents, auth (OAuth/mTLS/DID), and auditing of all inbound/outbound delegation.
- **Frequency:** Configuration + review.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target | Timeframe |
|---|---|---|---|
| Inbound A2A | prismal publishes a valid Agent Card + responds to JSON-RPC/SSE | Compliant with A2A v0.3.x | Phase I |
| Outbound A2A | delegate to a remote agent as a graph node | `A2AAgentNode` functional | Phase I |
| Interop as tools | remote agents as tools via `ToolProviderPort` | `A2AToolProvider` conformant | Phase I |
| Security | auth (OAuth/mTLS) + allowlist + delegation auditing | 100% of delegations audited | Phase I |
| Backward-compat | without enabling A2A, identical behavior | 100% | Global |
| Coverage | branch coverage of new modules | ≥ 85% | Global |

---

## 5. Scope

### 5.1 In Scope (Phase I)

**I1 — A2A domain model (`prismal/a2a/types.py`):**
- [ ] `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact` (Pydantic), compliant with the A2A spec.

**I2 — Agent Card generation:**
- [ ] `build_agent_card(settings, registry)` derives *skills* from the agent/subgraph registry + `DEFAULT_CAPABILITY_MAP`; declares endpoint, I/O formats, auth, and DID.

**I3 — Inbound (A2A Server adapter, `prismal/a2a/server.py`):**
- [ ] JSON-RPC handler (`message/send`, `tasks/get`, `tasks/cancel`) + SSE streaming.
- [ ] Maps an incoming A2A task → invocation of the compiled graph (or a specific subgraph/skill) → A2A artifacts.
- [ ] The HTTP endpoint is mounted by the host (`prismal-server`); the core provides the handler.

**I4 — Outbound (A2A Client, `prismal/a2a/client.py`):**
- [ ] `A2AClient` (discovers Agent Card, sends task, consumes SSE, handles auth).
- [ ] `A2AAgentNode(card_url, ...).as_node(name=...)` — wraps a remote agent as a prismal node (`@prismal_node`, downstream security).
- [ ] `A2AConnectionManager` (allowlist, pool, retries) — conceptual mirror of `mcp/connection.py`.

**I5 — Interop as tools (`prismal/a2a/provider.py`):**
- [ ] `A2AToolProvider` that exposes remote agents (their skills) as `BaseTool`, conforming to the `ToolProviderPort` (Phase Y) → the host can compose it into the `CompositeToolProvider`.

**I6 — Security and identity:**
- [ ] Outbound and inbound auth: OAuth2 / mTLS; identity via **DID** (links to `agent-identity-governance`).
- [ ] Allowlist/denylist of remote agents via settings; every remote response is **untrusted content** → goes through L1 (`InputSanitizer`/`SecurePromptBuilder`) and `ActionInterceptor`.
- [ ] `AuditLogger.log_event` for every inbound/outbound A2A task (without sensitive content).

**I7 — Settings + lifecycle:**
- [ ] `settings.a2a_enabled: bool = False`, `a2a_inbound_enabled`, `a2a_outbound_enabled`, allowlist, auth config.
- [ ] Integration with the composition root (Phase R): `build_runtime` can compose the `A2AToolProvider` and expose the inbound handler.

**I8 — Docs, examples, tests:**
- [ ] `docs/a2a.md` (expose prismal as A2A; consume remote agents).
- [ ] `examples/a2a_server.py`, `examples/a2a_remote_node.py`.
- [ ] Tests with a *fake* A2A server (no real network).

### 5.2 Out of Scope

- Implementing the HTTP server itself (mounted by `prismal-server`; the core provides handler + types).
- Federated agent registry/discovery (A2A catalog) — future.
- A2A *push notifications* / webhooks for long-running tasks — phase 2.
- Full DID issuance/management (lives in `agent-identity-governance`; consumed here).

### 5.3 Future Considerations

- A2A push notifications for long tasks.
- Agent catalog/registry (federated discovery).
- Modality negotiation (text/image/audio) with the multimodal layer.
- Delegation telemetry (latency, cost) → links to `cost-budget-governance`.

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-A2A-001 | A2A domain model (AgentCard, AgentSkill, Task, Message, Artifact) | `MUST` |
| RF-A2A-002 | `build_agent_card` derives skills from the agent registry + capability map | `MUST` |
| RF-A2A-003 | Inbound: JSON-RPC handler (`message/send`, `tasks/get`, `tasks/cancel`) + SSE | `MUST` |
| RF-A2A-004 | Inbound maps task → graph/subgraph → A2A artifacts | `MUST` |
| RF-A2A-005 | Outbound: `A2AClient` (discover card, send task, consume SSE, auth) | `MUST` |
| RF-A2A-006 | `A2AAgentNode.as_node()` wraps a remote agent as a graph node | `MUST` |
| RF-A2A-007 | `A2AToolProvider` conforms to `ToolProviderPort` (Phase Y) | `SHOULD` |
| RF-A2A-008 | OAuth2/mTLS auth + DID identity (in/out) | `MUST` |
| RF-A2A-009 | Allowlist/denylist of remote agents; remote responses go through L1–L5 | `MUST` |
| RF-A2A-010 | Auditing of every A2A task (inbound/outbound) | `MUST` |
| RF-A2A-011 | Settings `a2a_enabled`/inbound/outbound/allowlist/auth; Phase R integration | `MUST` |
| RF-A2A-012 | Fake A2A server for tests; docs + examples | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- Agent Card overhead: cached generation (once per startup/tenant).
- SSE streaming without blocking the event loop; reasonable backpressure.

### Security
- Every remote agent response is **untrusted** → L1 + `ActionInterceptor` before injecting into the state/prompt.
- Mandatory auth for inbound in production; mTLS/OAuth; reject Agent Cards without declared auth in strict mode.
- Default deny-all allowlist in strict outbound.
- Immutable delegation auditing (hash-chained, like the rest of `AuditLogger`).

### Compatibility
- Compliant with A2A v0.3.x (JSON-RPC over HTTP(S) + SSE, Agent Card at `/.well-known/agent-card.json`).
- `prismal/` PEP 420 namespace; A2A gated by `a2a_enabled=False` by default.
- `filterwarnings=error`: optional A2A/HTTP deps, deferred imports, `[a2a]` extra.

### Scalability
- Multi-tenant: Agent Card and auth per `org_id` (via Phase R); per-tenant delegation isolation.

### Observability
- Spans `prismal.a2a.inbound`, `prismal.a2a.outbound`; task/error/latency metrics per remote agent.

---

## 8. Constraints and Dependencies

| Dependency | Type | Use |
|---|---|---|
| Phase X (Extension Surface) | Prerequisite | `@prismal_node`, adapter-as-node |
| Phase Y (Tool Provider) | Recommended | `A2AToolProvider` conforms to `ToolProviderPort` |
| Phase R (Composition Root) | Recommended | Compose A2A into the runtime + multi-tenant |
| `specs/agent-identity-governance/` | Coupled | DID / agent identity |
| `mcp/connection.py` | Reference | Connection manager pattern (pool/allowlist/retry) |
| A2A lib / httpx / sse | New (optional) | A2A client/server; `[a2a]` extra |

---

## 9. User Stories

**US-A2A-001:** As an Ecosystem Integrator, I discover and invoke prismal via its Agent Card.
```
GET https://prismal.example.com/.well-known/agent-card.json
POST /a2a  {jsonrpc, method:"message/send", params:{...}}  -> SSE artifacts
```

**US-A2A-002:** As a Flow Author, I delegate to a remote agent as a node.
```python
from prismal.a2a import A2AAgentNode
node = A2AAgentNode("https://billing.acme/.well-known/agent-card.json").as_node(name="billing_agent")
builder.add_node("billing_agent", node)
```

**US-A2A-003:** As a Host, I expose prismal as A2A in the lifespan.
```python
from prismal.a2a import build_agent_card, A2AServerHandler
card = build_agent_card(settings, registry)
handler = A2AServerHandler(graph)   # mount in FastAPI: /.well-known/agent-card.json + /a2a
```

**US-A2A-004:** As a Security Lead, I restrict and audit.
```python
settings.a2a_outbound_allowlist = ["billing.acme", "*.trusted.org"]
# every delegation -> AuditLogger; remote responses -> InputSanitizer
```

---

## 10. Risks and Mitigations

| Risk | Prob. | Impact | Mitigation |
|---|---|---|---|
| Malicious remote agent response (prompt injection) | High | High | L1 `InputSanitizer`/`SecurePromptBuilder` + `ActionInterceptor` before injecting |
| Inbound endpoint exposed without auth | Medium | Critical | Mandatory auth in prod; strict mode rejects cards without auth; default disabled |
| Delegation to an unauthorized agent | Medium | High | Default deny-all allowlist in strict outbound |
| A2A spec evolves (v0.x) | Medium | Medium | Version pinning; versioned types; conformance tests |
| Long tasks block resources | Medium | Medium | Timeouts + cancel (`tasks/cancel`); push notifications (phase 2) |
| Uncontrolled delegation cost | Medium | Medium | Link to `cost-budget-governance` (per-run cap) |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| I1 — A2A types | 0.4 wk | Domain model |
| I2 — Agent Card | 0.4 wk | `build_agent_card` |
| I3 — Inbound handler | 1.0 wk | JSON-RPC + SSE + graph mapping |
| I4 — Outbound client/node | 1.0 wk | `A2AClient`, `A2AAgentNode`, connection manager |
| I5 — A2AToolProvider | 0.4 wk | conforms to `ToolProviderPort` |
| I6 — Security/identity | 0.6 wk | auth + DID + allowlist + audit |
| I7 — Settings + Phase R | 0.3 wk | toggles + composition |
| I8 — Docs + examples + tests | 0.6 wk | fake server + examples |
| Hardening | 0.5 wk | coverage, conformance, mypy/bandit |
| **Total** | **~5.2 wk** | Bidirectional A2A interop |

---

## 12. Definition of Done (Global for Phase I)

- [ ] A2A domain model compliant with v0.3.x.
- [ ] `build_agent_card` generates a valid card from the registry.
- [ ] Inbound: JSON-RPC handler + SSE maps tasks to the graph; mountable by `prismal-server`.
- [ ] Outbound: `A2AClient` + `A2AAgentNode` + connection manager (allowlist/retry).
- [ ] `A2AToolProvider` conforms to `ToolProviderPort`.
- [ ] Auth (OAuth/mTLS) + DID + allowlist + delegation auditing.
- [ ] Remote responses go through L1–L5.
- [ ] Settings `a2a_*` (default off); Phase R integration.
- [ ] `docs/a2a.md` + 2 examples + tests with a fake server; coverage ≥ 85%.
- [ ] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + Obsidian notes updated.
- [ ] PR merged with review.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Initial version — A2A / Agent Cards interoperability |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| Security Lead | — | | ☐ Pending |
