# Prismal Protocol Upgrades 2026 — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-06, v3.17.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | PRT |
| **Target package version** | `3.17.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/protocol-upgrades-2026/PLAN.md` |
| **SPEC** | `specs/protocol-upgrades-2026/SPEC.md` |
| **Architecture** | `specs/protocol-upgrades-2026/ARCHITECTURE.md` |

## 1. Implementation Summary

> **Implemented 2026-10-06 (v3.17.0).** PRT0 outcomes in `docs/adr/ADR-PRT-001-protocol-pins.md` amend the [verify] names below. The fake-MCP-server e2e (PRT6-04) lives under `tests/unit/mcp/` (it needs no running service); the A2A cross-talk (PRT6-05) under `tests/integration/a2a/`.

Seven phases: blocking PRT0 verification, then parallel tracks A2A (PRT1→2), MCP (PRT3→4), AG-UI
(PRT5); PRT6 closes. Every wiring point sits behind a default-off setting, so `main` stays green and
the graph snapshot is unchanged throughout. TDD for every task. Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Reuse, do not fork: `a2a/*`, `prismal/mcp/`, `McpToolProvider`, `CredentialVault`, `RunSummary` (TKE1), `monitoring/otel.py`.
- **Verify before build** — every **[verify]** fact gets a closing task in PRT0; no PRT1+ task starts
  until its row below is `DONE` and recorded in `docs/adr/ADR-PRT-001-protocol-pins.md`:

| Verify item | Closing task |
|---|---|
| A2A v1.0 release date/content, v1.0.1 patch delta, JSON-RPC method names, version header, `tenant` field placement, `tasks/list` params, OAuth PKCE/device-code card schema, error codes (`-32009`?) | PRT0-01 |
| MCP 2025-11-25 (async tasks, URL elicitation, sampling with tools, CIMD) and 2026-07-28 (stateless, `server/discover`, `ttlMs`/`cacheScope` values, tasks extension id + `tasks/get`, multi-round-trip shape, deprecation window); `mcp` SDK version supporting it | PRT0-02 |
| AG-UI 1.0 release, the 31 event names and fields, SSE encoding, native usage event, interrupt/HITL event, published JSON schema | PRT0-03 |
| Code facts: `PROTOCOL_VERSION = "0.3.0"`, handler task-registry name, MCP session lifecycle, `RunSummary` TKE1/GW fields, existing accessor name | PRT0-04 |
| Official conformance/TCK suites for A2A v1 / MCP 2026-07-28 / AG-UI 1.0 exist and are runnable offline | PRT0-01/02/03 |

## 3. Implementation Phases

### PHASE PRT0 — Spec verification + ADR

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT0-01 | Read A2A v1.0 + v1.0.1 spec and release notes; pin names in a draft `a2a/v1/constants.py` table | 0.5 d | — | A2A-001/002, OAU-001 | DONE |
| PRT0-02 | Read MCP 2025-11-25 and 2026-07-28 texts; check `mcp` SDK changelog for 2026-07-28 support | 0.5 d | — | MCP-001, MTC-001, MTK-001/002 | DONE |
| PRT0-03 | Read AG-UI 1.0 spec; list the 31 events + fields; locate JSON schema | 0.4 d | — | AGU-001/002, USG-002 | DONE |
| PRT0-04 | Re-confirm code facts (A2A version, task registry, MCP session, `RunSummary` fields) | 0.2 d | — | A2A-005, USG-001 | DONE |
| PRT0-05 | Capture golden fixtures under `tests/fixtures/protocols/`; write `ADR-PRT-001` with pins | 0.4 d | PRT0-01..04 | all | DONE |

**Done when:** ADR merged; every verify row closed or the dependent requirement re-scoped in SPEC.

### PHASE PRT1 — A2A v1 types + card

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT1-01 | `core/config.py`: `a2a_protocol_version`, `a2a_accept_legacy`, `a2a_tasks_list_max_page_size`, `a2a_inbound_auth_schemes`, `a2a_oauth_redirect_uri` + `_validate_protocol_upgrades` | 0.2 d | PRT0-05 | CFG-001 | DONE |
| PRT1-02 | `core/exceptions.py`: `ProtocolUpgradeError` hierarchy | 0.2 d | — | ERR-001 | DONE |
| PRT1-03 | `a2a/v1/constants.py` + `a2a/v1/types.py` (all v1 models, `extra="allow"`) | 0.8 d | PRT0-05 | A2A-001 | DONE |
| PRT1-04 | `a2a/negotiation.py`: `negotiate_inbound`, `select_outbound` | 0.4 d | PRT1-03 | A2A-002 | DONE |
| PRT1-05 | `a2a/convert.py`: v1 ⇄ internal codecs; lossy-field list | 0.6 d | PRT1-03 | A2A-001/004 | DONE |
| PRT1-06 | `build_agent_card(..., protocol_version=)`; per-tenant interfaces; cache key incl. version | 0.4 d | PRT1-03 | A2A-003 | DONE |

