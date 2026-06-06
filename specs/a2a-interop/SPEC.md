# Prismal A2A Interoperability — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **Architecture** | `specs/a2a-interop/ARCHITECTURE.md` |
| **TASKS** | `specs/a2a-interop/TASKS.md` |
| **Conformidad** | A2A Protocol v0.3.x (JSON-RPC over HTTP(S) + SSE; Agent Card en `/.well-known/agent-card.json`) |

---

## Convenciones

- `from __future__ import annotations`.
- Modelos con **Pydantic v2** (validación de I/O — cierra parcialmente la brecha de type-safety).
- Imports de deps A2A/HTTP **diferidos**; extra `[a2a]`.
- Async para I/O de red; el handler inbound es async-streaming (SSE).
- Todo contenido remoto pasa por `prismal.security` antes de tocar `AgentState`.

---

## Resumen de módulos

| Módulo | Estado | Contenido |
|---|---|---|
| `prismal/a2a/types.py` | NUEVO | `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart` |
| `prismal/a2a/card.py` | NUEVO | `build_agent_card` |
| `prismal/a2a/server.py` | NUEVO | `A2AServerHandler` |
| `prismal/a2a/client.py` | NUEVO | `A2AClient`, `A2AConnectionManager`, `A2AAgentNode` |
| `prismal/a2a/provider.py` | NUEVO | `A2AToolProvider` |
| `prismal/core/config.py` | MODIFICADO | settings `a2a_*` |
| `prismal/core/exceptions.py` | MODIFICADO | `A2AError`, `A2AAgentUnavailable` |

---

## SPEC-A2A-001: Tipos de dominio (`types.py`)

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
    url: str                        # endpoint A2A
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
    registry: Any,                  # registro de agentes/subgrafos
    *,
    org_id: str | None = None,
) -> AgentCard:
    """Deriva el Agent Card del registro:
    - skills: una AgentSkill por capacidad/subgrafo en settings.a2a_published_skills
      (allowlist; por defecto un subconjunto de alto nivel, p.ej. 'research', 'coding').
    - url = settings.a2a_base_url (+ tenant si org_id).
    - security_schemes desde settings.a2a_auth.
    - did desde el proveedor de identidad (agent-identity-governance) si disponible.
    - capabilities.streaming = True ; output_modes incluye media si multimodal_enabled.
    Resultado cacheado por org_id.
    """
```

---

## SPEC-A2A-003: Inbound — `A2AServerHandler` (`server.py`)

```python
class A2AServerHandler:
    def __init__(self, graph: CompiledStateGraph, *, settings: Settings | None = None) -> None: ...

    async def handle_rpc(self, request: dict, *, auth_ctx: "AuthContext") -> "RpcResult":
        """Despacha JSON-RPC A2A:
          - "message/send"  -> _run_task(message, skill_id) -> stream SSE de A2AArtifact
          - "tasks/get"     -> estado de A2ATask por id
          - "tasks/cancel"  -> cancela la ejecución del grafo (task_id == thread_id)
        Auth verificada por el host antes de llamar; en modo estricto, sin auth -> error.
        """

    async def _run_task(self, message: A2AMessage, skill_id: str | None) -> AsyncIterator[A2AArtifact]:
        """1. sanitize(message) via InputSanitizer/SecurePromptBuilder
           2. invoke graph (o subgrafo skill_id) con thread_id = task_id
           3. yield A2AArtifact por cada salida relevante (streaming)
           4. AuditLogger.log_event('a2a.inbound', {...})
        """
```

El **endpoint HTTP** (`GET /.well-known/agent-card.json`, `POST /a2a`) lo monta `prismal-server`; este handler es agnóstico del framework web.

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
        """Devuelve async (state) -> state_update, decorado con @prismal_node:
           - construye A2AMessage desde state['messages'] (último turno)
           - send_task -> consume artifacts -> sanitize -> append a state['messages']
           - errores -> state_update con error=True (no rompe el grafo)
           - audit de la delegación.
        """
```

---

## SPEC-A2A-005: `A2AToolProvider` (`provider.py`) — conforma Fase Y

