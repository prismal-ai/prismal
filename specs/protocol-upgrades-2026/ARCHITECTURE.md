# Prismal Protocol Upgrades 2026 — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | PRT |
| **Target package version** | `3.17.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/protocol-upgrades-2026/PLAN.md` |
| **SPEC** | `specs/protocol-upgrades-2026/SPEC.md` |
| **TASKS** | `specs/protocol-upgrades-2026/TASKS.md` |

---

## 1. Context

Three protocol surfaces exist today, each pinned to a 2025 revision:

- **A2A (Phase I)** — `prismal/a2a/types.py` (`PROTOCOL_VERSION = "0.3.0"`, camelCase aliases),
  `card.py::build_agent_card` (cached per settings fingerprint + org), `server.py::A2AServerHandler`
  (`message/send`, `tasks/get`, `tasks/cancel`; `_run_task` sanitizes → graph → artifact → audit),
  `client.py::A2AClient` (bearer + `oauth2_client_credentials`), `provider.py::A2AToolProvider`.
  The host mounts `GET /.well-known/agent-card.json` and `POST /a2a`.
- **MCP** — `prismal/mcp/` opens an `mcp.ClientSession` per server (`mcp>=1.28.1`), sends
  `initialize`, lists tools once per `McpToolProvider` build (cap 60), and resolves secrets through
  `resolve_secret()` (Phase W). Tools reach agents only via `ToolProviderPort` (Fase Y).
- **UI events** — the engine has none. `prismal-server/streaming.py` maps `graph.astream` to SSE
  `token {delta}`, `tool_call {name,args}`, `state`, terminal `done`/`error`, plus heartbeat comments.

The 2026 revisions (A2A v1.0, MCP 2026-07-28, AG-UI 1.0 — all **[verify]**) are breaking or new.
PRT adds them without disturbing any current caller.

## 2. Feasibility with the existing core

- **A2A** — Pydantic v2 models with alias generators already exist; a v1 package beside them is
  additive. `_run_task` is protocol-agnostic once a request is decoded into internal models, so v1
  only needs edge codecs (`convert.py`) plus the genuinely new operation `tasks/list`. The handler
  already tracks tasks for `tasks/get` (in-memory registry); wrapping it as `A2ATaskStorePort` gives
  `tasks/list` without a new store. Per-tenant cards already exist (`org_id` in URL + cache key).
- **OAuth** — `A2AClient` already caches client-credential tokens; PKCE and device code add a
  user-interaction step, which the engine cannot perform itself (no browser, no terminal). An injected
  `AuthorizationPrompter` (host/TUI/dashboard) keeps the engine headless. `CredentialVault` (IDN)
  stores refresh tokens per org.
- **MCP** — all MCP use goes through `prismal/mcp/` and `McpToolProvider`; inserting an adapter
  Protocol below the provider is local. The `mcp` SDK may not yet speak 2026-07-28 **[verify]**: the
  stateless wire is plain JSON-RPC over HTTP POST, implementable with `httpx` (already a transitive
  dependency of `[mcp]`/`[a2a]`) behind the adapter, so PRT does not block on the SDK.
- **AG-UI** — LangGraph `astream(stream_mode=["messages","updates","custom"])` yields
  `(mode, payload)` tuples carrying everything AG-UI needs: message chunks with tool-call chunks,
  per-node updates, custom events emitted by patterns (Skynet/MoA/spawner already write progress under
  `state["metadata"]`), and `__interrupt__` updates from `interrupt()`. A pure translator with a small
  mutable context is sufficient; no graph change is needed.
- **Usage** — TKE1 extends `Usage`/`RunSummary` with cache and reasoning tokens; GW adds served model
  and gateway attribution. PRT only adds the accessor and a serializer, not new metering.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose | Phase |
