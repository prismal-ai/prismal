# Prismal Protocol Upgrades 2026 — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Target package version** | `3.17.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/protocol-upgrades-2026/PLAN.md` |
| **Architecture** | `specs/protocol-upgrades-2026/ARCHITECTURE.md` |
| **TASKS** | `specs/protocol-upgrades-2026/TASKS.md` |

---

## Conventions

- `from __future__ import annotations`; Pydantic v2 wire models (`alias_generator=to_camel`,
  `populate_by_name=True`, `model_dump(by_alias=True, exclude_none=True)`); frozen dataclasses inside.
- Protocol names (methods, events, headers, versions) live in one constants module per protocol, filled
  from `ADR-PRT-001` at PRT0; every **[verify]** name below is provisional until then.
- HTTP imports deferred behind `[a2a]`/`[mcp]`; `prismal/events/` imports only `pydantic` + stdlib.
- Hot-path helpers are sync and **must not raise** (degrade to legacy + count); security paths
  (OAuth, tenant routing, elicitation) are **fail-closed**.
- Areas: `A2A`, `OAU`, `MCP`, `MTC` (MCP cache), `MTK` (MCP tasks), `AGU`, `USG`, `CFG`, `ERR`, `OTEL`, `GRD`.

## SPEC-PRT-A2A-001: v1 wire types (`a2a/v1/types.py`)

```python
A2A_V1_PROTOCOL_VERSION: Final = "1.0.0"   # PRT0 pin ("1.0.1" if the patch changes the wire) [verify]; all models extra="allow"

class TaskState(StrEnum): ...   # submitted|working|input-required|auth-required|completed|canceled|failed|rejected [verify]

class PartV1(A2AModelV1):       # discriminated by kind [verify]
    kind: Literal["text", "file", "data"]; text: str | None = None; file: FileRefV1 | None = None
    data: dict[str, Any] | None = None; metadata: dict[str, Any] | None = None

class MessageV1(A2AModelV1):
    message_id: str; role: Literal["user", "agent"]; parts: list[PartV1]
    context_id: str | None = None; task_id: str | None = None
    tenant: str | None = None          # v1 multi-tenancy [verify field name/location]

class TaskV1(A2AModelV1):
    id: str; context_id: str; status: TaskStatusV1     # state + timestamp + optional message
    artifacts: list[ArtifactV1] = []; history: list[MessageV1] | None = None
    tenant: str | None = None

class ListTasksParams(A2AModelV1):
    context_id: str | None = None; status: TaskState | None = None
    page_size: int = Field(default=50, ge=1, le=200); page_token: str | None = None
    tenant: str | None = None
class ListTasksResult(A2AModelV1): tasks: list[TaskV1]; next_page_token: str | None = None
class AgentInterfaceV1(A2AModelV1): url: str; transport: Literal["JSONRPC", "GRPC", "HTTP+JSON"]  # [verify]
class OAuthFlowsV1(A2AModelV1):  # authorization_code carries pkce_required: bool
    authorization_code: OAuthAuthCodeFlowV1 | None = None; device_code: OAuthDeviceCodeFlowV1 | None = None
    client_credentials: OAuthClientCredentialsFlowV1 | None = None

class AgentCardV1(A2AModelV1):
    protocol_version: str = A2A_V1_PROTOCOL_VERSION
    name: str; description: str; version: str
    supported_interfaces: list[AgentInterfaceV1]   # [verify] vs. legacy top-level `url`
    skills: list[AgentSkillV1]
    capabilities: AgentCapabilitiesV1              # streaming, push_notifications, multi_tenant
    security_schemes: dict[str, SecuritySchemeV1] = {}; security: list[dict[str, list[str]]] = []
    default_input_modes: list[str]; default_output_modes: list[str]
```

## SPEC-PRT-A2A-002: Version negotiation (`a2a/negotiation.py`)

