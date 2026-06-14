# Prismal Agent Identity & Access Governance — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Phase** | IDN (Identity) |
| **Target package version** | `3.4.0` (SemVer minor) |
| **PLAN** | `specs/agent-identity-governance/PLAN.md` |
| **SPEC** | `specs/agent-identity-governance/SPEC.md` |
| **TASKS** | `specs/agent-identity-governance/TASKS.md` |

---

## 1. Context

Prismal authorizes **actions** (`ActionInterceptor`), grants **capabilities** with a TTL (`PermissionManager`), and audits everything (`AuditLogger`) — but it has **no notion of *who* an agent is**. Agents borrow the process's global API keys; there is no verifiable per-agent identity, no scoped per-agent credential, no on-behalf-of delegation, and no declarative `allow(identity, action, resource)` policy. This is the most-cited governance gap for agentic systems in 2026 and a hard blocker for multi-tenant (`composition-root`, Phase R) and A2A interop (`a2a-interop`, Phase I).

This phase adds an **identity & access governance** layer (`prismal/identity/`): each agent/tenant gets a verifiable `AgentIdentity` (W3C **DID**), scoped credentials from a pluggable vault, an optional OAuth-on-behalf token threaded along the delegation chain, and an identity-aware `PolicyEngine` evaluated **pre-action** through the existing `ActionInterceptor` seam. It is **additive and opt-in** (`identity_enabled`, default `False`): with the flag off, behavior is byte-for-byte unchanged.

> **Supersession.** The identity-aware `PolicyEngine.allow(identity, action, resource, context)` is the richer successor of the identity-agnostic `ToolPolicyEngine` from [`specs/runtime-hardening/`](../runtime-hardening/) (Phase H). When identity is enabled, the `ActionInterceptor` consults `PolicyEngine`, which **delegates to** `ToolPolicyEngine` for the identity-agnostic `(agent, tool, args)` rules and adds identity/resource scoping on top — no rule duplication.

## 2. Feasibility with the existing core (confirmed)

- `ActionInterceptor.check()` (+ its `_tool_call_checker` seam) is already the single pre-action chokepoint → the `PolicyEngine` plugs in there, exactly where `ToolPolicyEngine` does.
- `PermissionManager` already persists TTL grants in SQLite → extended to be keyed by `identity` (DID) in addition to capability.
- `AuditLogger` already hash-chains records → gains an `identity` field (DID), never the secret.
- `ConfigSourcePort` (Phase W) already injects secrets without the core reading `os.environ` → the `CredentialVault` is a natural sibling port; per-tenant identity uses `composition-root`'s `org_id` resolution.
- `a2a-interop` (Phase I) already needs a DID for the Agent Card → this phase issues/verifies it.

No new LangGraph capability is required; identity is metadata + a pre-action gate.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose |
|---|---|
| `prismal/identity/types.py` | `AgentIdentity`, `DID`, `Credential`, `Scope`, `OnBehalfToken`, `PolicyDecision` |
| `prismal/identity/provider.py` | `IdentityProvider` Protocol + `LocalIdentityProvider`, `OidcIdentityProvider` (Entra/Okta adapter) |
| `prismal/identity/did.py` | DID issue/resolve/verify (`did:key` local; `did:web` for A2A) |
| `prismal/identity/vault.py` | `CredentialVault` Protocol + `EnvVault` (via ConfigSourcePort), `FileVault`, `FakeVault` |
| `prismal/identity/delegation.py` | OAuth on-behalf-of: mint/scope/propagate `OnBehalfToken` along the chain |
| `prismal/identity/policy.py` | `PolicyEngine.allow(identity, action, resource, context)`; YAML loader; delegates to `ToolPolicyEngine` |
| `prismal/security/action_interceptor.py` | *(extend)* consult `PolicyEngine` when identity is enabled |
| `prismal/security/permissions.py` | *(extend)* grants keyed by identity DID |
| `prismal/security/audit.py` | *(extend)* `identity` (DID) on every record |
| `prismal/agents/extension/ports.py` | `IdentityPort`, `CredentialVaultPort`, `PolicyPort` Protocols |
| `prismal/composition/runtime.py` | *(extend)* compose identity provider + vault per `org_id` |
| `prismal/core/config.py` | `identity_*` settings |
| `prismal/core/exceptions.py` | `IdentityError` hierarchy |
| `config/identity_policies.yaml` | declarative policy file (example shipped in this spec dir) |

`prismal/identity/` follows the **hexagonal port** pattern of Y/Z/W: the core depends on `IdentityPort`/`CredentialVaultPort`/`PolicyPort` Protocols; the host composes concrete providers via `build_runtime`.

### 3.2 Identity model

```
AgentIdentity
 ├─ did            did:key:z6Mk… (local) | did:web:org.example:agents:coder (A2A)
 ├─ agent_name     "coder"            (links to the runtime agent)
 ├─ org_id         tenant scope (Phase R)            
 ├─ scopes         ["tools:write_file", "rag:read", …]   (least privilege)
 └─ credential_ref opaque handle resolved by the vault (never the secret itself)
```

Credentials and on-behalf tokens are **never** placed in `AgentState` or logs — only an opaque `credential_ref` / token id travels; the secret is resolved at the call boundary from the vault and redacted from audit.

### 3.3 Data flow (with `identity_enabled=True`)