|---|---|---|
| `a2a/v1/types.py`, `a2a/v1/constants.py` | v1 wire models + names pinned at PRT0 | PRT1 |
| `a2a/negotiation.py` | inbound/outbound version selection | PRT1 |
| `a2a/convert.py` | v1 ⇄ internal codecs (lossy fields listed in docstring) | PRT1 |
| `a2a/card.py` *(extend)* | v1 card, per-tenant interfaces | PRT1 |
| `a2a/server.py` *(extend)* | dual-stack dispatch, tenant check, `tasks/list` | PRT2 |
| `a2a/task_store.py` | `A2ATaskStorePort`, `InMemoryA2ATaskStore` | PRT2 |
| `a2a/oauth.py` | `PkceAuthorizationCodeFlow`, `DeviceCodeFlow`, `AuthorizationPrompter` | PRT2 |
| `a2a/client.py` *(extend)* | `protocol=`, `tenant=`, `prompter=`, `list_tasks()` | PRT2 |
| `mcp/protocol.py`, `mcp/constants.py` | adapter Protocol + legacy/stateless adapters + `select_adapter` | PRT3 |
| `mcp/tool_cache.py` | TTL + scope + content-hash tool-list cache | PRT3 |
| `mcp/tasks.py`, `mcp/input_requests.py` | tasks extension poller; input-request handler port | PRT4 |
| `events/__init__.py`, `events/agui.py` | AG-UI models + translator (no HTTP) | PRT5 |
| `monitoring/run_summary.py` | `get_run_summary`, `RunSummary.to_usage_wire` | PRT5 |
| `core/config.py`, `core/exceptions.py`, `monitoring/otel.py` | settings, errors, counters | all |

(`prismal/events/` is a regular subpackage; the top-level `prismal/` stays a PEP 420 namespace.)

### 3.2 A2A dual-stack data flow

```
host POST /a2a  (payload, headers, org_id from auth)
      │
      ▼
A2AServerHandler.handle_rpc(payload, auth=…, headers=…, org_id=…)
      │
      ├─ negotiate_inbound(payload, headers) ──► None ──► JSON-RPC -32009 (legacy refused)
      │
      ├─ "0.3" ──► existing v0.3 dispatch (unchanged code path)
      │
      └─ "1.0" ──► tenant check (payload.tenant == org_id ?) ── no ──► -32001 + audit
                     │ yes
                     ├─ SendMessage / message:send ─► convert.from_v1 ─► _run_task ─► convert.to_v1
                     ├─ GetTask / CancelTask ───────► A2ATaskStorePort.get / existing cancel
                     └─ ListTasks ──────────────────► A2ATaskStorePort.list(org_id, params, principal)

outbound:  A2AClient(protocol="auto") ─► GET agent-card.json ─► select_outbound(card)
             ├─ "1.0": v1 envelopes, tenant=…, OAuth flow from card.security_schemes
             └─ "0.3": unchanged legacy client
```

Method names (`message/send` vs `SendMessage`-style) are pinned in `a2a/v1/constants.py` at PRT0;
the dispatch table is keyed by those constants only.

### 3.3 MCP adapter, discovery and TTL cache

```
McpToolProvider.get_tools(agent_name, capabilities)          (Fase Y, sync, must not raise)
      │
      ▼
McpToolListCache.get(server, org_id, principal) ── hit ──► CachedToolList (same object, same bytes)
      │ miss / expired
      ▼
select_adapter(server, settings)   (cached per server)
      ├─ mode=legacy ─────────────► LegacySessionAdapter (initialize + ClientSession; today)
      ├─ mode=stateless ──────────► StatelessHttpAdapter (server/discover; no session)
      └─ mode=auto ─► server/discover ─ ok+stateless ─► StatelessHttpAdapter
                                     └ -32601 / 404 / 405 ─► LegacySessionAdapter
      │
      ▼
adapter.list_tools()  → ToolListPage(tools, ttl_ms, cache_scope)
      │
      ▼
canonicalize (sort by name, sort_keys JSON) → xxhash64
      │
      ├─ hash == previous → keep previous CachedToolList, extend expiry
      └─ hash != previous → new CachedToolList, prismal.mcp_tool_list_changes_total{server}++
```

Tool calls: `adapter.call_tool(name, args, allow_task=mcp_tasks_enabled)` → immediate result, or a
`McpTaskHandle` that `McpTaskPoller.wait()` drives via `tasks/get` until terminal. Input requests
(multi-round-trip) are answered by the injected `InputRequestHandler`; the default declines.

### 3.4 AG-UI translation

```
host: for chunk in graph.astream(input, config, stream_mode=["messages","updates","custom"]):
          events = to_agui_events(chunk, ctx)        ← engine, pure
          frame(events)                              ← host (SSE / WebSocket)
      finish_run(ctx, summary=get_run_summary(thread_id))
```