**Done when:** fixtures round-trip; v0.3 card snapshot unchanged; v1 card tenant-scoped.

### PHASE PRT2 — A2A dual-stack, `tasks/list`, OAuth

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT2-01 | `a2a/task_store.py`: `A2ATaskStorePort`, `TaskRecord`, `InMemoryA2ATaskStore` over the existing registry | 0.5 d | PRT0-04 | A2A-005 | DONE |
| PRT2-02 | `A2AServerHandler.handle_rpc(..., headers=, org_id=)` dual-stack dispatch | 0.8 d | PRT1-04/05 | A2A-004 | DONE |
| PRT2-03 | Tenant mismatch → `-32001` + audit `a2a.inbound.tenant_mismatch` | 0.3 d | PRT2-02 | A2A-004 | DONE |
| PRT2-04 | `tasks/list` with pagination, tenant + principal scoping | 0.4 d | PRT2-01/02 | A2A-005 | DONE |
| PRT2-05 | `stream_rpc` v1 streaming envelopes | 0.4 d | PRT2-02 | A2A-004 | DONE |
| PRT2-06 | `a2a/oauth.py`: `AuthorizationPrompter`, `PkceAuthorizationCodeFlow` (S256, vault refresh) | 0.6 d | PRT1-02 | OAU-001 | DONE |
| PRT2-07 | `DeviceCodeFlow` (polling, `slow_down`, expiry) | 0.4 d | PRT2-06 | OAU-001 | DONE |
| PRT2-08 | `A2AClient(protocol=, tenant=, prompter=)`, `list_tasks()`, card-driven OAuth selection | 0.6 d | PRT1-04, PRT2-06 | A2A-006 | DONE |
| PRT2-09 | `A2AToolProvider`/`A2AAgentNode` work against v1 peers (no API change) | 0.3 d | PRT2-08 | A2A-006 | DONE |

**Done when:** two in-process handlers (v0.3, v1) interoperate both ways; tenant isolation proven;
headless PKCE without prompter fails closed.

### PHASE PRT3 — MCP protocol adapter + TTL tool cache

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT3-01 | `mcp_*` settings + validation | 0.2 d | PRT0-05 | CFG-001 | DONE |
| PRT3-02 | `mcp/constants.py`, `ServerDiscovery`, `ToolListPage`, `McpProtocolAdapter` Protocol | 0.4 d | PRT0-05 | MCP-001 | DONE |
| PRT3-03 | `LegacySessionAdapter` wrapping today's session path (byte-for-byte request sequence) | 0.6 d | PRT3-02 | MCP-001, GRD-001 | DONE |
| PRT3-04 | `StatelessHttpAdapter` (`server/discover`, per-request headers, pagination, errors) | 1.0 d | PRT3-02 | MCP-001 | DONE |
| PRT3-05 | `select_adapter` (`auto` probe, stdio ⇒ legacy, per-server memo) | 0.5 d | PRT3-03/04 | MCP-001 | DONE |
| PRT3-06 | `mcp/tool_cache.py`: TTL, scope, canonical hash, identity-preserving refresh, LRU | 0.8 d | PRT3-02 | MTC-001 | DONE |
| PRT3-07 | Wire adapter + cache into `McpToolProvider` behind flags | 0.5 d | PRT3-05/06 | MCP-001, MTC-001 | DONE |

**Done when:** `auto` sends zero `initialize` to stateless fakes; 100 rebuilds within TTL yield
byte-identical tool blocks; principal entries isolated per tenant.

