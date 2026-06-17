# Prismal A2A Interoperability — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **Architecture** | `specs/a2a-interop/ARCHITECTURE.md` |
| **TASKS** | `specs/a2a-interop/TASKS.md` |
| **Conformance** | A2A Protocol v0.3.x (JSON-RPC over HTTP(S) + SSE; Agent Card at `/.well-known/agent-card.json`) |

---

## Conventions

- `from __future__ import annotations`.
- Models with **Pydantic v2** (I/O validation — partially closes the type-safety gap).
- A2A/HTTP dep imports **deferred**; `[a2a]` extra.
- Async for network I/O; the inbound handler is async-streaming (SSE).
- All remote content passes through `prismal.security` before touching `AgentState`.

---

## Module summary

| Module | Status | Content |
|---|---|---|
| `prismal/a2a/types.py` | NEW | `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart` |
| `prismal/a2a/card.py` | NEW | `build_agent_card` |
| `prismal/a2a/server.py` | NEW | `A2AServerHandler` |
| `prismal/a2a/client.py` | NEW | `A2AClient`, `A2AConnectionManager`, `A2AAgentNode` |
| `prismal/a2a/provider.py` | NEW | `A2AToolProvider` |
| `prismal/core/config.py` | MODIFIED | `a2a_*` settings |
| `prismal/core/exceptions.py` | MODIFIED | `A2AError`, `A2AAgentUnavailable` |

---

## SPEC-A2A-001: Domain types (`types.py`)

```python
class A2APart(BaseModel):          # text | file | data
    kind: Literal["text", "file", "data"]
    text: str | None = None
    data: dict[str, Any] | None = None
    file: dict[str, Any] | None = None

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    input_modes: list[str] = ["text/plain"]
    output_modes: list[str] = ["text/plain"]

class AgentCard(BaseModel):
    name: str
    description: str
    url: str                        # A2A endpoint
    version: str
    protocol_version: str = "0.3.0"
    skills: list[AgentSkill]
    capabilities: dict[str, bool] = {"streaming": True}
    security_schemes: dict[str, Any] = {}    # OAuth/mTLS
    provider: dict[str, str] | None = None
    did: str | None = None          # W3C DID (agent-identity-governance)

class A2AMessage(BaseModel):
    role: Literal["user", "agent"]
    parts: list[A2APart]
    message_id: str

class A2AArtifact(BaseModel):
    artifact_id: str
    parts: list[A2APart]

class A2ATask(BaseModel):
    id: str
    status: Literal["submitted", "working", "input-required", "completed", "failed", "canceled"]
    history: list[A2AMessage] = []
    artifacts: list[A2AArtifact] = []
```

---

## SPEC-A2A-002: `build_agent_card` (`card.py`)

```python
def build_agent_card(
    settings: Settings,
    registry: Any,                  # agent/subgraph registry
    *,
    org_id: str | None = None,
) -> AgentCard:
    """Derives the Agent Card from the registry:
    - skills: one AgentSkill per capability/subgraph in settings.a2a_published_skills
      (allowlist; by default a high-level subset, e.g. 'research', 'coding').
    - url = settings.a2a_base_url (+ tenant if org_id).
    - security_schemes from settings.a2a_auth.
    - did from the identity provider (agent-identity-governance) if available.
    - capabilities.streaming = True ; output_modes includes media if multimodal_enabled.
    Result cached by org_id.
    """
```

---

## SPEC-A2A-003: Inbound — `A2AServerHandler` (`server.py`)

```python
class A2AServerHandler:
    def __init__(self, graph: CompiledStateGraph, *, settings: Settings | None = None) -> None: ...

    async def handle_rpc(self, request: dict, *, auth_ctx: "AuthContext") -> "RpcResult":
        """Dispatches A2A JSON-RPC:
          - "message/send"  -> _run_task(message, skill_id) -> SSE stream of A2AArtifact
          - "tasks/get"     -> A2ATask status by id
          - "tasks/cancel"  -> cancels the graph execution (task_id == thread_id)
        Auth verified by the host before calling; in strict mode, no auth -> error.
        """

    async def _run_task(self, message: A2AMessage, skill_id: str | None) -> AsyncIterator[A2AArtifact]:
        """1. sanitize(message) via InputSanitizer/SecurePromptBuilder
           2. invoke graph (or subgraph skill_id) with thread_id = task_id
           3. yield A2AArtifact for each relevant output (streaming)
           4. AuditLogger.log_event('a2a.inbound', {...})
        """
```

The **HTTP endpoint** (`GET /.well-known/agent-card.json`, `POST /a2a`) is mounted by `prismal-server`; this handler is agnostic of the web framework.

---

## SPEC-A2A-004: Outbound — `A2AClient` / `A2AAgentNode` (`client.py`)

