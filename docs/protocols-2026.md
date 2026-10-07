# Protocol upgrades 2026 (Phase PRT, v3.17.0)

Phase PRT moves prismal's three wire protocols to their 2026 revisions without breaking anyone on
the old ones. Everything is **opt-in**: with the default settings the compiled graph, the v0.3
Agent Card and the MCP request sequence are byte-for-byte what 3.16.0 produced (snapshot-tested in
`tests/unit/agents/test_graph_snapshot_protocol_upgrades.py`).

| Protocol | Old | New | Switch |
|---|---|---|---|
| A2A | v0.3 | **v1.0** (dual-stack) | `a2a_protocol_version`, `a2a_accept_legacy` |
| MCP | 2025-06-18 / 2025-11-25 (session) | **2026-07-28** (stateless) + tasks extension | `mcp_protocol_mode`, `mcp_tasks_enabled` |
| AG-UI | — | **1.0** event translator | `agui_events_enabled` (host-side) |

The exact wire names were verified against the official texts on 2026-10-06 and are pinned in
[ADR-PRT-001](adr/ADR-PRT-001-protocol-pins.md). Where the ADR and `specs/protocol-upgrades-2026/`
disagree, the ADR wins.

## A2A v1.0 dual-stack

`A2AServerHandler` serves **both** v0.3 and v1.0 on one endpoint. The version of each inbound
request is negotiated by `prismal.a2a.negotiation.negotiate_inbound`:

1. an explicit `A2A-Version` header (or `?A2A-Version=`) wins — an unsupported value answers
   **-32009 VersionNotSupported**;
2. otherwise the method family decides (v1 `SendMessage`, `GetTask`, `ListTasks`, `CancelTask`,
   … are PascalCase; v0.3 `message/send`, `tasks/get`, … are slash-named; they never overlap);
3. otherwise the header-less default is **0.3**, as the A2A spec requires.

`a2a_accept_legacy=False` rejects v0.3 traffic (only valid with `a2a_protocol_version="1.0"`).
`handle_rpc(..., headers=None)` keeps the 3.16.0 behaviour byte-for-byte.

| Piece | Module |
|---|---|
| v1 wire models (`TASK_STATE_*`, `ROLE_*`, `Part` oneof `text`/`raw`/`url`/`data`, `supportedInterfaces[]`) | `a2a/v1/{constants,types}.py` |
| v0.3 ⇄ v1 ⇄ internal codecs (lossy fields listed in the module docstring) | `a2a/convert.py` |
| Agent Card per version — `build_agent_card(..., protocol_version="0.3"|"1.0")` | `a2a/card.py` |
| Task store port (`A2ATaskStorePort`, `InMemoryA2ATaskStore`), `ListTasks` paging (max 100, last page ⇒ `nextPageToken: ""`) | `a2a/task_store.py` |
| OAuth flows: PKCE authorization code, device code, client credentials | `a2a/oauth.py` |
| Outbound client: `A2AClient(protocol=..., tenant=..., local_org_id=...)`, `list_tasks()` | `a2a/client.py` |

**Tenancy.** v1 carries `tenant` in request params and in `AgentInterface.tenant` (there is no
`multi_tenant` capability). A request for another tenant's task — including a `SendMessage`
that reuses a task id owned by another org or principal — answers **TaskNotFound** (v1 `-32001`; the v0.3 stack keeps its `-32004`, the same code as a missing task)
(revealing nothing) and is audited as `a2a.inbound.tenant_mismatch` with hashed ids; the existing
record is never overwritten.

**Routes.** The host mounts the card and the JSON-RPC endpoint from
`RuntimeContext.a2a_routes(protocol_version=None) -> A2ARouteSpec(card_path, rpc_path,
tenant_scoped)`; `a2a_base_url.rstrip("/") + rpc_path` is exactly the URL the card advertises
(contract-tested for both versions, with and without `org_id`).

**OAuth hardening (outbound).**
- The refresh token is stored under `a2a.oauth.<sha256(token origin | agent url)>` in the vault
  partition of the **local** org (`build_runtime(org_id=...)` ⇒ `local_org_id`), never under a
  name or tenant taken from the remote card; it is bound to its `token_url` and never sent to a
  different endpoint.
- Token / authorization / device URLs must be `https`; literal private, loopback and link-local
  IPs are rejected. Under `a2a_strict=True` their hosts must also match `a2a_outbound_allowlist`
  (empty ⇒ deny).
- **Residual risk:** outside strict mode and with no allowlist, a *hostname* that resolves to an
  internal address is not caught (no DNS resolution / IP pinning). Production hosts should run
  with `a2a_strict=True` and an allowlist.

## MCP 2026-07-28 (stateless) + tasks extension