```python
A2AProtocolVersion = Literal["0.3", "1.0"]

def negotiate_inbound(
    payload: Mapping[str, Any], headers: Mapping[str, str], *, settings: Settings
) -> A2AProtocolVersion | None:
    """Precedence: (1) explicit version header (`A2A-Version` [verify]) whose major is 1 or 0.3;
    (2) JSON-RPC method name membership in V1_METHODS vs LEGACY_METHODS; (3) settings
    .a2a_protocol_version. Returns None when the request is v0.3 and not a2a_accept_legacy.
    Never raises."""

def select_outbound(card: Mapping[str, Any], *, preferred: A2AProtocolVersion | None) -> A2AProtocolVersion:
    """Read card['protocolVersion']; major 1 ⇒ '1.0', '0.3.x' ⇒ '0.3'; missing/unparseable ⇒ '0.3'.
    `preferred` wins only when the card advertises that version. Never raises."""
```

A refused legacy request gets JSON-RPC `-32009` "unsupported protocol version" **[verify code]**.

## SPEC-PRT-A2A-003: Agent Card (`a2a/card.py`, extend)

```python
def build_agent_card(
    settings: Settings, registry: Mapping[str, Any], *, org_id: str | None = None,
    did: str | None = None,
    protocol_version: A2AProtocolVersion | None = None,   # NEW; None ⇒ settings.a2a_protocol_version
) -> AgentCard | AgentCardV1: ...
```

`"0.3"` ⇒ today's `AgentCard` (snapshot). `"1.0"` ⇒ `AgentCardV1`: interface URLs tenant-scoped like
v0.3, `multi_tenant = org_id is not None`, schemes from `a2a_inbound_auth_schemes`. Cache key adds version.

## SPEC-PRT-A2A-004: Dual-stack inbound handler (`a2a/server.py`, extend)

```python
class A2AServerHandler:
    async def handle_rpc(self, payload: Mapping[str, Any], *, auth: AuthContext | None,
                         headers: Mapping[str, str] | None = None,   # NEW; None ⇒ legacy behaviour
                         org_id: str | None = None) -> dict[str, Any]: ...   # NEW: host tenant
    def stream_rpc(self, payload: Mapping[str, Any], *, auth: AuthContext | None,
                   headers: Mapping[str, str] | None = None,
                   org_id: str | None = None) -> AsyncIterator[str]: ...   # SSE `data:` lines
```

- v1 requests are decoded by `convert.py`, run by the unchanged `_run_task` (sanitize → graph →
  artifact → audit) and re-encoded as v1.
- Tenant rule (fail-closed): IF a v1 request's `tenant` is set AND differs from the host-resolved
  `org_id`, THEN THE SYSTEM SHALL reject with JSON-RPC `-32001` and audit `a2a.inbound.tenant_mismatch`.
- `tasks/list` (v1 only) reads `A2ATaskStorePort.list(org_id=..., params=...)`; results are scoped
  to the caller's tenant and, WHERE `a2a_strict` is enabled, to tasks created by the same principal.

## SPEC-PRT-A2A-005: Task store (`a2a/task_store.py`)

```python
@runtime_checkable
class A2ATaskStorePort(Protocol):
    def put(self, task: TaskRecord) -> None: ...
    def get(self, task_id: str, *, org_id: str | None) -> TaskRecord | None: ...
    def list(self, *, org_id: str | None, params: ListTasksParams,
             principal: str | None = None) -> tuple[list[TaskRecord], str | None]: ...

@dataclass(frozen=True)
class TaskRecord:
    task_id: str; context_id: str; org_id: str | None; principal: str | None; state: TaskState
    created_at: datetime; updated_at: datetime; artifact_ids: tuple[str, ...] = ()
```

`InMemoryA2ATaskStore` wraps the registry the handler already uses for `tasks/get` (name confirmed
at PRT0-04); page tokens are opaque base64 of `(updated_at, task_id)`; ordering newest-first.

## SPEC-PRT-A2A-006: Outbound client (`a2a/client.py`, extend)