### PHASE PRT4 — MCP tasks extension + input requests

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT4-01 | `McpTaskHandle`, `call_tool(..., allow_task=)` in both adapters | 0.4 d | PRT3-04 | MTK-001 | DONE |
| PRT4-02 | `McpTaskPoller.wait` (backoff, `poll_hint_ms`, timeout ⇒ cancel + `McpTaskTimeout`) | 0.7 d | PRT4-01 | MTK-001 | DONE |
| PRT4-03 | Run cancellation ⇒ `cancel_task`; wall-clock reported to `BudgetGuard` | 0.3 d | PRT4-02 | MTK-001 | DONE |
| PRT4-04 | `InputRequestHandler`, `DeclineAllInputHandler`, sanitization + hash audit | 0.6 d | PRT3-04 | MTK-002 | DONE |
| PRT4-05 | `mcp_tasks_enabled=False` ⇒ task-only tools filtered + counted | 0.2 d | PRT4-01 | MTK-001 | DONE |
| PRT4-06 | Stateless mode declines sampling/roots; logging ignored; legacy unchanged | 0.2 d | PRT4-04 | MTK-002 | DONE |

**Done when:** fake long tool completes via polling; timeout cancels; elicitation declined + audited.

### PHASE PRT5 — AG-UI translator + usage wire

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT5-01 | Capture 12 golden `astream` transcripts (ReAct, tools, Skynet, MoA, HITL interrupt, error) | 0.4 d | PRT0-03 | AGU-002 | DONE |
| PRT5-02 | `events/agui.py` models (`AgUiEventType`, base + subclasses, `extra="allow"`) | 0.6 d | PRT0-03 | AGU-001 | DONE |
| PRT5-03 | `to_agui_events`: text + tool-call mapping, `start_run`, `error_run` | 0.8 d | PRT5-02 | AGU-002 | DONE |
| PRT5-04 | Steps, `STATE_SNAPSHOT`, `STATE_DELTA` (RFC 6902) over allowlisted state view | 0.5 d | PRT5-03 | AGU-002 | DONE |
| PRT5-05 | Sub-agent events, `INTERRUPT`, `CUSTOM prismal.routing/decision`, `RAW` fallback | 0.5 d | PRT5-03 | AGU-002 | DONE |
| PRT5-06 | `monitoring/run_summary.py`: `get_run_summary`, `RunSummary.to_usage_wire` (12 keys) | 0.4 d | PRT0-04 | USG-001 | DONE |
| PRT5-07 | `finish_run` usage emission (`prismal.usage` + native if defined) | 0.2 d | PRT5-03/06 | USG-002 | DONE |
| PRT5-08 | `agui_*` settings | 0.2 d | — | CFG-001 | DONE |

**Done when:** 12 transcripts match fixtures; START/END balanced; usage wire has exactly 12 keys.

### PHASE PRT6 — Interop, guards, docs

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| PRT6-01 | Register the 11 PRT counters/histograms in `OTelManager` | 0.3 d | PRT1–5 | OTEL-001 | DONE |
| PRT6-02 | AST guard: `prismal/events/**` imports no HTTP library | 0.2 d | PRT5 | GRD-001 | DONE |
| PRT6-03 | Snapshot: all PRT defaults ⇒ graph, v0.3 card, MCP request sequence unchanged | 0.4 d | PRT1–5 | GRD-001 | DONE |
| PRT6-04 | Integration: fake stateless + legacy MCP servers (`httpx.MockTransport`) | 0.5 d | PRT3, PRT4 | MCP-001, MTK-001 | DONE |
| PRT6-05 | Integration: v0.3 ⇄ v1 cross-talk between two in-process handlers | 0.4 d | PRT2 | A2A-004/006 | DONE |
| PRT6-06 | `interop` marker: official conformance/TCK suites + AG-UI reference client parse | 0.5 d | PRT2–5 | all | DONE |
| PRT6-07 | Usage-wire contract fixture `tests/fixtures/protocols/usage_wire.json` (shared with server/SDK) | 0.2 d | PRT5-06 | USG-001 | DONE |
| PRT6-08 | `docs/protocols-2026.md` (A2A dual-stack, MCP modes, AG-UI mapping, rollout) | 0.4 d | all | — | DONE |
| PRT6-09 | `examples/agui_translate.py`, notebook in `prisma-notebooks`, README/CHANGELOG (as planned) | 0.4 d | PRT6-08 | — | DONE (notebook in `prisma-notebooks` pending — out of repo) |

**Done when:** `uv run pytest -m "unit or integration"` green; ruff, `mypy --strict`, bandit clean;
coverage ≥ 80 % on new modules; snapshot (PRT6-03) green.

