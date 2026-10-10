# Prismal — Protocol Upgrades 2026 (A2A v1.0, MCP 2026-07-28, AG-UI 1.0)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-06, v3.17.0; wire pins amended by `docs/adr/ADR-PRT-001-protocol-pins.md`) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | PRT (Protocol Upgrades 2026) |
| **Target package version** | `3.17.0` (SemVer minor — new opt-in functionality) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Priority** | P1 (interop with the 2026 agent ecosystem; unblocks `prismal-server` 0.2 AG-UI mode and A2A v1 routes) |
| **Related** | `docs/gap-analysis-gateways-tokens-decisions-2026-10.md` (§2 "Protocols" row, §3.4 last row, §5 row 5), `specs/a2a-interop/` (Phase I — the v0.3 stack this spec makes dual-stack), `specs/tool-provider-injection/` (Fase Y — `McpToolProvider`), `specs/composition-root/` (Fase R — per-tenant `RuntimeContext`), `specs/token-efficiency/` (TKE1 usage fields, TKE3 forward reference to `ttlMs`), `specs/identity/` (IDN — `CredentialVault`), satellite deltas `prismal-server/specs/host-v0.2-usage-gateways-agui/`, `prismal-sdk/specs/client-v0.2-usage-agui/` |

> **Volatility notice.** Every protocol fact in this document post-dates mid-2026 and several are
> single-source. They are tagged **[verify]**. Phase **PRT0 (spec verification + ADR)** is mandatory
> and blocking: no PRT1+ code is written until the official spec texts are read, versions are pinned
> in `docs/adr/ADR-PRT-001-protocol-pins.md`, and every **[verify]** row in TASKS §2 is closed.

---

## 1. Executive Summary

Prismal speaks three agent-interop protocols, all of them at a 2025 revision: A2A **v0.3**
(`prismal/a2a/types.py::PROTOCOL_VERSION = "0.3.0"`), a **session-based** MCP client
(`mcp>=1.28.1`, `prismal/mcp/`), and — through the reference host — a **custom** SSE event set
(`token` / `tool_call` / `state` / `done` / `error`). In 2026 all three standards moved:

1. **A2A v1.0** (2026-03-12; v1.0.1 2026-05-28 **[verify]**) breaks v0.3: multi-tenancy, `tasks/list`,
   OAuth PKCE + device code, `/.well-known/agent-card.json` discovery.
2. **MCP 2026-07-28** **[verify]** makes the protocol **stateless** (sessions and `initialize` are
   removed, replaced by `server/discover`), adds `ttlMs` + `cacheScope` to list/read results, moves
   tasks into an extension polled with `tasks/get`, replaces server-initiated requests with
   multi-round-trip requests, and deprecates sampling / roots / logging with a 12-month window. The
   intermediate 2025-11-25 revision added async tasks, URL-mode elicitation, sampling with tools and
   OAuth Client ID Metadata Documents (CIMD) **[verify]**.
3. **AG-UI 1.0** (2026-09-30) **[verify exact names]** standardises 31 agent→UI events (run, text,
   tool call, state snapshot/delta, sub-agent, HITL interrupt, token usage); emitted by LangChain,
   Google ADK, OpenAI Agents SDK and Microsoft Agent Framework.

**PRT** upgrades the engine to all three as **opt-in, additive, dual-stack** changes: v0.3 A2A keeps
working (deprecated, removal planned for `4.0.0`), the legacy MCP session path stays the default, and
AG-UI is a **pure translator** (`prismal/events/agui.py`) with no HTTP in the engine — the host
chooses the wire. With every PRT flag at its default, the compiled supervisor graph and all existing
outputs are byte-for-byte unchanged (snapshot test).

---

## 2. Context and Problem (code facts, gap analysis §2)

- **A2A pinned to v0.3.** `prismal/a2a/types.py` declares `PROTOCOL_VERSION = "0.3.0"`; models use
  camelCase aliases (`protocolVersion`, `messageId`, …). `A2AServerHandler.handle_rpc` dispatches only
  `message/send`, `tasks/get`, `tasks/cancel`; there is no `tasks/list`, no tenant field on requests,
  and `A2AClient` supports bearer + `oauth2_client_credentials` only. A v1.0 peer that sends a v1
  request or publishes a v1 card is not understood.