| astream input | AG-UI output |
|---|---|
| first `AIMessageChunk` with text in a message | `TEXT_MESSAGE_START` + `TEXT_MESSAGE_CONTENT` |
| subsequent text chunks | `TEXT_MESSAGE_CONTENT` |
| chunk with `tool_call_chunks` (new id) | `TOOL_CALL_START`, then `TOOL_CALL_ARGS` per args fragment |
| message end / tool chunk closed | `TEXT_MESSAGE_END` / `TOOL_CALL_END` |
| `ToolMessage` in `messages` mode | `TOOL_CALL_RESULT` (content passed as produced) |
| `updates` `{node: {...}}` | `STEP_STARTED{stepName=node}` … `STEP_FINISHED` (+ `STATE_DELTA` RFC 6902 vs `last_state`) |
| Skynet worker / MoA proposer / spawner start/end (custom events) | `SUBAGENT_STARTED` / `SUBAGENT_FINISHED` |
| `__interrupt__` update | `INTERRUPT` (HITL payload, resume token = interrupt id) |
| `metadata.routing` / `metadata.decision` changes | `CUSTOM prismal.routing` / `CUSTOM prismal.decision` |
| anything else | `RAW` (`event` = original payload, JSON-safe) |
| `finish_run` | close open items → usage (`CUSTOM prismal.usage` [+ native]) → `RUN_FINISHED` |

`STATE_SNAPSHOT` is emitted once after `RUN_STARTED` when `include_state_deltas` is on, with a
filtered state view (no `messages`, no live objects, no `metadata.security`).

## 4. Design Decisions

### DD-PRT-001: PRT0 verification gates all coding
All protocol facts are post-mid-2026 and partly single-source. PRT0 reads the official texts, captures
example payloads as fixtures, and writes `ADR-PRT-001` with pinned versions and names. Constants
modules are the only place those names appear.

### DD-PRT-002: A2A v1 is an edge codec over the existing internal model
v0.3 models remain the internal canonical shape until `4.0.0`. v1 requests are decoded at the edge;
`_run_task`, sanitization and audit are shared. v1-only concepts (tenant, list pagination) travel in
handler arguments, not in the v0.3 models. Lower risk than rewriting Phase I.

### DD-PRT-003: Negotiation is explicit, deterministic and per request/card
Inbound: header > method-name family > setting. Outbound: remote card `protocolVersion`. No probing
with trial requests (avoids side effects of a mis-negotiated `message/send`).

### DD-PRT-004: v0.3 deprecated, not removed
`a2a_accept_legacy=True` by default. When `a2a_protocol_version="1.0"`, v0.3 traffic is still served
and counted; a `DeprecationWarning` is emitted only at settings load when the operator sets
`a2a_protocol_version="0.3"` explicitly after `4.0.0` is announced. Removal is a `4.0.0` item.

### DD-PRT-005: Tenant mismatch is fail-closed
The host resolves `org_id` from authentication; a v1 `tenant` that disagrees is rejected and audited.
The engine never trusts a client-supplied tenant to select a `RuntimeContext`.

### DD-PRT-006: Interactive OAuth through an injected prompter
PKCE (`S256` only) and device code need a human. The engine exposes `AuthorizationPrompter`; hosts
implement it (dashboard redirect handler, TUI prints device code). Missing prompter ⇒ `A2AOAuthError`.

### DD-PRT-007: MCP adapter below the provider, legacy default
`mcp_protocol_mode="legacy"` reproduces today's session path exactly. `auto` uses a single
`server/discover` probe. stdio servers stay legacy (stateless HTTP only applies to HTTP transports).

### DD-PRT-008: Tool lists change only when TTL expires AND content changes
Feeds TKE2/TKE3 cache stability: identical content returns the identical object; ordering is sorted
by name; canonical JSON. A server that churns descriptions every request is still bounded by TTL.

### DD-PRT-009: Principal-scoped cache entries never cross tenants
`cacheScope` values that are not explicitly shared map to `principal`, keyed by `(server, org_id,
principal)`. Unknown scope value ⇒ `principal` (fail-closed).

### DD-PRT-010: Input requests are untrusted and declined by default
Elicitation messages and URLs are remote content: sanitized, audited by hash, never auto-answered or
auto-opened. Sampling/roots are not implemented for stateless servers (deprecated upstream).

### DD-PRT-011: AG-UI translator is pure; the host owns the wire
No HTTP, SSE or WebSocket code in the engine. The translator is sync and never raises; its only state
is `AgUiContext`, created per run by the caller and never checkpointed.

### DD-PRT-012: One usage wire object for every surface
`RunSummary.to_usage_wire()` defines the 12-key object used by the host `done` event, `/v1/usage`
entries, the AG-UI `prismal.usage` custom event and the SDK `Usage` model.

## 5. Security & cost

- All remote A2A content keeps flowing through `InputSanitizer` + `AuditLogger` in `_run_task`;
  v1 decoding happens before sanitization, never after.
