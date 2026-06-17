# A2A (Agent2Agent) Interoperability — Phase I

`prismal.a2a` lets prismal participate in multi-vendor agent networks using the
**A2A protocol** (Agent2Agent, Linux Foundation, Apache 2.0): JSON-RPC over
HTTP(S) + SSE, an **Agent Card** at `/.well-known/agent-card.json`, and W3C DID
identity. A2A is the *agent*-level complement to MCP (the *tool* level) — prismal
keeps `prismal.mcp` for tools and adds `prismal.a2a` for agents.

It is **opt-in and additive**: with `a2a_enabled=False` (the default) the core
behaves identically and the compiled supervisor graph is unchanged. HTTP/SSE
deps ship under the `[a2a]` extra (`pip install "prismal-ai[a2a]"`), and their
imports are deferred.

```
            EXTERNAL A2A WORLD (Google ADK / MS Agent Framework / ERP / …)
   ▲ inbound (others call prismal)            │ outbound (prismal calls others)
   GET /.well-known/agent-card.json           │  discover remote card
   POST /a2a  (JSON-RPC + SSE)                ▼
┌──────────────────────────────────────────────────────────────┐
│ prismal-server (FastAPI) — mounts the A2A HTTP endpoint        │
│   /.well-known/agent-card.json → build_agent_card(...)         │
│   /a2a → A2AServerHandler(graph).handle_rpc / stream_rpc       │
└───────────────┬───────────────────────────────┬──────────────┘
   prismal/a2a/server.py                 prismal/a2a/{client,provider}.py
   (L1–L5 security + audit applied on both directions)
```

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `a2a_enabled` | `False` | Master opt-in. |
| `a2a_inbound_enabled` | `False` | Expose prismal as an A2A agent. |
| `a2a_outbound_enabled` | `False` | Let prismal delegate to remote agents. |
| `a2a_base_url` | `None` | Public endpoint advertised in the Agent Card. |
| `a2a_published_skills` | `[]` | Allowlist of skills to publish (empty = all registry entries). |
| `a2a_outbound_allowlist` | `[]` | Allowed remote hosts (fnmatch, e.g. `*.trusted.org`). |
| `a2a_strict` | `True` | Require auth; deny-all outbound when the allowlist is empty. |

## Inbound — expose prismal as an A2A agent

The core provides the **handler and the card**; the host (`prismal-server`)
mounts the HTTP routes and validates the caller's auth before dispatching.

```python
from prismal.a2a import build_agent_card, A2AServerHandler
from prismal.a2a.server import AuthContext
from prismal.agents.graph import get_async_compiled_graph
from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP
from prismal.core.config import get_settings

settings = get_settings()
graph = await get_async_compiled_graph()

# GET /.well-known/agent-card.json
card = build_agent_card(settings, DEFAULT_CAPABILITY_MAP)
card_json = card.model_dump(by_alias=True, exclude_none=True)

# POST /a2a  (the host validates auth → builds an AuthContext → dispatches)
handler = A2AServerHandler(graph, settings=settings)
response = await handler.handle_rpc(request_body, auth_ctx=AuthContext(authenticated=True))

# Streaming variant (text/event-stream):
async for sse_line in handler.stream_rpc(request_body, auth_ctx=ctx):
    yield sse_line
```

Inbound message text is **untrusted**: it is sanitized before it reaches the
graph, the graph runs with `thread_id = task_id`, and every task is audited
(`a2a.inbound`) without content. In strict mode an unauthenticated call returns a
JSON-RPC `-32001` error.

## Outbound — delegate to a remote agent

### As a graph node

```python
from prismal.a2a import A2AAgentNode

node = A2AAgentNode(
    "https://billing.acme/.well-known/agent-card.json",
    skill_id="create_invoice",
).as_node(name="billing_agent")
builder.add_node("billing_agent", node)
```

The node builds an A2A task from the last user turn, streams the artifacts back,
**sanitizes** them, and merges the answer into `state["messages"]`. A remote
failure yields `metadata.a2a.error = True` instead of breaking the graph.

### As tools (via the Phase Y `ToolProviderPort`)

```python
from prismal.a2a import A2AToolProvider
from prismal.agents.extension import CompositeToolProvider, StubToolProvider
from prismal.agents.tool_registry import set_tool_provider

a2a = A2AToolProvider(["https://billing.acme/.well-known/agent-card.json"])
await a2a.prepare()  # discover URL-only agents
set_tool_provider(CompositeToolProvider([a2a, StubToolProvider()]))
```

Each remote skill is exposed as a tool named `a2a__{agent}__{skill}`.

## Composition root (multi-tenant)

```python
from prismal.composition import build_runtime

ctx = await build_runtime(
    settings,
    org_id="acme",
    graph=graph,                 # enables ctx.a2a_handler when a2a_inbound_enabled
    a2a_agents=[remote_card],    # composed into the tool provider when a2a_outbound_enabled
)
# ctx.a2a_handler is an A2AServerHandler the host mounts; outbound A2A tools are
# already in ctx.tool_provider.
```

## Auth & identity

`A2AAuth` supports `none`, `bearer`, `oauth2_client_credentials`, and `mtls`
(secrets held as `SecretStr`). The Agent Card embeds the agent's DID
(`did:web:<domain>` from the Phase IDN identity settings, or an explicit
override), linking A2A to `docs/identity.md`.

## Security model

Every remote response crosses a trust boundary and is treated as **untrusted**:
`InputSanitizer` runs over each artifact before it touches state, the outbound
allowlist is enforced (deny-all in strict), and all delegations are audited. See
the L1–L5 layers in `prismal/security/`.