```python
class A2AClient:
    def __init__(self, card_or_url: str | AgentCard, *, auth: "A2AAuth | None" = None) -> None: ...
    async def discover(self) -> AgentCard: ...              # GET /.well-known/agent-card.json
    async def send_task(self, message: A2AMessage, *, skill_id: str | None = None
                        ) -> AsyncIterator[A2AArtifact]: ...  # JSON-RPC message/send + SSE
    async def cancel(self, task_id: str) -> None: ...

class A2AConnectionManager:
    def __init__(self, *, allowlist: list[str], settings: Settings | None = None) -> None: ...
    async def get_client(self, card_url: str) -> A2AClient: ...   # allowlist + pool + retry

class A2AAgentNode:
    def __init__(self, card_or_url: str | AgentCard, *, client: A2AClient | None = None,
                 skill_id: str | None = None) -> None: ...
    def as_node(self, *, name: str, capabilities: list[str] | None = None) -> "PrismalNode":
        """Returns async (state) -> state_update, decorated with @prismal_node:
           - builds A2AMessage from state['messages'] (last turn)
           - send_task -> consume artifacts -> sanitize -> append to state['messages']
           - errors -> state_update with error=True (does not break the graph)
           - audit of the delegation.
        """
```

---

## SPEC-A2A-005: `A2AToolProvider` (`provider.py`) — conforms to Phase Y

```python
class A2AToolProvider:
    def __init__(self, agents: list[str | AgentCard], *, manager: A2AConnectionManager | None = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Exposes allowlisted remote agents' skills as BaseTool:
           name = f"a2a__{remote_agent}__{skill_id}"; ainvoke -> A2AClient.send_task (sanitized).
           Conforms to ToolProviderPort (Phase Y) -> composable in CompositeToolProvider.
           Captures exceptions -> []. agent_name/capabilities filter which skills are offered.
        """
```

---

## SPEC-A2A-006: Auth and identity (`client.py` / integration)

```python
class A2AAuth(BaseModel):
    scheme: Literal["none", "oauth2_client_credentials", "mtls", "bearer"]
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None    # secret: never logged
    cert_path: str | None = None
    key_path: str | None = None
    did: str | None = None              # own identity (agent-identity-governance)
```

Inbound: the host validates the caller's auth and passes an `AuthContext` to the handler. Outbound: `A2AClient` obtains/renews the token according to `A2AAuth`.

---

## SPEC-A2A-007: Settings (`core/config.py`)

```python
a2a_enabled: bool = False
a2a_inbound_enabled: bool = False
a2a_outbound_enabled: bool = False
a2a_base_url: str | None = None                 # public endpoint for the card (inbound)
a2a_published_skills: list[str] = []            # allowlist of skills to publish
a2a_outbound_allowlist: list[str] = []          # allowed hosts/patterns (outbound)
a2a_strict: bool = True                         # requires auth; deny-all if allowlist empty
# a2a_auth: credentials structure (see A2AAuth)
```

---

## SPEC-A2A-008: Exceptions (`core/exceptions.py`)

```python
class A2AError(PrismalError):
    """Generic A2A interop failure."""

class A2AAgentUnavailable(A2AError):
    """Remote agent unreachable or outside the allowlist."""
    def __init__(self, agent: str, reason: str) -> None:
        super().__init__(f"A2A agent '{agent}' unavailable: {reason}")
```

---

## SPEC-A2A-009: Integration with the Composition Root (Phase R)

```python
# in build_runtime(...) (specs/composition-root), when settings.a2a_enabled:
#   - if a2a_outbound_enabled: add A2AToolProvider(...) to the CompositeToolProvider
#   - if a2a_inbound_enabled: build A2AServerHandler(graph) and expose it in RuntimeContext
#   - card per org_id (multi-tenant)
```

`RuntimeContext` gains (optionally) `a2a_handler: A2AServerHandler | None` that `prismal-server` mounts in its routes.

---

## Host Contract (`prismal-server`)

```python
from prismal.a2a import build_agent_card, A2AServerHandler

card = build_agent_card(settings, registry)          # GET /.well-known/agent-card.json -> card.model_dump()
handler = A2AServerHandler(graph, settings=settings)  # POST /a2a -> await handler.handle_rpc(req, auth_ctx=...)
# caller auth validated by the server BEFORE invoking the handler.
```

### Outbound as a node
```python
from prismal.a2a import A2AAgentNode
node = A2AAgentNode("https://billing.acme/.well-known/agent-card.json", skill_id="create_invoice").as_node(name="billing_agent")
builder.add_node("billing_agent", node)
```

### Outbound as tools (via Phase Y)
```python
from prismal.a2a import A2AToolProvider
provider = CompositeToolProvider([McpToolProvider(...), A2AToolProvider(["https://billing.acme/.well-known/agent-card.json"]), StubToolProvider()])
set_tool_provider(provider)
```

---

## Compatibility and Versioning

- Compliant with **A2A v0.3.x**; `protocol_version` declared; conformance tests.
- Public API (`A2AClient`, `A2AAgentNode`, `A2AToolProvider`, `build_agent_card`, `A2AServerHandler`) versioned (SemVer).
- **Opt-in:** `a2a_enabled=False` and `[a2a]` extra → zero impact if unused.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Initial specification — types, card, server, client, provider, auth |
