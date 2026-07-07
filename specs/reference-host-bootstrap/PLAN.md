# Prismal — Reference Host Bootstrap (prismal-server minimal viable host)

## Strategic Plan / Product Requirements Document (PLAN) — PRD SEED

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `PRD SEED` — scope only; **no ARCHITECTURE/SPEC/TASKS in this repo** (see §9) |
| **Version** | 0.1 |
| **Date** | 2026-07-04 |
| **Phase** | RHB (Reference Host Bootstrap) |
| **Target package version** | N/A — this spec does **not** ship inside `prismal-ai` (the engine); it scopes a *separate* repository, `prismal-server` |
| **Reviewers** | Tech Lead, AI Architect |
| **Priority** | P0 (production-unblocking) per `docs/gap-analysis-loops-harness-guardrails-2026-07.md`, item #1 |
| **Related** | `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (item #1), `docs/competitive-analysis.md`, `specs/composition-root/`, `specs/a2a-interop/`, `specs/agent-identity-governance/`, `README.md` §"Architecture" (diagram walkthrough, dashed-outline repos) |

---

## 1. Executive Summary

Every other gap this round of Spec-Driven Design addresses (`guardrails-modernization`, `loop-hardening`, `node-io-typesafety`, `observability-integration`) improves the **engine** (`prismal/`, this repository). This one is different in kind: the single largest gap identified in `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (item #1) is that **the engine has nothing to run inside**. `prismal-server`, `prismal-sdk`, `prismal-dashboard`, `prismal-tui`, `prismal-webchat`, and `prismal-chatbot` are all listed in `README.md` as "planned/early-stage repositories" (dashed outline in the architecture diagram) — none of them exist as buildable code today. Without a host, `prismal` is a library that only a Python process embedding it can use; there is no REST/WebSocket/SSE surface, no inbound A2A endpoint (`/.well-known/agent-card.json`, `/a2a`), and no way for `prismal-dashboard`/`prismal-tui`/`prismal-webchat` to exist at all.

This PLAN is intentionally a **seed**, not a full SDD set: it defines *what the engine already guarantees* toward a host, *what the minimal viable host must do*, and *where that work should actually live* (a new repository, not this one) — per the repo's own hard rule (`CLAUDE.md`, "Framework or host?" sections repeated across every phase): **contract/logic → framework (`prismal/`); serving HTTP, authenticating, rendering, persisting config → host.**

---

## 2. Context and Problem

- `build_runtime()` (Phase R, `prismal/composition/runtime.py`) already composes every port a host needs — tool provider, vector store, embeddings, checkpointer, audit, and (once `agent-identity-governance`/`a2a-interop` are enabled) identity + A2A — into one `RuntimeContext` with coordinated `aclose()`. **The engine side of "unblock the host" is done.**
- `get_async_compiled_graph()` is the other half of the contract: a host only needs to call it once per session/tenant and stream from it.
- `A2AServerHandler` (Phase I) already implements `handle_rpc`/`stream_rpc` — a host only needs to mount them at `/a2a` and `/.well-known/agent-card.json` and own auth in front of them.
- None of this is reachable from outside a Python process today. There is no process that binds a port, no auth layer, no session/thread-id mapping to HTTP identities, no SDK a browser or CLI could use, and therefore no dashboard, TUI, web chat, or chatbot connectors can exist — they all sit behind the SDK → server → engine path per the `README.md` architecture diagram.
- **This is the #1 cited "experimentation → production" gap** in `docs/competitive-analysis.md` §3 (item 1) and is unchanged since that analysis (2026-06-06): every other P0–P3 item in that document has since shipped (Y, Z, R, W, C, H, V, IDN, I are all `✅ implemented`); this is the only one that requires code **outside** `prismal-ai`.

---

## 3. Target Users

- **Application developers** who want to call prismal over HTTP/WS/SSE from a non-Python client (web app, mobile app, another service).
- **Platform/SRE** who need one deployable, health-checked, horizontally-scalable process to operate instead of hand-rolling a host around `prismal`.
- **`prismal-dashboard`/`prismal-tui`/`prismal-webchat`/`prismal-chatbot` maintainers**, who are entirely blocked until a server + SDK exist to build against.
- **A2A ecosystem peers** (other agent frameworks) who need a live `/.well-known/agent-card.json` to discover and call a running prismal agent at all — `A2AServerHandler` exists but is unreachable without a host.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| A minimal `prismal-server` process exists and boots | `uvicorn prismal_server.app:app` (or equivalent) serves a health check | Boots from a fresh clone + `build_runtime()` |
| REST/WS/SSE surface over the engine | `POST /threads/{id}/messages` streams tokens/tool-calls via SSE | Round-trips a full turn through `get_async_compiled_graph()` |
| A2A reachable | `/.well-known/agent-card.json` and `/a2a` mounted over `A2AServerHandler` | Passes an external A2A client smoke test |
| Session/tenant mapping | HTTP session ↔ `thread_id`/`org_id` resolved consistently with `build_runtime(org_id=...)` | 1:1, documented |
| Minimal `prismal-sdk` | A thin client wraps the REST/WS/SSE contract | Used by at least one reference front-end (e.g. a CLI smoke client) |
| Framework untouched | Zero changes required inside `prismal/` beyond what Phases R/Y/Z/W/I/IDN already ship | Confirmed by this PLAN's own scope boundary |

---

## 5. Scope

### In scope (for the *seed*, i.e. what this document decides now)
- Confirming — not building — that the engine-side contract is complete enough to host: `build_runtime()`, `get_async_compiled_graph()`, `A2AServerHandler`, `build_agent_card()`.
- Naming the new repository (`prismal-server`) and its minimal surface: health check, one streaming chat endpoint, the A2A endpoints, and auth as a pluggable seam (not a specific IdP choice yet — that is `agent-identity-governance`'s `IdentityPort`/OIDC adapter, already engine-side).
- Naming the companion `prismal-sdk` and its minimal client contract (thin wrapper, no business logic).
- Explicitly identifying what does **not** belong in `prismal-ai` so future contributors don't accidentally build a server inside the engine repo.

### Out of Scope (deferred to the new repository's own SDD set, once bootstrapped)
- Actual server code, HTTP framework choice (FastAPI is the implied default given `starlette`/`aiohttp` are already transitive deps and `A2AServerHandler` speaks JSON-RPC/SSE, but this is the new repo's ARCHITECTURE.md decision, not this seed's).
- `prismal-dashboard`, `prismal-tui`, `prismal-webchat`, `prismal-chatbot` — each is its own future repo/spec, blocked on `prismal-server` + `prismal-sdk` existing first.
- Auth/IdP product decisions (Entra/Okta/etc. wiring is `agent-identity-governance`'s `OidcIdentityProvider`, already engine-side and reusable).
- Deployment topology (containers, k8s, autoscaling) — operational concern for whoever stands up `prismal-server`.

---

## 6. Functional Requirements (summary, for the eventual `prismal-server` SPEC)

| ID | Requirement | Priority |
|---|---|---|
| RF-RHB-001 | A process boots `build_runtime()` once at startup (lifespan) and calls `RuntimeContext.aclose()` on shutdown | `MUST` |
| RF-RHB-002 | A streaming endpoint (SSE or WS) maps one HTTP/WS session to one `thread_id` and streams `get_async_compiled_graph().astream(...)` | `MUST` |
| RF-RHB-003 | `/.well-known/agent-card.json` and `/a2a` are mounted over the existing `A2AServerHandler` when `a2a_enabled` | `MUST` |
| RF-RHB-004 | Auth is a pluggable seam that can resolve to an `AgentIdentity` via the existing `IdentityPort` | `SHOULD` |
| RF-RHB-005 | Per-tenant `org_id` resolution maps to `build_runtime(org_id=...)` collection isolation | `SHOULD` |
| RF-RHB-006 | A minimal `prismal-sdk` wraps the above without adding business logic | `SHOULD` |
| RF-RHB-007 | No new logic duplicated from `prismal/` — the host only composes and serves | `MUST` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Scope creep — building dashboard/TUI features into the server | Hold the line at RF-RHB-001…007; each front-end is its own repo/spec |
| Reinventing engine logic in the host | `build_runtime()`/`get_async_compiled_graph()`/`A2AServerHandler` are the only allowed entry points — enforce via a review checklist in the new repo's own `CLAUDE.md` |
| Two repos drifting (engine changes break the host silently) | Pin `prismal-ai` version in the new repo; add a contract/smoke test against a released `prismal-ai` version in the new repo's CI |
| Never gets bootstrapped (bus factor, single maintainer) | This seed is deliberately small — bootstrapping a "hello world" host is now unblocked and cheap; treat as the very next standalone effort |

---

## 8. Dependencies

- `prismal.composition.runtime.build_runtime()` (Phase R) — done, engine-side.
- `prismal.agents.graph.get_async_compiled_graph()` — done, engine-side.
- `prismal.a2a.server.A2AServerHandler` / `prismal.a2a.card.build_agent_card()` (Phase I) — done, engine-side.
- `prismal.identity` `IdentityPort`/`OidcIdentityProvider` (Phase IDN) — done, engine-side, ready for the host to call.
- A **new** repository, `prismal-server` (and, immediately after, `prismal-sdk`) — does not exist yet; this PLAN is the first artifact toward creating it.

---

## 9. Why this repo does not also get ARCHITECTURE.md / SPEC.md / TASKS.md

Per `CLAUDE.md`'s rule, repeated verbatim in every other phase's PLAN in this repo: **"contract/logic → framework (`prismal/`); serving HTTP, authenticating, rendering, persisting config → host."** `prismal-ai` ships no web server, dashboard, or CLI by design (see `CLAUDE.md`'s first paragraph). Writing a full ARCHITECTURE/SPEC/TASKS set for a FastAPI service *inside* `prismal-ai`'s `specs/` would violate that boundary and mislead contributors into building the host in the wrong repository. This seed's job is only to (a) confirm the engine-side contract is complete — it is — and (b) hand off a scoped, reviewed starting point once `prismal-server` is created. **Next step:** bootstrap the `prismal-server` repository and author its own `specs/reference-host-bootstrap/{ARCHITECTURE,SPEC,TASKS}.md` there, using this PLAN as the seed PRD (mirrors how this repo's own `docs/competitive-analysis.md` §7 originally seeded `agent-identity-governance`, `agent-eval-harness`, and `cost-budget-governance` as PLAN-only before they were expanded in place).

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-07-04 | Ernesto Crespo | Initial PRD seed from gap-analysis (`docs/gap-analysis-loops-harness-guardrails-2026-07.md`, item #1) |