prismal keeps the pinned `mcp` **1.28.1** SDK (it stops at 2025-11-25); the 2026-07-28 stateless
wire and the tasks extension are implemented in-house on `httpx` (`prismal/mcp/protocol.py`). The
`mcp` 2.x migration is a separate future task.

| `mcp_protocol_mode` | Behaviour |
|---|---|
| `legacy` (default) | today's session path — `initialize` → `notifications/initialized` → `tools/list` → `tools/call`, byte-for-byte (recording + respx e2e tests) |
| `stateless` | HTTP servers use `StatelessHttpAdapter`: no `initialize`, no `Mcp-Session-Id`; every request carries `_meta` (`io.modelcontextprotocol/protocolVersion`, `clientCapabilities`, `clientInfo`) and the `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` headers |
| `auto` | stdio is always legacy; HTTP servers are probed once with `server/discover` (a legacy server answers `-32601` ⇒ legacy), memoized per server |

Selection happens on connect inside `MCPServerConnection`; `build_default_tool_provider(settings)`
forwards the settings to `MCPClientManager` only when a PRT MCP flag is on, so default hosts build
the manager exactly as in 3.16.0. A stateless server's tools still become `mcp.types.Tool` and go
through `MCPToolAdapter` unchanged.

**Tool-list cache** (`mcp_tool_cache_enabled`, `prismal/mcp/tool_cache.py`). Lists are cached
under the server's `ttlMs` (clamped to `mcp_tool_cache_max_ttl_s`; legacy lists without `ttlMs`
use `mcp_tool_cache_default_ttl_s`) and `cacheScope` (`public` ⇒ shared, `private` ⇒ per
`(org_id, principal)`; a missing scope fails closed to per-principal). A canonical content hash
keeps the *same* `BaseTool` objects across refreshes while the content is unchanged, so the tool
block in the prompt stays byte-identical and prompt caching keeps hitting.

**Tasks** (`mcp_tasks_enabled`, `prismal/mcp/tasks.py`). A task-augmented `tools/call` returns a
handle; `McpTaskPoller.wait` polls `tasks/get` with backoff (`mcp_task_poll_interval_s` →
`mcp_task_poll_max_interval_s`, honouring `pollIntervalMs`), cancels and raises on
`mcp_task_timeout_s`, and cancels the remote task when the run is cancelled. A task result is
accepted **only** when the client declared the extension; otherwise prismal cancels it and raises
`McpTaskError` (fail closed). With tasks off, task-only tools (`execution.taskSupport ==
"required"`) are filtered out and counted as `mcp_tasks{status="unavailable"}`.

**Input requests** (multi-round-trip). An `input_required` result is answered through an
injectable `InputRequestHandler` (`MCPClientManager(input_handler=...)`); the default
`DeclineAllInputHandler` declines and hash-audits. Sampling and roots requests (deprecated) are
always declined in stateless mode. Rounds are capped (3).

## AG-UI 1.0 translator

`prismal/events/agui.py` turns a LangGraph `astream(..., stream_mode=["messages", "updates",
"custom"])` stream into AG-UI 1.0 events. It is a **pure translator**: no HTTP, no I/O, no prompt
building (an AST guard, `tests/unit/events/test_no_http_imports.py`, forbids HTTP libraries in
`prismal/events/**`; `webhook_dispatcher.py` is the one documented exemption). The host owns the
transport and maps the `agui_include_*` settings onto `AgUiContext`.

```python
from prismal.events.agui import AgUiContext, error_run, finish_run, start_run, to_agui_events, to_sse

ctx = AgUiContext(run_id=run_id, thread_id=thread_id,
                  include_state_deltas=s.agui_include_state_deltas,
                  include_usage=s.agui_include_usage,
                  include_metadata=s.agui_include_metadata)
for ev in start_run(ctx, initial_state=state):
    yield to_sse(ev)
async for chunk in graph.astream(state, config, stream_mode=["messages", "updates", "custom"],
                                 subgraphs=True):
    for ev in to_agui_events(chunk, ctx):
        yield to_sse(ev)
for ev in finish_run(ctx, summary=get_run_summary(session_id)):
    yield to_sse(ev)
```

None of these functions raise; a chunk that cannot be translated yields `[]` and counts
`prismal.agui_translation_errors_total{mode}`.