```python
class A2AToolProvider:
    def __init__(self, agents: list[str | AgentCard], *, manager: A2AConnectionManager | None = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Expone skills de agentes remotos allowlisted como BaseTool:
           name = f"a2a__{remote_agent}__{skill_id}"; ainvoke -> A2AClient.send_task (sanitizado).
           Conforma ToolProviderPort (Fase Y) -> componible en CompositeToolProvider.
           Captura excepciones -> []. agent_name/capabilities filtran qué skills se ofrecen.
        """
```

---

## SPEC-A2A-006: Auth e identidad (`client.py` / integración)

```python
class A2AAuth(BaseModel):
    scheme: Literal["none", "oauth2_client_credentials", "mtls", "bearer"]
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None    # secreto: nunca se loguea
    cert_path: str | None = None
    key_path: str | None = None
    did: str | None = None              # identidad propia (agent-identity-governance)
```

Inbound: el host valida la auth del llamante y pasa un `AuthContext` al handler. Outbound: `A2AClient` obtiene/renueva el token según `A2AAuth`.

---

## SPEC-A2A-007: Settings (`core/config.py`)

```python
a2a_enabled: bool = False
a2a_inbound_enabled: bool = False
a2a_outbound_enabled: bool = False
a2a_base_url: str | None = None                 # endpoint público para el card (inbound)
a2a_published_skills: list[str] = []            # allowlist de skills a publicar
a2a_outbound_allowlist: list[str] = []          # hosts/patrones permitidos (outbound)
a2a_strict: bool = True                         # exige auth; deny-all si allowlist vacía
# a2a_auth: estructura de credenciales (ver A2AAuth)
```

---

## SPEC-A2A-008: Excepciones (`core/exceptions.py`)

```python
class A2AError(PrismalError):
    """Fallo genérico de interop A2A."""

class A2AAgentUnavailable(A2AError):
    """Agente remoto no alcanzable o fuera de allowlist."""
    def __init__(self, agent: str, reason: str) -> None:
        super().__init__(f"A2A agent '{agent}' unavailable: {reason}")
```

---

## SPEC-A2A-009: Integración con el Composition Root (Fase R)

```python
# en build_runtime(...) (specs/composition-root), cuando settings.a2a_enabled:
#   - si a2a_outbound_enabled: añadir A2AToolProvider(...) al CompositeToolProvider
#   - si a2a_inbound_enabled: construir A2AServerHandler(graph) y exponerlo en RuntimeContext
#   - card por org_id (multi-tenant)
```

`RuntimeContext` gana (opcional) `a2a_handler: A2AServerHandler | None` que `prismal-server` monta en sus rutas.

---

## Contrato del Host (`prismal-server`)

```python
from prismal.a2a import build_agent_card, A2AServerHandler

card = build_agent_card(settings, registry)          # GET /.well-known/agent-card.json -> card.model_dump()
handler = A2AServerHandler(graph, settings=settings)  # POST /a2a -> await handler.handle_rpc(req, auth_ctx=...)
# auth del llamante validada por el server ANTES de invocar el handler.
```

### Outbound como nodo
```python
from prismal.a2a import A2AAgentNode
node = A2AAgentNode("https://billing.acme/.well-known/agent-card.json", skill_id="create_invoice").as_node(name="billing_agent")
builder.add_node("billing_agent", node)
```

### Outbound como tools (vía Fase Y)
```python
from prismal.a2a import A2AToolProvider
provider = CompositeToolProvider([McpToolProvider(...), A2AToolProvider(["https://billing.acme/.well-known/agent-card.json"]), StubToolProvider()])
set_tool_provider(provider)
```

---

## Compatibilidad y Versionado

- Conforme a **A2A v0.3.x**; `protocol_version` declarado; tests de conformidad.
- API pública (`A2AClient`, `A2AAgentNode`, `A2AToolProvider`, `build_agent_card`, `A2AServerHandler`) versionada (SemVer).
- **Opt-in:** `a2a_enabled=False` y extra `[a2a]` → cero impacto si no se usa.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Especificación inicial — tipos, card, server, client, provider, auth |