```python
class A2AClient:
    def __init__(self, base_url: str, *, auth: A2AAuth | None = None,
                 protocol: Literal["auto", "0.3", "1.0"] = "auto", tenant: str | None = None,   # NEW
                 prompter: AuthorizationPrompter | None = None, **existing_kwargs: Any) -> None: ...
    async def list_tasks(self, params: ListTasksParams) -> ListTasksResult: ...   # v1 only
```

`protocol="auto"` calls `select_outbound(card)` once per discovered card (cached with the card).

## SPEC-PRT-OAU-001: OAuth flows (`a2a/oauth.py`)

```python
@runtime_checkable
class AuthorizationPrompter(Protocol):
    async def authorize_url(self, url: str, *, state: str) -> str: ...        # returns auth code
    async def device_code(self, verification_uri: str, user_code: str,
                          expires_in_s: int) -> None: ...                    # display only

class PkceAuthorizationCodeFlow:     # access_token(): S256 only; refresh via vault-stored token
    def __init__(self, *, token_url: str, authorize_url: str, client_id: str, redirect_uri: str,
                 scopes: Sequence[str], vault_key: str, prompter: AuthorizationPrompter) -> None: ...
    async def access_token(self) -> SecretStr: ...
class DeviceCodeFlow:                # polls token_url honouring interval/slow_down until max_wait_s
    def __init__(self, *, device_authorization_url: str, token_url: str, client_id: str,
                 scopes: Sequence[str], vault_key: str, prompter: AuthorizationPrompter,
                 max_wait_s: int = 600) -> None: ...
    async def access_token(self) -> SecretStr: ...
```

Rules: PKCE method `S256` only (`plain` rejected); `state` and `code_verifier` from `secrets`; refresh
tokens persisted only through `CredentialVault` under `vault_key@org_id`, never in `os.environ`,
logs or audit (audit records flow name + token hash prefix). IF no `prompter` is injected and an
interactive grant is required, THEN THE SYSTEM SHALL raise `A2AOAuthError` (no downgrade).

## SPEC-PRT-MCP-001: Protocol adapter (`mcp/protocol.py`)

```python
McpProtocolMode = Literal["auto", "legacy", "stateless"]

@dataclass(frozen=True)
class ServerDiscovery:
    protocol_version: str; stateless: bool   # e.g. "2026-07-28" | "2025-11-25" | "2025-06-18"
    capabilities: Mapping[str, Any]; extensions: frozenset[str]   # {"tasks"} [verify id]
    server_name: str; server_version: str

@runtime_checkable
class McpProtocolAdapter(Protocol):
    @property
    def discovery(self) -> ServerDiscovery: ...
    async def list_tools(self) -> ToolListPage: ...          # all pages; carries ttl_ms, cache_scope
    async def call_tool(self, name: str, arguments: Mapping[str, Any], *,
                        allow_task: bool = False) -> ToolCallOutcome: ...   # result | McpTaskHandle
    async def get_task(self, task_id: str) -> McpTaskStatus: ...
    async def cancel_task(self, task_id: str) -> None: ...; async def aclose(self) -> None: ...

class LegacySessionAdapter: ...        # wraps today's mcp.ClientSession (initialize + session)
class StatelessHttpAdapter: ...        # 2026-07-28: no initialize; server/discover; per-request headers

async def select_adapter(server: McpServerConfig, *, settings: Settings,
                         input_handler: InputRequestHandler | None = None) -> McpProtocolAdapter:
    """mode=legacy ⇒ LegacySessionAdapter (today's path, byte-for-byte). mode=stateless ⇒
    StatelessHttpAdapter or McpDiscoveryError. mode=auto ⇒ try `server/discover`; JSON-RPC -32601
    or HTTP 404/405 ⇒ legacy; success with stateless=True ⇒ stateless. Result cached per server for
    mcp_tool_cache_default_ttl_s. stdio servers are always legacy."""
```

## SPEC-PRT-MTC-001: TTL tool-list cache (`mcp/tool_cache.py`)

