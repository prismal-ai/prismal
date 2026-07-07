# Addendum — Phase ID8: `PermissionManager` grants keyed by identity DID

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (v3.10.0, test-first 2026-07-07 — see §7) |
| **Version** | 1.1 |
| **Date** | 2026-07-04 (impl. 2026-07-07) |
| **Parent spec** | `specs/agent-identity-governance/` (`SPEC.md`, `ARCHITECTURE.md`, `PLAN.md`, `TASKS.md` — Phase IDN, shipped v3.4.0) |
| **Origin** | `README.md` Roadmap item 6, parenthetical: *"(ID6-02 PermissionManager-DID deferred.)"*; `docs/gap-analysis-loops-harness-guardrails-2026-07.md`, item #9 |
| **Target package version** | `3.10.0` (opt-in, additive minor — the next open minor at implementation time) |

---

## 1. Why an addendum instead of reopening the parent spec

`specs/agent-identity-governance/` is `Status: IMPLEMENTED` (shipped v3.4.0, 100% coverage on `prismal/identity`) and its own `TASKS.md` already contains the exact task this addendum re-opens — **`ID6-02`: `security/permissions.py`: grants keyed by identity DID + TTL** — which was explicitly deferred during the original rollout (it is the one item the `README.md` roadmap flags as unfinished inside an otherwise-complete phase). Rather than editing the historical, already-shipped SPEC/ARCHITECTURE/TASKS in place (which would misrepresent what actually shipped in v3.4.0), this addendum captures the follow-up as its own small, reviewable unit that references `ID6-02` by its original ID.

## 2. Problem (verified against current code)

`prismal/security/permissions.py::PermissionManager.grant()` today accepts `(permission_type, resource, ttl_seconds)` only — **no identity dimension at all**. Every other Phase IDN control (`PolicyEngine.allow()`, `OnBehalfToken`, audit records) already threads the caller's `AgentIdentity`/DID through, but a TTL grant issued by `PermissionManager` cannot be scoped, listed, or revoked *per identity* — two different agent identities sharing the same `(permission_type, resource)` pair share the same grant, and there is no way to answer "what is `did:key:z6Mk...` currently allowed to do" without cross-referencing audit logs.

## 3. Scope

### In scope
- Extend `PermissionManager.grant()`/`check()`/`revoke()` with an optional `identity: DID | str | None = None` keying dimension, backward-compatible when omitted (today's behavior — global grants — must be unchanged for existing callers).
- A `list_grants(identity: DID | None = None)` query to support both an identity-scoped and a global view.
- `security/audit.py` already gained `identity` on every record in `ID6-03` (shipped) — this addendum makes `PermissionManager` consistent with that, closing the one remaining asymmetry.
- Update `SPEC-IDN-*` cross-references: this addendum's interface belongs conceptually under `SPEC-IDN-POL-001`'s neighborhood in the parent `SPEC.md`; do not duplicate the whole spec, just add a `SPEC-IDN-PERM-001` section there (or a clearly marked appendix) when implementing.

### Out of scope
- No change to `PolicyEngine.allow()` semantics (already identity-aware) — this addendum only closes the `PermissionManager` gap.
- No change to grant storage backend (stays SQLite via `prismal.core.database`).
- No new settings flag — reuses `identity_enabled`; when `False`, `PermissionManager` behaves exactly as it does today (identity parameter simply unused).

## 4. Interface sketch

```python
# prismal/security/permissions.py (extended)

async def grant(
    self,
    permission_type: PermissionType,
    resource: str,
    *,
    identity: str | None = None,   # DID string; None = global grant (today's behavior)
    ttl_seconds: int | None = 3600,
) -> None: ...

async def check(
    self,
    permission_type: PermissionType,
    resource: str,
    *,
    identity: str | None = None,   # must match the identity the grant was issued to,
                                    # unless the grant was global (identity=None)
) -> bool: ...

async def list_grants(self, *, identity: str | None = None) -> list[Grant]: ...

async def revoke(self, permission_type: PermissionType, resource: str, *, identity: str | None = None) -> None: ...
```

## 5. Tasks

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| ID8-01 | `security/permissions.py`: add `identity` column/param to grant storage + `grant`/`check`/`revoke`, backward-compatible default `None` | 0.4 d | — | `DONE` |
| ID8-02 | `security/permissions.py`: `list_grants(identity=...)` query | 0.2 d | ID8-01 | `DONE` |
| ID8-03 | Wire `PermissionManager` calls in `action_interceptor.py` to pass the resolved `AgentIdentity.did` when set (`tool_policy.py` does not call `PermissionManager`) | 0.3 d | ID8-01 | `DONE` |
| ID8-04 | Unit: identity-scoped grant does not leak to a different identity; global grant (`identity=None`) keeps legacy behavior | 0.4 d | ID8-01 | `DONE` |
| ID8-05 | Regression: identity-less interceptor call is byte-for-byte the legacy `check(perm, "*")` (no `identity` kwarg) | 0.2 d | ID8-03 | `DONE` |
| ID8-06 | Update `specs/agent-identity-governance/TASKS.md` row `ID6-02` to `DONE`; update `docs/identity.md`; CHANGELOG + version bump | 0.2 d | ID8-04, ID8-05 | `DONE` |

**Implementation notes (v3.10.0):** `identity` is a **keyword-only** param appended to `grant`/`check`/`revoke` (positional `ttl_seconds`/`reason` untouched), so all ~existing call sites are source-compatible. `list_grants(identity=None)` returns the global/admin view (all active grants) and `list_permissions()` became a thin alias for it. Semantics: a global grant (column `NULL`) satisfies any check; a DID-scoped grant satisfies only a matching-DID check; `check(identity=None)` matches global grants only. `revoke` is symmetric with `grant` (deletes exactly the targeted scope). `ActionInterceptor` gained a keyword-only `identity` ctor param; it forwards the DID to `check` only when set, keeping the identity-less call unchanged (ID8-05). No Alembic migration — the column is additive & nullable.

**Done when:** an identity-scoped grant is only usable by the identity it was issued to; a global (identity-less) grant behaves exactly as `PermissionManager` does today; `identity_enabled=False` changes nothing; `ID6-02` in the parent `TASKS.md` is updated to `DONE` with a pointer to this addendum.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Breaking existing global-grant callers | `identity` is optional and defaults to `None`; existing call sites need no changes |
| Grant-store schema migration | Additive column with a default (`NULL` = global); no destructive migration |
| Divergence between `PermissionManager` grants and `PolicyEngine` decisions | Document that `PolicyEngine.allow()` remains the authoritative identity-aware decision; `PermissionManager` grants are a narrower, TTL-based cache/allowlist beneath it |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial addendum re-opening deferred `ID6-02`, from gap-analysis item #9 |