## 4. Test Inventory (planned)
| Test module(s) | Covers |
|---|---|
| `tests/unit/core/test_{config,exceptions}_protocol_upgrades.py` | PRT1-01/02, PRT3-01, PRT5-08 |
| `tests/unit/a2a/v1/test_types_fixtures.py`, `tests/unit/a2a/test_{negotiation,convert_v1,card_v1}.py` | PRT1-03..06 |
| `tests/unit/a2a/test_{task_store,server_dual_stack,oauth_flows,client_v1}.py` | PRT2-01..09 |
| `tests/unit/mcp/test_{protocol_adapters,tool_cache,tasks_poller,input_requests}.py`, `tests/unit/agents/extension/test_mcp_provider_cache.py` | PRT3-02..07, PRT4-01..06 |
| `tests/unit/events/test_agui_{models,translator}.py` | PRT5-01..05, PRT5-07 |
| `tests/unit/monitoring/test_{usage_wire,otel_protocol_counters}.py` | PRT5-06, PRT6-01, PRT6-07 |
| `tests/unit/events/test_no_http_imports.py`, `tests/unit/agents/test_graph_snapshot_protocol_upgrades.py` | PRT6-02, PRT6-03 |
| `tests/unit/mcp/test_stateless_and_legacy_servers.py`, `tests/integration/a2a/test_v03_v1_crosstalk.py` | PRT6-04, PRT6-05 |
| `tests/interop/test_conformance_suites.py` (`interop` marker, opt-in) | PRT6-06 |

## 5. Risk Register (implementation)
| Risk | Mitigation |
|---|---|
| PRT0 finds names/fields differ from this SPEC | Constants modules isolate names; SPEC amended in the ADR-PRT-001 PR |
| `mcp` SDK incompatible with the stateless wire | In-house `httpx` adapter behind the Protocol; SDK kept for legacy |
| astream chunk shapes vary across LangGraph versions | Transcripts pinned per version; `RAW` fallback; re-capture on bump |
| OAuth flows untestable without an IdP / multi-worker task store | Fake IdP + fake clock; port allows a SQLite task-store adapter later |

## 6. Definition of Done (feature)
- [x] ADR-PRT-001 merged; all verify rows closed; every MUST requirement has ≥ 1 task and test (§8).
- [x] Defaults ⇒ v0.3 A2A, legacy MCP and graph unchanged (snapshot + existing suites).
- [x] Usage wire fixture published and referenced by `prismal-server` RH2 and `prismal-sdk` CS2.
- [x] `prismal/events/**` HTTP-free; import guards pass; ruff/`mypy --strict`/bandit clean; coverage ≥ 80 %.

## 7. Effort Summary

| PRT0 | PRT1 | PRT2 | PRT3 | PRT4 | PRT5 | PRT6 | **Total** |
|---|---|---|---|---|---|---|---|
| 2.0 d verify + ADR | 2.6 d A2A v1 types/card | 4.3 d dual-stack + OAuth | 4.0 d MCP adapter + cache | 2.4 d MCP tasks | 3.6 d AG-UI + usage | 3.3 d interop + docs | **~22.2 d** |

## 8. Traceability Matrix

| SPEC | Tasks | Tests |
|---|---|---|
| A2A-001, A2A-002 | PRT0-01, PRT1-03..05 | `test_types_fixtures.py`, `test_convert_v1.py`, `test_negotiation.py` |
| A2A-003 | PRT1-06 | `test_card_v1.py`, `test_graph_snapshot_protocol_upgrades.py` |
| A2A-004, A2A-005 | PRT2-01..05, PRT6-05 | `test_task_store.py`, `test_server_dual_stack.py`, `test_v03_v1_crosstalk.py` |
| A2A-006, OAU-001 | PRT2-06..09 | `test_oauth_flows.py`, `test_client_v1.py` |
| MCP-001, MTC-001 | PRT3-02..07, PRT6-04 | `test_protocol_adapters.py`, `test_tool_cache.py`, `test_mcp_provider_cache.py`, `test_stateless_and_legacy_servers.py` |
| MTK-001, MTK-002 | PRT4-01..06 | `test_tasks_poller.py`, `test_input_requests.py` |
| AGU-001, AGU-002, USG-002 | PRT5-01..05, PRT5-07 | `test_agui_models.py`, `test_agui_translator.py` |
| USG-001 | PRT5-06, PRT6-07 | `test_usage_wire.py` |
| CFG-001, ERR-001 | PRT1-01/02, PRT3-01, PRT5-08 | `test_config_protocol_upgrades.py`, `test_exceptions_protocol_upgrades.py` |
| OTEL-001 | PRT6-01 | `test_otel_protocol_counters.py` |
| GRD-001 | PRT3-03, PRT6-02, PRT6-03 | `test_no_http_imports.py`, `test_graph_snapshot_protocol_upgrades.py` |

(All IDs are `SPEC-PRT-<AREA>-NNN`.)
