# Prismal A2A Interoperability — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/a2a-interop/PLAN.md` |
| **Architecture** | `specs/a2a-interop/ARCHITECTURE.md` |
| **SPEC** | `specs/a2a-interop/SPEC.md` |
| **Related** | Phase X, Phase Y, Phase R, `specs/agent-identity-governance/` |

---

## 1. Implementation Summary

Phase I adds bidirectional A2A interop in a new subpackage `prismal/a2a/` (`[a2a]` extra), reusing the adapter-as-node (Phase X), the `ToolProviderPort` (Phase Y), and the connection manager from `mcp/`. **Additive, gated (`a2a_enabled=False`), everything remote goes through L1–L5.** The HTTP is mounted by `prismal-server`; the core provides handler + types + client.

---

## 2. Prerequisites

- Phase X: `@prismal_node`, adapter-as-node pattern. (extension implemented)
- Phase Y: `ToolProviderPort` + `CompositeToolProvider` (for `A2AToolProvider`).
- Phase R: `build_runtime` (to compose A2A + multi-tenant) — recommended.
- `specs/agent-identity-governance/`: DID (minimum for the card) — coordinate.
- Decision: use the official A2A SDK vs our own subset (see PA-2).

---

## 3. Implementation Phases

### PHASE I1 — Domain types
- [x] `prismal/a2a/types.py`: `AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart` (Pydantic v2, compliant with v0.3.x).
- **Done:** `model_dump`/`model_validate` round-trip against spec examples.

### PHASE I2 — Agent Card
- [x] `card.py::build_agent_card(settings, registry, org_id=)` derives skills from the registry + `a2a_published_skills` allowlist; auth + did + modalities; cache per org.
- **Done:** valid card (conformance) for a test registry.

### PHASE I3 — Inbound (server handler)
- [x] `server.py::A2AServerHandler.handle_rpc` (`message/send`, `tasks/get`, `tasks/cancel`).
- [x] `_run_task`: sanitize -> invoke graph(thread=task_id) -> SSE artifacts -> audit.
- [x] skill_id -> subgraph/entry mapping.
- **Done:** fake JSON-RPC -> expected SSE artifacts; mandatory auth in strict.

### PHASE I4 — Outbound (client + node)
- [x] `client.py::A2AClient` (discover, send_task SSE, cancel, auth).
- [x] `A2AConnectionManager` (allowlist, pool, retry) — mirror of `mcp/connection.py`.
- [x] `A2AAgentNode.as_node()` (@prismal_node; map state<->task; error=True without breaking).
- **Done:** fake A2A server -> node integrates artifacts into the state; allowlist enforced.

### PHASE I5 — A2AToolProvider (Phase Y)
- [x] `provider.py::A2AToolProvider.get_tools` exposes remote skills as `BaseTool`; conforms to `ToolProviderPort`; deferred import; capture -> [].
- **Done:** composable in `CompositeToolProvider`; `react_loop` executes a remote skill.

### PHASE I6 — Security and identity
- [x] Auth out (OAuth client-credentials / mTLS / bearer) + in (caller validation).
- [x] DID in the card (consumes `agent-identity-governance`).
- [x] Deny-all allowlist in strict; every remote artifact -> `InputSanitizer`/`SecurePromptBuilder`; tool-calls -> `ActionInterceptor`.
- [x] `AuditLogger.log_event` per task (in/out), without secrets.
- **Done:** test of neutralized remote injection; test of blocked dangerous tool.

### PHASE I7 — Settings + Phase R
- [x] Settings `a2a_*` (default off).
- [x] `A2AError` / `A2AAgentUnavailable`.
- [x] `build_runtime`: composes `A2AToolProvider` (if outbound) and exposes `A2AServerHandler` (if inbound) in `RuntimeContext`; card per org.

### PHASE I8 — Docs + examples + tests
- [x] `docs/a2a.md` (expose prismal; consume remotes; security).
- [x] `examples/a2a_server.py`, `examples/a2a_remote_node.py`.
- [x] Fake A2A server for tests (httpx mock); card/handler conformance.

### HARDENING
- [x] Coverage ≥ 85% in `prismal/a2a/**`.
- [x] `ruff`/`mypy --strict`/`bandit` clean; `pytest -m "not live_api"` 100%.
- [x] `[a2a]` extra in `pyproject.toml`; deferred imports.
- [x] `CLAUDE.md` + `README.md` + Obsidian notes.

---

## 4. Inter-Task Dependencies

```
I1 (types)
 ├─▶ I2 (card) ──────────────▶ I3 (inbound)
 └─▶ I4 (client/node) ─▶ I5 (provider, needs Phase Y)
I6 (security/identity) ─▶ I3, I4, I5
I7 (settings + Phase R) ─▶ compose all
I8 (docs/tests) [last]
```

Critical path: **I1 → I4 → I6 → I8** (outbound first, without exposing anything). Inbound (I3) after having a host.

---

## 5. Tasks ↔ Requirements Matrix

| Task | RF covered |
|---|---|
| I1 | RF-A2A-001 |
| I2 | RF-A2A-002 |
| I3 | RF-A2A-003, RF-A2A-004 |
| I4 | RF-A2A-005, RF-A2A-006 |
| I5 | RF-A2A-007 |
| I6 | RF-A2A-008, RF-A2A-009, RF-A2A-010 |
| I7 | RF-A2A-011 |
| I8 | RF-A2A-012 |

Coverage: RF-A2A-001..012 mapped.

---

## 6. Risk Matrix

| Risk | Mitigation | Task |
|---|---|---|
| Injection from a remote agent | L1 + ActionInterceptor over artifacts | I6 |
| Inbound exposed without auth | Mandatory auth; default off; strict | I6, I7 |
| Delegation to an unauthorized agent | Deny-all allowlist (strict) | I4, I6 |
| A2A spec drift (v0.x) | Pin v0.3.x; conformance tests | I1, I3 |
| Long tasks block | Timeout + tasks/cancel | I3, I4 |
| Uncontrolled delegation cost | Link `cost-budget-governance` | (cross) |

---

## 7. Definition of Done (Global for Phase I)

- [x] A2A types compliant with v0.3.x; `build_agent_card` valid.
- [x] Inbound (JSON-RPC handler + SSE) mountable by `prismal-server`.
- [x] Outbound (`A2AClient` + `A2AAgentNode` + connection manager).
- [x] `A2AToolProvider` conforms to `ToolProviderPort`.
- [x] Auth + DID + allowlist + auditing; remote goes through L1–L5.
- [x] Settings `a2a_*` (default off); Phase R integration.
- [x] `docs/a2a.md` + 2 examples + fake server tests; coverage ≥ 85%.
- [x] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [x] `CLAUDE.md` + `README.md` + Obsidian updated; PR merged.

---

## 8. Effort Estimate

| Sub-phase | Effort |
|---|---|
| I1 Types | 0.4 wk |
| I2 Card | 0.4 wk |
| I3 Inbound | 1.0 wk |
| I4 Outbound | 1.0 wk |
| I5 Provider | 0.4 wk |
| I6 Security/identity | 0.6 wk |
| I7 Settings + Phase R | 0.3 wk |
| I8 Docs + tests | 0.6 wk |
| Hardening | 0.5 wk |
| **Total** | **~5.2 wk** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-06 | Ernesto Crespo | Initial implementation plan — A2A interop |
