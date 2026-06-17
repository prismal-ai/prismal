# Prismal Agent Identity & Access Governance — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Target package version** | `3.4.0` (SemVer minor) |
| **PLAN** | `specs/agent-identity-governance/PLAN.md` |
| **SPEC** | `specs/agent-identity-governance/SPEC.md` |
| **Architecture** | `specs/agent-identity-governance/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Identity & Access Governance lands in seven phases (ID1–ID7) as a **new hexagonal package** `prismal/identity/` (ports composed by `build_runtime`), gated behind `settings.identity_enabled` (default `False`) so `main` stays green and the 26 agents are unaffected until the final wiring phase. It reuses existing seams (`ActionInterceptor`, `PermissionManager`, `AuditLogger`, `ConfigSourcePort` vault, `composition-root` `org_id`) and **delegates** tool-level decisions to the Phase H `ToolPolicyEngine` (no rule duplication). Every gate honours `mode ∈ {off, warn, enforce}`.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. Phases ID1–ID7 are `DONE`
(implemented test-first in v3.4.0, 100% coverage on `prismal/identity`). The
single exception is **ID6-02** (PermissionManager grants keyed by DID), which is
`DEFERRED`: it needs an Alembic migration for the existing `permissions` table
and the `PolicyEngine` + scopes already provide identity-aware authorization.

## 2. Prerequisites

- Reuse, do not fork: `security/action_interceptor.py` (`_tool_call_checker` seam), `security/permissions.py`, `security/audit.py`, `agents/subgraphs/gates.py::hitl_gate`, `core/config_source.py` (vault), `composition/runtime.py` (`org_id`), `monitoring/otel.py`.
- **Recommended (not required):** `specs/runtime-hardening/` (Phase H) shipped, so `PolicyEngine` can delegate to `ToolPolicyEngine`. Without it, `PolicyEngine` runs standalone.
- Confirm `AgentState.metadata.identity` can carry a DID + refs (serializable; no secrets).
- Crypto: reuse the existing `cryptography` dependency for `did:key` signing.

## 3. Implementation Phases

### PHASE ID1 — Types + settings + exceptions + ports

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID1-01 | `identity/types.py`: `DID`, `AgentIdentity`, `Scope`, `Credential`, `OnBehalfToken`, `PolicyDecision` (SPEC-IDN-TYP-001) | 0.5 d | — | TODO |
| ID1-02 | `core/config.py`: `identity_*` settings + `_validate_identity` (SPEC-IDN-CFG-001) | 0.3 d | — | TODO |
| ID1-03 | `core/exceptions.py`: `IdentityError` hierarchy (SPEC-IDN-ERR-001) | 0.2 d | — | TODO |
| ID1-04 | `agents/extension/ports.py`: `IdentityPort`, `CredentialVaultPort`, `PolicyPort` Protocols | 0.3 d | ID1-01 | TODO |

**Done when:** value objects round-trip; settings parse from `PRISMAL_*`; bad `identity_mode`/`provider` → `IdentityConfigError`; ports importable.

### PHASE ID2 — DID + identity provider

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID2-01 | `identity/did.py`: `issue_did_key` / `issue_did_web` / `resolve_did` / `verify_did` / `did_document` | 0.8 d | ID1 | TODO |
| ID2-02 | `identity/provider.py`: `LocalIdentityProvider` (did:key, PermissionManager-backed) | 0.6 d | ID2-01 | TODO |
| ID2-03 | `identity/provider.py`: `OidcIdentityProvider` adapter (Entra/Okta; SDK isolated) | 0.6 d | ID2-02 | TODO |
| ID2-04 | `FakeIdentityProvider` (deterministic test double) | 0.2 d | ID2-02 | TODO |

**Done when:** `did:key` round-trips offline; tampered signature fails `verify`; OIDC adapter maps a subject (fake) to an `AgentIdentity`.

### PHASE ID3 — Credential vault

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID3-01 | `identity/vault.py`: `EnvVault` via `ConfigSourcePort` (no `os.environ`) | 0.5 d | ID1 | TODO |
| ID3-02 | `FileVault` (encrypted) + `FakeVault` | 0.4 d | ID3-01 | TODO |
| ID3-03 | Boundary resolution + redaction (secret never in state/audit) | 0.4 d | ID3-01 | TODO |

**Done when:** `resolve()` returns a `SecretStr`; spy proves the secret never reaches state/logs/audit; out-of-scope → `ScopeError`.

### PHASE ID4 — Policy engine (delegates to Phase H)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID4-01 | `identity/policy.py`: `IdentityPolicy`, `PolicyEngine.allow` (scopes + identity rules) | 0.7 d | ID1 | TODO |
| ID4-02 | Delegate `(agent, tool, args)` to Phase H `ToolPolicyEngine` when present | 0.4 d | ID4-01 | TODO |
| ID4-03 | `load_identity_policies` + `config/identity_policies.yaml` (ship example) | 0.3 d | ID4-01 | TODO |

**Done when:** out-of-scope action denied; identity rules resolve most-specific-wins; tool rules still flow through the delegated engine; `warn` vs `enforce` honoured.

### PHASE ID5 — Delegation (on-behalf-of)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID5-01 | `identity/delegation.py`: `mint_on_behalf` / `propagate` (narrow-only) / `revoke` / `validate` | 0.6 d | ID1 | TODO |
| ID5-02 | Thread the `OnBehalfToken` through `state["metadata"]["identity"]` along the chain | 0.4 d | ID5-01 | TODO |

**Done when:** scopes only narrow along `propagate`; expired/revoked token fails `validate`; chain audited.

### PHASE ID6 — Integration + composition (the only behavior-changing phase)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID6-01 | `security/action_interceptor.py`: consult `PolicyEngine` when `identity_enabled`; HITL on `REQUIRE_HITL` | 0.5 d | ID4 | TODO |
| ID6-02 | `security/permissions.py`: grants keyed by identity DID + TTL | 0.4 d | ID2 | TODO |
| ID6-03 | `security/audit.py`: add `identity` (DID) to every record; redact secrets | 0.3 d | ID2 | TODO |
| ID6-04 | `composition/runtime.py`: compose provider + vault + policy per `org_id`; `aclose()` release | 0.6 d | ID2,ID3,ID4 | TODO |
| ID6-05 | `monitoring/otel.py`: register identity counters (SPEC-IDN-OTEL-001) | 0.2 d | ID4 | TODO |

**Done when:** with `identity_enabled=False` the compiled-graph snapshot is unchanged; with `True`+`enforce` an out-of-scope action is denied and a high-risk action routes to HITL end-to-end.

### PHASE ID7 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID7-01 | Unit: DID issue/resolve/verify (key + web) | 0.5 d | ID2 | TODO |
| ID7-02 | Unit: vault resolve + redaction (secret never leaks) | 0.4 d | ID3 | TODO |
| ID7-03 | Unit: policy allow/deny/HITL + scope + Phase-H delegation | 0.6 d | ID4 | TODO |
| ID7-04 | Unit: delegation (narrow-only, expiry, revoke) | 0.4 d | ID5 | TODO |
| ID7-05 | Integration: `identity_enabled=False` graph snapshot unchanged | 0.3 d | ID6 | TODO |
| ID7-06 | Integration: out-of-scope deny + HITL-on-high-risk end-to-end (fakes) | 0.5 d | ID6 | TODO |
| ID7-07 | Guards: vault uses `ConfigSourcePort` (AST guard); no provider import outside `providers/` | 0.3 d | ID6 | TODO |
| ID7-08 | `docs/identity.md` + `examples/agent_identity.py` | 0.5 d | ID6 | TODO |
| ID7-09 | `README.md` + `CHANGELOG.md`; mark PLAN/SPEC/ARCHITECTURE `IMPLEMENTED` | 0.2 d | ID6 | TODO |

**Done when:** `uv run pytest -m unit` green; `ruff` + `mypy --strict` + `bandit` clean; coverage ≥ project target on `prismal/identity/`.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Secrets in logs/state | Vault + redaction; only refs/ids in state; spy test (ID7-02) |
| DID/PKI complexity | Start `did:key` (offline); `did:web` only for A2A; no in-house CA |
| Misconfigured policy blocks everything | `warn` before `enforce`; safe defaults; per-identity override |
| Coupling A2A + identity simultaneously | Ship the minimal DID subset A2A needs first (`did_document`) |
| Duplicating Phase H tool policy | `PolicyEngine` delegates to `ToolPolicyEngine`; one policy format extends the other |
| Behavior leak when disabled | Gate every wiring point; snapshot test (ID7-05) |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-IDN-001…008) implemented and tested.
- [ ] Each agent/tenant carries a verifiable DID; actions authorized by `PolicyEngine` pre-execution.
- [ ] Per-agent scoped credentials; secret never in state/logs/audit (proven).
- [ ] On-behalf-of delegation narrows scopes and is revocable.
- [ ] `did_document()` ready for the A2A Agent Card; remote DIDs verified.
- [ ] With `identity_enabled=False`, zero behavior change (snapshot proven).
- [ ] Vault uses `ConfigSourcePort` (no `os.environ` in core); no provider import in the wrong layer.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| ID1 | Types + settings + exceptions + ports | ~1.3 d |
| ID2 | DID + identity provider | ~2.2 d |
| ID3 | Credential vault | ~1.3 d |
| ID4 | Policy engine (+ Phase H delegation) | ~1.4 d |
| ID5 | Delegation (on-behalf-of) | ~1.0 d |
| ID6 | Integration + composition | ~2.0 d |
| ID7 | Tests + docs + packaging | ~3.7 d |
| **Total** | | **~12.9 d** |