```python
CacheScope = Literal["shared", "principal"]   # mapped from MCP cacheScope values [verify]

@dataclass(frozen=True)
class CachedToolList:
    server: str; scope_key: str                # server | f"{server}:{org_id}:{principal}"
    tools: tuple[Mapping[str, Any], ...]       # sorted by name; content_hash = xxhash64(canonical JSON)
    content_hash: str; fetched_at: float; expires_at: float

class McpToolListCache:
    def __init__(self, *, settings: Settings | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def get(self, server: str, *, org_id: str | None, principal: str | None) -> CachedToolList | None: ...
    def put(self, server: str, page: ToolListPage, *, org_id: str | None,
            principal: str | None) -> CachedToolList: ...
    def invalidate(self, server: str | None = None) -> int: ...
```

- TTL = `min(ttl_ms/1000, max_ttl_s)`; absent `ttlMs` ⇒ default TTL; `ttlMs=0` ⇒ never cached; LRU bounded.
- **Stability rule (feeds TKE2/TKE3):** on refresh, IF the new `content_hash` equals the cached one,
  THEN THE SYSTEM SHALL return the *previous* `CachedToolList` object (identical serialized bytes);
  the tool block observed by agents changes only when TTL expired **and** content hash changed.
- Principal-scoped lists are never served across tenants or principals (security, fail-closed).

## SPEC-PRT-MTK-001: Tasks extension (`mcp/tasks.py`)

```python
@dataclass(frozen=True)
class McpTaskHandle:
    server: str; task_id: str; created_at: float; poll_hint_ms: int | None

class McpTaskPoller:
    def __init__(self, adapter: McpProtocolAdapter, *, settings: Settings | None = None) -> None: ...
    async def wait(self, handle: McpTaskHandle, *,
                   cancel_event: asyncio.Event | None = None) -> ToolCallOutcome:
        """Poll tasks/get, exponential backoff (mcp_task_poll_interval_s → _max_interval_s, honour
        poll_hint_ms) until terminal. Timeout ⇒ cancel_task() + McpTaskTimeout; run cancellation ⇒
        cancel_task() best-effort; wall-clock reported to BudgetGuard."""
```

WHERE `mcp_tasks_enabled` is False, THE SYSTEM SHALL call tools with `allow_task=False` and treat a
task-only tool as unavailable (filtered from the list, counted).

## SPEC-PRT-MTK-002: Multi-round-trip input requests (`mcp/input_requests.py`)

```python
@runtime_checkable
class InputRequestHandler(Protocol):
    async def handle(self, request: McpInputRequest, *, server: str,
                     org_id: str | None) -> McpInputResponse: ...   # elicitation (form|url)

class DeclineAllInputHandler: ...   # default: action="decline"; audits hash of the prompt
```

Elicitation text and URLs are untrusted: L1-sanitized before reaching any handler; URL-mode
elicitation is never opened by the engine; sampling and roots requests are declined in stateless
mode (deprecated upstream) and answered only by the legacy adapter as today.

## SPEC-PRT-AGU-001: AG-UI models (`events/agui.py`)

```python
AGUI_PROTOCOL_VERSION: Final = "1.0.0"   # [verify]

class AgUiEventType(StrEnum):   # value == name; all 31 pinned at PRT0 [verify exact names]
    ...  # RUN_STARTED RUN_FINISHED RUN_ERROR STEP_STARTED STEP_FINISHED TEXT_MESSAGE_START
         # TEXT_MESSAGE_CONTENT TEXT_MESSAGE_END TOOL_CALL_START TOOL_CALL_ARGS TOOL_CALL_END
         # TOOL_CALL_RESULT STATE_SNAPSHOT STATE_DELTA MESSAGES_SNAPSHOT SUBAGENT_STARTED
         # SUBAGENT_FINISHED INTERRUPT (HITL) USAGE (native) RAW CUSTOM + thinking/chunk variants

class AgUiEvent(BaseModel):     # one subclass per type, e.g. the four below
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)
    type: AgUiEventType | str; timestamp: int | None = None   # epoch ms
    run_id: str | None = None; thread_id: str | None = None
class TextMessageContent(AgUiEvent): message_id: str; delta: str
class ToolCallStart(AgUiEvent): tool_call_id: str; tool_call_name: str; parent_message_id: str | None = None
class StateDelta(AgUiEvent): delta: list[dict[str, Any]]          # RFC 6902 JSON Patch
class SubagentStarted(AgUiEvent): subagent_id: str; name: str; parent_run_id: str
class CustomEvent(AgUiEvent): name: str; value: Any
```