| LangGraph | AG-UI 1.0 |
|---|---|
| run start / end | `RUN_STARTED` (`protocolVersion: "1.0"`) / `RUN_FINISHED` |
| node `updates` | `STEP_STARTED` / `STEP_FINISHED`, `STATE_DELTA` (RFC 6902) |
| `AIMessageChunk` text | `TEXT_MESSAGE_START` / `_CONTENT` / `_END` (several open messages per node, so parallel `Send` branches do not close each other) |
| tool-call chunks / `ToolMessage` | `TOOL_CALL_START` / `_ARGS` / `_END` / `_RESULT` |
| `interrupt()` | `RUN_FINISHED.outcome = {type: "interrupt", interrupts: [...]}` (there is no `INTERRUPT` event) |
| run usage | `CUSTOM prismal.usage` (12-key wire) **and** native `RUN_FINISHED.usage[]` (there is no `USAGE` event) |
| `metadata.routing` / `metadata.decision` | `CUSTOM prismal.routing` / `prismal.decision` (never in state deltas) |
| custom `{"type": "subagent", "phase": ...}` | `SUBAGENT_STARTED` / `_FINISHED` / `_ERROR` |
| anything else | `RAW` |

The state view is an allowlist of top-level keys, and `metadata` is an allowlist too
(`ALLOWED_METADATA_KEYS`: `budget` projected to `{enabled, scope}`, `intent` as a scalar).
Only scalar leaves are copied, so `metadata.security`, `hardening`, `identity` (on-behalf
tokens), `mm` (media paths), `routing`, `decision`, `_`-prefixed and any unknown keys never
leave the process. `RAW` never carries state: it holds only `{mode, payload_type}` (plus, for a
mapping `custom` payload, its allowlist projection). Sub-agents still open at
`finish_run`/`error_run` are closed with `SUBAGENT_ERROR` (`subagent_incomplete`).

**Known limit (3.17.0):** nothing in prismal emits the `subagent` custom payload yet, so live
Skynet / MoA runs show `STEP_*` events but no `SUBAGENT_*` events. Emitting it from
`get_stream_writer()` in the Skynet worker and the MoA proposers is follow-up work.

Golden transcripts: `tests/fixtures/protocols/agui/*.json` (12; regenerate with
`uv run python -m tests.unit.events._transcripts --write`). Example: `examples/agui_translate.py`.

## Usage wire (shared contract)

`RunSummary.to_usage_wire()` and `prismal.monitoring.get_run_summary(session_id, turn="last"|"all")`
produce the 12-key object the server, SDK and AG-UI `CUSTOM prismal.usage` all carry:

`prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
total_tokens, calls, cost_usd, cost_source, estimated, served_model, gateway`

`cost_source` is `gateway | litellm | table | subscription | none | mixed`; `estimated` is true
whenever any priced call was not authoritative. The contract fixture is
`tests/fixtures/protocols/usage_wire.json` — change it only together with the consumers.

## Observability

| Metric | Labels |
|---|---|
| `prismal.a2a_requests_total` | `protocol_version`, `direction`, `method`, `outcome` |
| `prismal.a2a_oauth_tokens_total` | `flow`, `outcome` |
| `prismal.mcp_protocol_negotiations_total` | `mode` |
| `prismal.mcp_tool_cache_hits_total` / `prismal.mcp_tool_cache_misses_total` | — |
| `prismal.mcp_tool_list_changes_total` | `server` |
| `prismal.mcp_tasks_total` | `status` |
| `prismal.mcp_task_wait_s` (histogram) | — |
| `prismal.mcp_input_requests_total` | `kind`, `action` |
| `prismal.agui_events_total` | `type` |
| `prismal.agui_translation_errors_total` | `mode` |
| `prismal.host_surface_errors_total` | `method` |

## Conformance suites (opt-in)

`tests/interop/test_conformance_suites.py` runs the official suites only with `PRISMAL_INTEROP=1`
plus each suite's artifact:

| Suite | Env |
|---|---|
| AG-UI 1.0 JSON schema over the golden transcripts | `PRISMAL_INTEROP_AGUI_SCHEMA=<path to spec/1.0/schema.json>` |
| `a2aproject/a2a-tck` (mandatory category) | `PRISMAL_INTEROP_A2A_TCK=<checkout>`, `PRISMAL_INTEROP_A2A_SUT=<url>` |
| `@modelcontextprotocol/conformance` (`npx`) | `PRISMAL_INTEROP_MCP_SERVER=<url>` |

They never run in the unit gate.

## Rollout

1. Upgrade to 3.17.0 with defaults — nothing changes on the wire.
2. **A2A:** keep `a2a_protocol_version="0.3"` and turn on v1 per peer by sending `A2A-Version: 1.0`;
   once all clients speak v1, set `a2a_protocol_version="1.0"` (keep `a2a_accept_legacy=True`
   until the last v0.3 client is gone).
3. **MCP:** set `mcp_protocol_mode="auto"` (stdio stays legacy; HTTP servers are probed with
   `server/discover`), then `mcp_tool_cache_enabled=True`, then `mcp_tasks_enabled=True`.
4. **AG-UI:** the host enables `agui_events_enabled` and serves `to_sse()` frames.