- OAuth: `S256` PKCE, random `state`, token endpoint over HTTPS only (except `localhost` in tests),
  refresh tokens in `CredentialVault`, never logged; audit stores flow + token-hash prefix.
- MCP stateless adapter sends credentials per request (no long-lived session); secrets resolved via
  `resolve_secret()` at call time, never cached in the tool cache.
- Tool cache stores schemas only, never results; principal-scoped entries are tenant-isolated.
- Tasks extension: poll interval floor (`≥ 0.25 s`) and timeout prevent runaway polling; wall-clock
  of long tasks reported to `BudgetGuard` (Phase C) so budgets still apply.
- AG-UI `STATE_SNAPSHOT`/`STATE_DELTA` use an allowlisted state view (no secrets, no security
  metadata, no canary values). Cost: translator is CPU-only (target p95 ≤ 50 µs/chunk).
- Cost benefit: stable tool lists raise provider prompt-cache hit rate for MCP-heavy agents (TKE KPI).

## 6. Observability

Counters per SPEC-PRT-OTEL-001. Spans `prismal.mcp.discover`, `prismal.mcp.task_wait`; negotiation is an
attribute on `prismal.a2a.inbound`; translator: counters only. Logs never include tokens, elicitation text or tool args.

## 7. Relationship to existing specs

- **`a2a-interop/` (Phase I)** — extended in place; v0.3 behaviour frozen by its existing tests.
- **`tool-provider-injection/` (Fase Y)** — `McpToolProvider` gains the cache/adapter internally;
  `ToolProviderPort` unchanged.
- **`token-efficiency/` (TKE)** — TKE1 fields consumed by `to_usage_wire`; TKE3 deferred tool loading
  respects `ttlMs`/`cacheScope` through `McpToolListCache` (forward reference closed here).
- **`llm-gateway-providers/` (GW)** — supplies `served_model`, `gateway`, `cost_source`.
- **`typed-decision-models/` (DEC) / `adaptive-model-routing/` (AMR)** — metadata surfaced as
  `CUSTOM prismal.decision` / `prismal.routing` events when present.
- **`composition-root/` (Fase R)** — per-tenant handler/card; `build_runtime` unchanged in signature.
- **Satellites** — `prismal-server` RH2-3 (AG-UI framing), RH2-4 (A2A v1 routes), RH2-2 (usage);
  `prismal-sdk` CS2.

## 8. Testing strategy (summary; detail in TASKS)

- Golden fixtures from official spec examples (A2A v1, MCP 2026-07-28, AG-UI 1.0) captured at PRT0.
- Unit: codecs round-trip, negotiation table, tenant mismatch, `tasks/list` pagination isolation,
  PKCE verifier/challenge, device-code polling with fake clock, adapter selection, cache TTL/hash/scope,
  task poller backoff/timeout/cancel, translator transcripts (12 fixtures), usage wire shape.
- Integration (fakes, no network): fake stateless + legacy MCP servers over `httpx.MockTransport`;
  v0.3 ⇄ v1 cross-talk between two in-process prismal handlers.
- Interop (`interop` marker, opt-in): official conformance/TCK suites if published **[verify]**;
  a reference AG-UI client parsing translator output.
- Snapshot: all PRT defaults ⇒ graph, card and MCP request sequence unchanged.

## 9. Rollout

1. Ship with defaults (`"0.3"`, `legacy`, flags off) — no observable change.
2. **Shadow:** `mcp_protocol_mode="auto"` + `mcp_tool_cache_enabled=True` in staging; compare tool
   lists and call outcomes against legacy (counter parity), watch cache-hit ratio.
3. **Warn:** `a2a_protocol_version="1.0"` with `a2a_accept_legacy=True`; monitor legacy share via
   `prismal.a2a_requests_total{protocol_version="0.3"}`.
4. **Enforce:** per deployment, `mcp_tasks_enabled`, `agui_events_enabled` (host opts into AG-UI
   mode); `a2a_accept_legacy=False` only once legacy traffic is zero.

## 10. Open questions

1. Exact v1 JSON-RPC method names and whether the version travels in a header or only in the card
   (PRT0-01).
2. Whether AG-UI 1.0 defines a native usage event and its field names (PRT0-03).
3. Does the `mcp` Python SDK support 2026-07-28 at PRT3 start? If yes, prefer it over in-house `httpx` (PRT0-02).
4. Persistent (SQLite) `tasks/list` store for multi-worker hosts? Default in-memory; SQLite is a follow-up.