## SPEC-PRT-AGU-002: Translator (`events/agui.py`)

```python
@dataclass
class AgUiContext:                        # mutable per-run translation state (not checkpointed)
    run_id: str; thread_id: str
    include_state_deltas: bool = True; include_usage: bool = True; include_metadata: bool = True
    open_message_id: str | None = None; last_state: dict[str, Any] | None = None
    open_tool_calls: dict[str, str] = field(default_factory=dict)
    active_subagents: dict[str, str] = field(default_factory=dict)

def start_run(ctx: AgUiContext) -> list[AgUiEvent]: ...            # [RUN_STARTED]
def to_agui_events(chunk: tuple[str, Any] | Any, ctx: AgUiContext) -> list[AgUiEvent]:
    """Pure, sync, no I/O, never raises. Input = one item of graph.astream(stream_mode=
    ["messages", "updates", "custom"]). Unknown shapes ⇒ [RAW]. Translation errors ⇒ [] +
    prismal.agui_translation_errors_total."""
def finish_run(ctx: AgUiContext, *, summary: RunSummary | None, result: Mapping[str, Any] | None = None) -> list[AgUiEvent]: ...
def error_run(ctx: AgUiContext, *, code: str, message: str) -> list[AgUiEvent]: ...
def interrupt_event(ctx: AgUiContext, interrupt: Any) -> list[AgUiEvent]: ...   # LangGraph interrupt()
```

Mapping (normative; full table in ARCHITECTURE §3.4): AI message chunk ⇒ `TEXT_MESSAGE_START` (first)
+ `TEXT_MESSAGE_CONTENT`; tool-call chunk ⇒ `TOOL_CALL_START/ARGS/END`; `ToolMessage` ⇒
`TOOL_CALL_RESULT`; `updates` from a node ⇒ `STEP_STARTED/FINISHED` (+ `STATE_DELTA` vs
`last_state` when enabled); Skynet worker / MoA proposer / spawner `Send` ⇒ `SUBAGENT_STARTED/FINISHED`;
`interrupt()` ⇒ `INTERRUPT`; `state["metadata"]["routing"|"decision"]` ⇒ `CUSTOM` `prismal.routing` /
`prismal.decision`. `finish_run` closes any open message/tool call, then emits usage (SPEC-PRT-USG-002),
then `RUN_FINISHED`. Message content is passed through as produced — the translator never re-reads
user input (no prompt construction).

## SPEC-PRT-USG-001: Usage wire object (`monitoring/run_summary.py`)

```python
def get_run_summary(session_id: str, *, turn: Literal["last", "all"] = "last") -> RunSummary | None: ...  # never raises

class RunSummary:   # existing (OBS/TKE1); additive fields defaulted
    served_model: str | None = None; gateway: str | None = None; cost_source: str = "none"
    def to_usage_wire(self) -> dict[str, Any]: ...      # exactly the 12 keys below, in this order
```

| # | Key | Type | Semantics |
|---|---|---|---|
| 1 | `prompt_tokens` | int ≥ 0 | All input tokens, **including** cached reads/writes |
| 2 | `completion_tokens` | int ≥ 0 | All output tokens, **including** reasoning |
| 3 | `cache_read_tokens` | int ≥ 0 | Subset of `prompt_tokens` served from provider cache |
| 4 | `cache_write_tokens` | int ≥ 0 | Subset of `prompt_tokens` written to provider cache |
| 5 | `reasoning_tokens` | int ≥ 0 | Subset of `completion_tokens` |
| 6 | `total_tokens` | int ≥ 0 | `prompt_tokens + completion_tokens` |
| 7 | `calls` | int ≥ 0 | LLM calls aggregated |
| 8 | `cost_usd` | float ≥ 0 | USD, rounded to 6 decimals |
| 9 | `cost_source` | str | `gateway` \| `litellm` \| `table` \| `subscription` \| `none` \| `mixed` |
| 10 | `estimated` | bool | `true` unless every call was priced by `gateway` or `litellm` |
| 11 | `served_model` | str \| null | Model that served the last call (null if unknown) |
| 12 | `gateway` | str \| null | GW gateway name; `null` = direct provider |