- **MCP client is session-bound** (`mcp>=1.28.1`): `initialize` + long-lived `ClientSession`, tools
  re-listed with no freshness notion. A 2026-07-28 server without `initialize` cannot be used.
- **Tool-list churn defeats prompt caching.** The tool block is the first cache-prefix segment (TKE2);
  any MCP reconnect may re-order/re-describe tools. `ttlMs`/`cacheScope` are ignored.
- **No standard UI event stream.** Host `streaming.py` emits five custom SSE events; AG-UI clients
  cannot be reused; sub-agent progress, HITL interrupts and token usage are invisible.
- **Usage is not on the wire.** TKE1 enriches `Usage`/`RunSummary`, but no helper produces a stable
  wire object — host and SDK would invent field names.

## 3. Target Users

- **Platform Host (`prismal-server`)** — dual-stack A2A handler, AG-UI translator to frame as
  SSE/WebSocket, stable usage wire object for the `done` event.
- **Integrator** — call and be called by A2A v1.0 agents (multi-tenant, OAuth-PKCE) without losing v0.3 peers.
- **Tool-server operator** — MCP servers on 2026-07-28 (stateless HTTP) and long tools via the tasks extension.
- **Front-end developer** — off-the-shelf AG-UI clients; sub-agent progress, interrupts, usage.
- **FinOps / Operator** — cache-stable tool schemas and usage on every turn.

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| A2A v1.0 interop | Official A2A v1 conformance/TCK suite (if published **[verify]**) or the PRT6 interop matrix | 100 % of MUST cases pass, inbound and outbound |
| No v0.3 regression | Existing `tests/unit/a2a/**` and Phase I integration tests | 100 % green, unmodified |
| MCP stateless support | Calls against a 2026-07-28 reference server with `mcp_protocol_mode=auto` | 100 % success; 0 `initialize` calls sent to a stateless server |
| Tool-schema stability | Changes of the serialized tool block for an unchanged MCP server across 100 provider rebuilds | 0 (cache hit on every rebuild within TTL) |
| Discovery overhead | Extra round trips per MCP server per process when TTL is honoured | ≤ 1 `server/discover` per `mcp_tool_cache_default_ttl_s` |
| Long tools | MCP tasks-extension tools complete or time out deterministically | 100 % terminal state within `mcp_task_timeout_s` |
| AG-UI fidelity | Translator output validates against the AG-UI 1.0 JSON schema **[verify]** | 100 % of golden fixtures |
| AG-UI overhead | Translator CPU cost per chunk (p95, unit benchmark) | ≤ 50 µs |
| Usage on the wire | `RunSummary.to_usage_wire()` field set identical in engine, host and SDK specs | 12/12 fields, contract test |
| Opt-in safety | All PRT flags at defaults ⇒ supervisor graph snapshot and existing outputs unchanged | 100 % (snapshot test) |

---

## 5. Scope

### In Scope
- **PRT0** — read the official texts of A2A v1.0/v1.0.1, MCP 2025-11-25 and 2026-07-28, AG-UI 1.0;
  pin versions and exact method/event names in `ADR-PRT-001`; record the `mcp` Python SDK version
  that supports 2026-07-28 (or decide to implement the stateless wire in `prismal/mcp/` directly).
- **PRT1** — A2A v1 Pydantic types in `prismal/a2a/v1/`, version negotiation helpers, v1 Agent Card
  (`build_agent_card(..., protocol_version="1.0")`), per-tenant cards aligned with `org_id`.
- **PRT2** — dual-stack inbound handler (v0.3 + v1, `tasks/list` over the existing task store,
  tenant routing), outbound client choosing the stack from the remote card, OAuth authorization-code
  + PKCE and device-code client flows with tokens in `CredentialVault`.
- **PRT3** — MCP `McpProtocolAdapter` (legacy session vs. stateless 2026-07-28) chosen per server by
  discovery; `McpToolListCache` honouring `ttlMs` / `cacheScope` + content hash; stable tool order.
- **PRT4** — MCP tasks extension (create → poll `tasks/get` → result/cancel) for long tools;
  multi-round-trip input requests routed to an injectable handler (default: decline, fail-closed).
- **PRT5** — AG-UI translator `to_agui_events(chunk, ctx)` + typed models; usage, decision and routing
  metadata; `prismal.monitoring.get_run_summary()` + `RunSummary.to_usage_wire()`.
