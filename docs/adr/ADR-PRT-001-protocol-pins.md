# ADR-PRT-001 — Protocol pins for Phase PRT (A2A v1.0, MCP 2026-07-28, AG-UI 1.0)

| Field | Value |
|---|---|
| **Status** | `ACCEPTED` |
| **Date** | 2026-10-06 |
| **Author** | Ernesto Crespo |
| **Scope** | `prismal/a2a/v1/`, `prismal/a2a/{negotiation,convert,card,server,task_store,oauth,client}.py`, `prismal/mcp/{constants,protocol,tool_cache,tasks,input_requests}.py`, `prismal/events/agui.py` |
| **Spec** | `specs/protocol-upgrades-2026/` (PRT0-01..05) |

## 1. Context

The PRT SDD was written before the three protocols were read end to end; every protocol name in it
was marked **[verify]**. PRT0 read the official texts on 2026-10-06. Protocol names appear only in
the per-protocol constants modules; this ADR is the source of truth for them and amends the SPEC
where the facts differ.

## 2. Verified facts and pins

### 2.1 A2A (a2aproject/A2A `specification/a2a.proto` v1.0.0 2026-03-12, v1.0.1 2026-05-28)

| Item | Pin | SPEC change |
|---|---|---|
| Version | `"1.0"` (v1.0.1 changed comments only) | was `"1.0.0"` |
| Version header | `A2A-Version: <Major.Minor>` (also `?A2A-Version=`); **empty ⇒ 0.3** | missing header no longer means v1 |
| v1 JSON-RPC methods | `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, `{Create,Get,List,Delete}TaskPushNotificationConfig(s)`, `GetExtendedAgentCard` | was `tasks/list` |
| v0.3 methods | `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`, `tasks/resubscribe`, `tasks/pushNotificationConfig/*`, `agent/getAuthenticatedExtendedCard` | — |
| `tenant` | field of every **request params** object and of `AgentInterface`; not on `Message`/`Task` | moved off the models |
| `TaskState` | `TASK_STATE_{SUBMITTED,WORKING,COMPLETED,FAILED,CANCELED,INPUT_REQUIRED,REJECTED,AUTH_REQUIRED,UNSPECIFIED}` | was lowercase |
| `Role` | `ROLE_USER`, `ROLE_AGENT` | — |
| `Part` | oneof `text` / `raw` (base64) / `url` / `data` + `mediaType`, `filename`, `metadata`; **no `kind`** | was `kind`-discriminated |
| `ListTasks` params | `tenant`, `contextId`, `status`, `pageSize` (default 50, **max 100**), `pageToken`, `historyLength`, `statusTimestampAfter`, `includeArtifacts` (default false ⇒ artifacts omitted) | wider |
| `ListTasks` result | `{tasks, nextPageToken, pageSize, totalSize}` all required; **last page ⇒ `nextPageToken: ""`** | was `None` |
| Agent Card | no top-level `url` / `protocolVersion`; `supportedInterfaces[]` each `{url, protocolBinding (JSONRPC\|GRPC\|HTTP+JSON), protocolVersion, tenant?}`; `capabilities {streaming, pushNotifications, extensions, extendedAgentCard}`; `securitySchemes` / `securityRequirements` | no `multi_tenant` capability: tenancy = `AgentInterface.tenant` |
| OAuth | `OAuthFlows` oneof: `authorizationCode` (+`pkceRequired`), `clientCredentials`, `deviceCode` (`deviceAuthorizationUrl`, `tokenUrl`, `refreshUrl?`, `scopes`) | — |
| Errors | -32001 TaskNotFound … -32009 **VersionNotSupported** | -32009 confirmed |
| Content type | `application/a2a+json` preferred on HTTP | — |

**Decisions.**
- Inbound negotiation (amends SPEC-PRT-A2A-002): explicit `A2A-Version` header wins; otherwise the
  method family decides (the PascalCase v1 names and the slash v0.3 names do not overlap); an
  unknown method falls back to the header-less default **0.3** (the spec rule), not to the setting.
- Tenant mismatch (SPEC-PRT-A2A-004) answers **-32001 TaskNotFound** on purpose — it reveals
  nothing about the tenant's tasks — and is audited as `a2a.inbound.tenant_mismatch`.
  Authentication failures stay with the host (HTTP 401/403); the existing v0.3 strict-mode
  `-32001` is unchanged.
- `a2a_tasks_list_max_page_size` is clamped to the protocol maximum 100.

### 2.2 MCP (modelcontextprotocol `schema/2025-11-25`, `schema/2026-07-28`; ext-tasks SEP-2663)

| Item | Pin |
|---|---|
| Versions | stateless `2026-07-28`; legacy `2025-11-25`, `2025-06-18` |
| Stateless wire | no `initialize`/`notifications/initialized`/`Mcp-Session-Id`; every request `_meta`: `io.modelcontextprotocol/protocolVersion` (req.), `io.modelcontextprotocol/clientCapabilities` (req.), `io.modelcontextprotocol/clientInfo`; headers `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`; errors -32020 header mismatch, -32021 missing client capability, -32022 unsupported version |
| Discovery | `server/discover` → `{supportedVersions[], capabilities, instructions?}`; legacy servers answer **-32601** |
| Cache | `ttlMs` (≥ 0) and `cacheScope` (`public` \| `private`) **required** on `tools/list` (and other list/read results); `ttlMs: 0` ⇒ immediately stale |
| Results | `resultType`: `complete` \| `input_required` \| `task` (missing ⇒ `complete`) |
| Multi-round-trip | `InputRequiredResult {inputRequests?: {key: CreateMessage\|ListRoots\|Elicit request}, requestState?}`; retry the original request with `inputResponses` (same keys) + `requestState` |
| Tasks extension | id `io.modelcontextprotocol/tasks`; `tasks/get`, `tasks/update` (carries `inputResponses`), `tasks/cancel`, `notifications/tasks`; task-augmented `tools/call` ⇒ `{resultType: "task", taskId, status, ttlMs, pollIntervalMs?}`; statuses `working`, `input_required`, `completed`, `failed`, `cancelled` |
| Deprecated | sampling, roots, logging, `toolChoice` (≥ 12-month window) |
| Python SDK | `mcp` 2.x supports 2026-07-28 (breaking API); the pinned **1.28.1** stops at 2025-11-25; no SDK ships the tasks extension |

**Decisions.**
- **No SDK bump in 3.17.0.** `LegacySessionAdapter` keeps the 1.x session path byte-for-byte;
  `StatelessHttpAdapter` and the tasks extension are implemented in-house on `httpx` behind
  `mcp_protocol_mode` / `mcp_tasks_enabled`. The `mcp` 2.x migration is a separate future task.
- `CacheScope` maps `public → shared`, `private → principal`. A legacy list without `ttlMs` uses the
  default TTL.
- Fail closed on the spec inconsistency about unsolicited task handles: a task result is accepted
  only when the client declared the tasks extension; otherwise it is an error.
- Stateless mode declines sampling/roots input requests (deprecated) and answers elicitations
  through the injected `InputRequestHandler` (default: decline + hash audit).

### 2.3 AG-UI (`ag-ui-protocol` 1.0.0, 2026-09-17; schema `https://ag-ui.com/spec/1.0/schema.json`)

| Item | Pin |
|---|---|
| Version | `"1.0"` (two components; `RUN_STARTED.protocolVersion`) |
| Events (31) | `TEXT_MESSAGE_{START,CONTENT,END,CHUNK}`, `TOOL_CALL_{START,ARGS,END,CHUNK,RESULT}`, `STATE_{SNAPSHOT,DELTA}`, `MESSAGES_SNAPSHOT`, `ACTIVITY_{SNAPSHOT,DELTA}`, `RAW`, `CUSTOM`, `RUN_{STARTED,FINISHED,ERROR}`, `STEP_{STARTED,FINISHED}`, `REASONING_{START,MESSAGE_START,MESSAGE_CONTENT,MESSAGE_END,MESSAGE_CHUNK,END,ENCRYPTED_VALUE}`, `SUBAGENT_{STARTED,FINISHED,ERROR}` |
| Usage | **no `USAGE` event**: `RUN_FINISHED.usage` / `RUN_ERROR.usage` = `TokenUsage[] {provider, model, inputTokens, outputTokens, totalTokens, reasoningTokens, cachedInputTokens, cacheWriteInputTokens}` |
| Interrupt | **no `INTERRUPT` event**: `RUN_FINISHED.outcome = {type: "interrupt", interrupts: [{id, reason, message?, toolCallId?, responseSchema?, expiresAt?, subagentRunId?, metadata?}]}`; resume via `RunAgentInput.resume` |
| Sub-agents | `SUBAGENT_STARTED {subagentRunId, name, description?, parentSubagentRunId?, parentToolCallId?, parentMessageId?}`, `SUBAGENT_FINISHED {subagentRunId, result?, outcome}`, `SUBAGENT_ERROR` |
| Encoding | SSE `data: <camelCase JSON>\n\n` |

**Decisions.**
- Hand-written Pydantic models in `prismal/events/agui.py` (no new dependency; `extra="allow"`).
- `interrupt()` ⇒ `RUN_FINISHED.outcome.type="interrupt"`. Usage ⇒ `CUSTOM prismal.usage` (the
  12-key wire, SPEC-PRT-USG-001) **and** the native `RUN_FINISHED.usage[]` mapping.
- `SUBAGENT_ERROR` added to the Skynet / MoA mapping; `prismal.routing` / `prismal.decision` stay
  `CUSTOM` events.

### 2.4 Conformance suites

`a2aproject/a2a-tck` (Python, needs a running SUT), `@modelcontextprotocol/conformance` (Node) and
AG-UI `spec/1.0/conformance/streams/*.json` exist. They run under the opt-in `interop` marker, never
in the unit gate.

## 3. Consequences

- SPEC-PRT-A2A-001/002/004/005, MCP-001, MTC-001, MTK-001/002, AGU-001/002 and USG-002 are amended
  by this ADR (the SPEC keeps its text with a pointer here).
- `mcp` 2.x migration and protobuf/gRPC bindings are out of scope for 3.17.0.

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-06 | Ernesto Crespo | PRT0-01..05 pins and re-scopes |