This table is normative for `prismal-server` (`SPEC-RH2-USG-001`) and `prismal-sdk`
(`SPEC-CS2-USG-001`); changes require a coordinated minor in all three repos.

## SPEC-PRT-USG-002: Usage in AG-UI

`finish_run` emits `CUSTOM {name: "prismal.usage", value: <usage wire>}` immediately before
`RUN_FINISHED`; WHERE AG-UI 1.0 defines a native usage event (PRT0), THE SYSTEM SHALL additionally emit
it with the native field mapping documented in ADR-PRT-001. `RUN_FINISHED.result` is not used for usage.

## SPEC-PRT-CFG-001: Settings (`core/config.py`)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `a2a_protocol_version` | `Literal["0.3","1.0"]` | `"0.3"` | Card version + default inbound stack |
| `a2a_accept_legacy` | `bool` | `True` | Accept v0.3 requests when v1 is primary |
| `a2a_tasks_list_max_page_size` / `a2a_inbound_auth_schemes` | `int` / `list[str]` | `200` / `[]` | `tasks/list` bound; schemes in v1 card |
| `a2a_oauth_redirect_uri` | `str \| None` | `None` | PKCE redirect (host callback) |
| `mcp_protocol_mode` | `Literal["auto","legacy","stateless"]` | `"legacy"` | Adapter selection |
| `mcp_stateless_protocol_version` | `str` | `"2026-07-28"` | Version sent by the stateless adapter |
| `mcp_tool_cache_enabled` | `bool` | `False` | TTL tool-list cache |
| `mcp_tool_cache_default_ttl_s` / `_max_ttl_s` / `_max_entries` | `int` | `300` / `86400` / `256` | TTL without `ttlMs`; TTL ceiling; LRU bound |
| `mcp_tasks_enabled` | `bool` | `False` | Tasks extension |
| `mcp_task_poll_interval_s` / `_max_interval_s` / `mcp_task_timeout_s` | `float` | `1.0` / `15.0` / `900.0` | Poll backoff + timeout |
| `agui_events_enabled` | `bool` | `False` | Expose the translator to the host |
| `agui_include_state_deltas` / `agui_include_usage` / `agui_include_metadata` | `bool` | `True` | Translator options |

`_validate_protocol_upgrades` rejects non-positive TTL/poll values, `default_ttl > max_ttl`, and
`a2a_protocol_version="0.3"` with `a2a_accept_legacy=False`. Secrets (OAuth client secrets, refresh
tokens) are never settings fields — they are `CredentialVault` keys.

## SPEC-PRT-ERR-001: Exceptions (`core/exceptions.py`)

```python
class ProtocolUpgradeError(PrismalError): ...
class A2AVersionError(ProtocolUpgradeError, A2AError): ...; class A2AOAuthError(ProtocolUpgradeError, A2AError): ...
class McpProtocolError(ProtocolUpgradeError): ...; class McpDiscoveryError(McpProtocolError): ...
class McpTaskError(McpProtocolError): ...; class McpTaskTimeout(McpTaskError): ...
class AgUiTranslationError(ProtocolUpgradeError): ...   # internal/tests only; translator never raises
```

## SPEC-PRT-OTEL-001: Counters (`monitoring/otel.py`)