- **PRT6** — interop tests (conformance suites where published), `docs/protocols-2026.md`,
  `examples/agui_translate.py`, notebook in `prisma-notebooks`.

### Out of Scope
- HTTP/WebSocket framing of AG-UI (host concern — `prismal-server` RH2-3/RH2-5).
- Removing A2A v0.3 or the legacy MCP session path (planned for `4.0.0`).
- MCP server-side upgrades for `prismal/mcp/servers/` subprocesses (follow-up).
- MCP Apps UI resources (passed through as opaque tool results); sampling/roots/logging in stateless
  mode (deprecated upstream).
- Interactive browser flows — prompts go to a host-provided `AuthorizationPrompter`.

---

## 6. Phases Overview

| Phase | Focus | Flag(s) | Est. |
|---|---|---|---|
| PRT0 | Spec verification, version pins, ADR, fixture capture | — | ~2.0 d |
| PRT1 | A2A v1 types, negotiation, v1 / per-tenant Agent Card | `a2a_protocol_version` | ~2.6 d |
| PRT2 | Dual-stack inbound/outbound, `tasks/list`, OAuth PKCE + device code | `a2a_accept_legacy` | ~4.3 d |
| PRT3 | MCP protocol adapter, discovery, TTL tool cache | `mcp_protocol_mode`, `mcp_tool_cache_enabled` | ~4.0 d |
| PRT4 | MCP tasks extension, multi-round-trip input requests | `mcp_tasks_enabled` | ~2.4 d |
| PRT5 | AG-UI translator, usage wire, decision/routing metadata | `agui_events_enabled` | ~3.6 d |
| PRT6 | Interop/conformance tests, snapshot, docs, example, notebook | — | ~3.3 d |
| **Total** | | | **~22.2 d** |

Tracks A2A (PRT1–2), MCP (PRT3–4), AG-UI (PRT5) run in parallel after PRT0; server RH2-3 needs PRT5, RH2-4 needs PRT2.

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Single-source or misremembered protocol details (method names, event names, fields) | PRT0 blocks coding; names live in one constants module per protocol; golden fixtures captured from official examples |
| A2A v1 and v0.3 diverge on task/message semantics | v1 is an edge codec over the existing internal model (DD-PRT-002); lossy fields documented and tested |
| `mcp` Python SDK lags the 2026-07-28 spec | Adapter Protocol isolates the wire; stateless adapter may use `httpx` directly behind `[mcp]`; keep `mcp>=1.28.1` floor |
| Stale tool lists after TTL misuse by a server | Content hash + `mcp_tool_cache_max_ttl_s` ceiling; explicit `invalidate()` on `tools/list_changed`-equivalent signals |
| OAuth PKCE/device code used headless | Flows require an injected `AuthorizationPrompter`; absent ⇒ `A2AOAuthError` (fail-closed), never silent downgrade to no auth |
| Multi-round-trip requests used for injection (elicitation text) | Elicitation/URL prompts are untrusted content: L1-sanitized, never auto-answered, default handler declines |
| AG-UI drift (31 events, 1.0 just released) | Typed models `extra="allow"`; unknown chunk kinds map to `CUSTOM`/`RAW`; translator version constant exposed to the host |
| Behaviour leak with flags off | Defaults (`"0.3"`, `legacy`, `False`) gate every wiring point; snapshot test PRT6-03 |

---

## 8. Dependencies

Phase I `prismal/a2a/*`; `prismal/mcp/` + `McpToolProvider` (Y); `build_runtime(..., org_id)` (R);
IDN `CredentialVault` (OAuth secrets/refresh tokens); TKE1 `Usage`/`RunSummary` cache fields; GW
served-model/gateway (optional, `null` without GW); DEC/AMR metadata (optional); `monitoring/otel.py`;
`security/` (`InputSanitizer`, `AuditLogger`).

---

## 9. References

- A2A releases — https://github.com/a2aproject/A2A/releases
- MCP 2025-11-25 update — https://workos.com/blog/mcp-2025-11-25-spec-update
- MCP 2026 spec changes — https://stacktr.ee/blog/mcp-2026-spec-changes
- AG-UI 1.0 — https://www.copilotkit.ai/blog/ag-ui-1.0
- Gap analysis — `docs/gap-analysis-gateways-tokens-decisions-2026-10.md`

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-10-04 | Ernesto Crespo | Initial PLAN from the 2026-10 gap analysis (§2 Protocols, §5 row 5) |
