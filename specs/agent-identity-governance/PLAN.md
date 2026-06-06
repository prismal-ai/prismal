# Prismal — Agent Identity & Access Governance

## Strategic Plan / Product Requirements Document (PLAN) — *seed PRD*

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` (seed PRD; ARCHITECTURE/SPEC/TASKS missing) |
| **Version** | 0.1 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Priority** | P1 (enterprise production blocker) |
| **Related** | `specs/a2a-interop/` (consumes DID), `prismal/security/permissions.py`, `audit.py` |

---

## 1. Executive Summary

The most cited governance gap in 2026 is the **identity and access control of autonomous agents**: teams share human credentials with agents for lack of alternatives. Prismal has `PermissionManager` (TTL grants) and `AuditLogger`, but **has no per-agent identity** (DID), **no per-agent credentials of its own**, nor **OAuth-on-behalf-style delegation**. This feature adds an **identity and access governance** layer so that each agent (internal or exposed via A2A) has a verifiable identity, scoped credentials, and auditable access policies — a foundation of trust for multi-tenant (Phase R) and A2A (Phase I).

---

## 2. Context and Problem

- **No agent identity:** there is no standard way to assert "this agent is X" nor to verify the identity of a remote agent (A2A uses **W3C DID** — prismal neither issues nor validates it).
- **Shared credentials:** agents use the process's global API keys; there are no per-agent/tenant credentials with minimal *scopes*.
- **No on-behalf delegation:** an agent acting for a user does not carry a scoped token of the user (OAuth on-behalf-of), which prevents fine-grained auditing and revocation.
- **`PermissionManager` is coarse:** TTL grants per capability, but not per agent identity nor per resource/action with a declarative policy.
- **Emerging runtime governance** ("policies on paths"): a policy engine is missing that decides, by identity+action+resource, whether to allow.

---

## 3. Target Users

- **Security/Compliance Lead:** verifiable per-agent identity, minimal scopes, revocation, auditing by identity.
- **Platform Host (`prismal-server`):** issue/rotate credentials per agent/tenant; integrate with the corporate IdP (OIDC/Entra/Okta).
- **A2A Integrator:** DID for the Agent Card and verification of remote DIDs.
- **Operator:** declarative policies (who can do what over what).

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Per-agent identity | Each agent/tenant has a verifiable `AgentIdentity` (DID) | 100% |
| Scoped credentials | Minimal scopes per agent; no shared global keys | 0 global keys in agents |
| On-behalf delegation | Scoped user token propagated and audited | Supported |
| Declarative policy | `allow(identity, action, resource)` engine evaluated pre-action | Integrated with `ActionInterceptor` |
| Auditing by identity | Every action attributable to an identity | 100% |
| Backward-compat | Without enabling, current behavior | 100% |

---

## 5. Scope (proposed)

### In Scope
- **`AgentIdentity`** (DID + metadata) and an `IdentityProvider` (issuance/rotation/verification), with a pluggable backend (local; OIDC/Entra/Okta as adapters).
- **Per-agent/tenant credentials** with scopes; pluggable secrets vault (not in clear in state/logs).
- **OAuth on-behalf-of**: propagate and scope the user's token along the delegation chain.
- **Policy engine** `PolicyEngine.allow(identity, action, resource, context)`; integration with `ActionInterceptor` (pre-tool/pre-action) and with A2A (in/out).
- **DID for A2A**: issue the Agent Card's DID and verify remote DIDs.
- **Auditing by identity** (extends `AuditLogger`).
- Settings `identity_*`; integration with Phase R (identity per `org_id`).

### Out of Scope
- A full in-house IdP (integrates with existing IdPs).
- In-house PKI/CA (the environment's is used).
- Real-time distributed revocation across tenants (later phase).

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-IDN-001 | `AgentIdentity` with a verifiable DID per agent/tenant | `MUST` |
| RF-IDN-002 | Pluggable `IdentityProvider` (local + OIDC/Entra/Okta) | `MUST` |
| RF-IDN-003 | Per-agent credentials with scopes; pluggable vault | `MUST` |
| RF-IDN-004 | OAuth on-behalf-of along the delegation chain | `SHOULD` |
| RF-IDN-005 | `PolicyEngine.allow(...)` integrated with `ActionInterceptor` | `MUST` |
| RF-IDN-006 | Issue/verify DID for Agent Cards (A2A) | `MUST` |
| RF-IDN-007 | Auditing by identity (extends `AuditLogger`) | `MUST` |
| RF-IDN-008 | Settings + Phase R integration (per `org_id`) | `SHOULD` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Secrets in logs/state | Vault + redaction; never in `AgentState`/logs |
| DID/PKI complexity | Pluggable backend; start with local DID + OIDC |
| Misconfigured policy blocks everything | `warn` mode before `enforce`; safe defaults |
| Coupling A2A and identity simultaneously | Define the minimal DID subset that A2A needs first |

---

## 8. Dependencies

- `prismal/security/permissions.py`, `action_interceptor.py`, `audit.py` (extension).
- `specs/a2a-interop/` (DID consumer).
- `specs/composition-root/` (per-tenant identity).
- External IdP (OIDC) — the host's responsibility.

---

## 9. Next Steps

Expand this PRD to the full SDD set (ARCHITECTURE/SPEC/TASKS) with: the `AgentIdentity`/`IdentityProvider`/`PolicyEngine` model, credential and scope format, exact integration with `ActionInterceptor` and A2A, and a phased plan.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | Seed PRD — agent identity and access governance |