`prismal.a2a_requests_total{protocol_version,direction,method,outcome}`, `prismal.a2a_oauth_tokens_total{flow,outcome}`,
`prismal.mcp_protocol_negotiations_total{mode}`, `prismal.mcp_tool_cache_hits_total`, `prismal.mcp_tool_cache_misses_total`,
`prismal.mcp_tool_list_changes_total{server}`, `prismal.mcp_tasks_total{status}`, `prismal.mcp_task_wait_s` (histogram),
`prismal.mcp_input_requests_total{kind,action}`, `prismal.agui_events_total{type}`, `prismal.agui_translation_errors_total`.

## SPEC-PRT-GRD-001: Guards

With all PRT settings at defaults the compiled supervisor graph, `build_agent_card()` output, MCP
request sequence and tool lists are byte-for-byte unchanged; `prismal/events/**` imports no HTTP
library (AST test); the existing `agents/**` → `mcp`/`skills` guard is unchanged.

## Acceptance Criteria (per requirement, EARS)

| Requirement | Acceptance criterion |
|---|---|
| SPEC-PRT-A2A-001 | WHEN an official v1 fixture is parsed and dumped, THE SYSTEM SHALL reproduce it without field loss |
| SPEC-PRT-A2A-002 | WHEN a v1 method arrives without version header, THE SYSTEM SHALL route it to the v1 stack; IF v0.3 arrives with `a2a_accept_legacy=False`, THEN THE SYSTEM SHALL answer `-32009` and count `outcome="rejected"` |
| SPEC-PRT-A2A-003 | WHERE `a2a_protocol_version="0.3"`, THE SYSTEM SHALL emit the pre-PRT card byte-for-byte; WHEN `"1.0"` with `org_id="acme"`, every interface URL SHALL contain the `acme` segment |
| SPEC-PRT-A2A-004 | IF a v1 `tenant` differs from the resolved `org_id`, THEN THE SYSTEM SHALL return `-32001` and audit it |
| SPEC-PRT-A2A-005 | WHEN 120 `acme` and 5 `beta` tasks exist, THE SYSTEM SHALL page `acme` as 50/50/20 with final `next_page_token=None` and never return a `beta` task |
| SPEC-PRT-A2A-006 | IF `list_tasks` targets a v0.3 peer, THEN THE SYSTEM SHALL raise `A2AVersionError` before any I/O |
| SPEC-PRT-OAU-001 | IF an interactive grant is needed and no prompter is injected, THEN THE SYSTEM SHALL raise `A2AOAuthError` |
| SPEC-PRT-MCP-001 | WHEN `auto` targets a 2026-07-28 server, THE SYSTEM SHALL send zero `initialize`; WHEN it targets a 2025-06-18 server, THE SYSTEM SHALL use the legacy adapter and count `mode="legacy"` |
| SPEC-PRT-MTC-001 | WHILE the TTL is valid or the content hash is unchanged, THE SYSTEM SHALL return byte-identical tool lists |
| SPEC-PRT-MTK-001 | IF a task exceeds `mcp_task_timeout_s`, THEN THE SYSTEM SHALL cancel it and raise `McpTaskTimeout` |
| SPEC-PRT-MTK-002 | WHEN no handler is injected, THE SYSTEM SHALL decline every elicitation and audit its hash |
| SPEC-PRT-AGU-001/002 | WHEN the 12 golden transcripts are translated, THE SYSTEM SHALL match fixtures, validate against the AG-UI 1.0 schema **[verify]** and balance every `*_START` with one `*_END` |
| SPEC-PRT-USG-001 | THE SYSTEM SHALL emit exactly the 12 usage keys with the documented types |
| SPEC-PRT-USG-002 | WHERE `agui_include_usage` is enabled, THE SYSTEM SHALL emit `prismal.usage` before `RUN_FINISHED` |
| SPEC-PRT-CFG-001 | IF `mcp_tool_cache_default_ttl_s > mcp_tool_cache_max_ttl_s`, THEN THE SYSTEM SHALL fail settings load |
| SPEC-PRT-OTEL-001 | WHEN each triggering event occurs, THE SYSTEM SHALL increment its counter exactly once |
| SPEC-PRT-GRD-001 | WHERE all PRT flags are at defaults, THE SYSTEM SHALL keep graph and outputs byte-for-byte unchanged |