```
build_runtime(org_id) ──► IdentityProvider.issue(agent, org_id) ──► AgentIdentity (DID + scopes)
                                                  │
user request ──► supervisor ──► agent node (carries identity ref in metadata.identity)
                                      │
            tool/action ──► ActionInterceptor.check()
                                      │  └─► PolicyEngine.allow(identity, action, resource, ctx)
                                      │         ├─ delegates to ToolPolicyEngine (agent,tool,args)   [Phase H]
                                      │         ├─ checks scopes (least privilege)
                                      │         └─ allow │ deny │ require_hitl
                                      │  └─► CredentialVault.resolve(credential_ref)  (scoped secret, at boundary)
                                      ▼
                              action executes  ──►  AuditLogger.log(identity=DID, …)
```

For **A2A** (Phase I): outbound requests carry the agent's DID in the Agent Card; inbound requests' remote DIDs are verified via `did.verify()` before the `PolicyEngine` authorizes the delegated action.

All identity runtime state lives under `state["metadata"]["identity"]` (DID + refs only — serializable, never secrets). Live providers/vaults live in the per-run registry / `RuntimeContext`, never in checkpointed state (same rule as Budget/Hardening).

## 4. Design Decisions

### DD-IDN-001: Hexagonal ports, host composes
`IdentityProvider`, `CredentialVault`, `PolicyEngine` are Protocols. The core never constructs an IdP or a vault; `build_runtime(org_id=...)` injects them. Mirrors Y (tools), Z (vector), W (config). Default `LocalIdentityProvider` + `EnvVault` keep zero-config parity.

### DD-IDN-002: DID local-first, `did:web` for interop
Start with `did:key` (self-contained, no network) for internal agents; issue `did:web` for agents exposed via A2A so external parties resolve them over HTTPS. No in-house PKI/CA — the environment's is used.

### DD-IDN-003: Secrets never in state or logs
Only `credential_ref` / token ids travel in `AgentState`/audit. The actual secret is resolved at the action boundary from the vault and redacted (reuses the redaction discipline of `pii_sanitizer`). Honors the Phase W rule (no `os.environ` reads in the core; vault uses the injected `ConfigSourcePort`).

### DD-IDN-004: PolicyEngine supersedes, does not duplicate, ToolPolicyEngine
`PolicyEngine.allow(identity, action, resource, ctx)` first delegates to the Phase H `ToolPolicyEngine` for `(agent, tool, args)` rules, then layers identity scopes + resource matching. If Phase H is not shipped, `PolicyEngine` runs standalone. One policy file format extends the other (adds `identity`/`resource`/`scope` keys).

### DD-IDN-005: `warn` before `enforce`
`PolicyEngine` honors `mode ∈ {off, warn, enforce}` (same convention as Phase H). `warn` audits denials without blocking → safe rollout; `enforce` blocks. Default `warn` when `identity_enabled=True`.

### DD-IDN-006: On-behalf-of is opt-in and scoped
When a user delegates to an agent, an `OnBehalfToken` (scoped, short-TTL) is minted and **propagated** along the delegation chain (supervisor → agent → sub-agent → A2A peer), enabling fine-grained audit and revocation. Off by default.

### DD-IDN-007: Per-tenant identity via `org_id`
Identity issuance and policy resolution key on `org_id` (Phase R). `collection_for(base, org_id)` already isolates data; identities are isolated the same way, so parallel tenants never share credentials.

### DD-IDN-008: Opt-in, snapshot-guaranteed
Every wiring point gates on `identity_enabled`. A snapshot test asserts the compiled graph is byte-for-byte identical when off (mirrors Skynet/Kokoro/Budget/Hardening).

## 5. Security & cost
- Least privilege: an agent's `scopes` are the ceiling; `PolicyEngine` denies anything outside them even if a tool is reachable.
- Revocation: identities/tokens carry a TTL via the extended `PermissionManager`; a revoked DID fails `verify()`.
- All denials/grants hash-first audited with the `identity` DID (never the secret).
- No LLM calls; negligible runtime cost (policy eval is O(1) per action). DID resolution for A2A is cached.

## 6. Observability

### 6.1 OTel counters (registered in `OTelManager`)
- `prismal.identity_issued_total{org}`
- `prismal.policy_decisions_total{effect}` (`allow`|`deny`|`require_hitl`)
- `prismal.credential_resolved_total{vault}`
- `prismal.did_verify_total{result}` (`ok`|`fail`)

### 6.2 Spans
- `prismal.identity.issue`, `prismal.identity.policy_eval`, `prismal.identity.did_verify`.

## 7. Relationship to existing specs
- **`runtime-hardening/` (H)** — `PolicyEngine` delegates to its `ToolPolicyEngine`; this phase adds the identity/resource layer.
- **`composition-root/` (R)** — composes the identity provider + vault per `org_id`.
- **`config-source-injection/` (W)** — the vault resolves secrets through the injected `ConfigSourcePort`.
- **`a2a-interop/` (I)** — consumes the DID (Agent Card issue + remote DID verify); this phase is its foundation.
- **`tool-provider-injection/` (Y)** — scopes constrain which injected tools an identity may actually use.

## 8. Testing strategy (summary; detail in `TASKS.md`)
- Unit: DID issue/resolve/verify (`did:key`, `did:web`); vault resolve + redaction; policy allow/deny/HITL + scope checks; on-behalf token mint/propagate/revoke.
- Integration: `identity_enabled=False` graph snapshot unchanged; end-to-end where an out-of-scope action is denied; a high-risk action routes to HITL; secret never appears in state/audit (spy assertion).
- Guards: vault uses `ConfigSourcePort` (no `os.environ` in core — reuse AST guard); no provider import outside `providers/`.

## 9. Rollout
1. Ship `prismal/identity/` behind `identity_enabled=False` (no wiring observable).
2. Enable in `warn` mode in staging; tune scopes/policies from `policy_decisions_total`.
3. Flip to `enforce`; turn on on-behalf-of for delegated flows; issue `did:web` for A2A-exposed agents.
