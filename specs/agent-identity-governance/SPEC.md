# Prismal Agent Identity & Access Governance — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Target package version** | `3.4.0` (SemVer minor) |
| **PLAN** | `specs/agent-identity-governance/PLAN.md` |
| **Architecture** | `specs/agent-identity-governance/ARCHITECTURE.md` |
| **TASKS** | `specs/agent-identity-governance/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async only where I/O is involved (OIDC token exchange, `did:web` resolution); pure policy evaluation is `sync` and O(1) and must **not raise** on the hot path (fail-open in `warn`, fail-closed in `enforce`).
- Frozen dataclasses / Pydantic models for value objects.
- Constructors accept `settings: Settings | None = None`.
- **Hexagonal ports**: the core depends on `IdentityPort`/`CredentialVaultPort`/`PolicyPort` Protocols; the host composes concrete providers via `build_runtime`.
- **Secrets never travel**: only `credential_ref` / token ids appear in `AgentState`/logs/audit; the secret is resolved at the action boundary and redacted.
- The vault resolves secrets through the injected `ConfigSourcePort` (no `os.environ` reads in the core); no provider SDK import outside `prismal/providers/`.
- All identity runtime state lives under `state["metadata"]["identity"]`; live providers/vaults live in the `RuntimeContext` / per-run registry, never in checkpointed state.
- Every gate honours `mode ∈ {off, warn, enforce}`; `identity_enabled=False` ⇒ zero wiring observable.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/identity/types.py` | `DID`, `AgentIdentity`, `Scope`, `Credential`, `OnBehalfToken`, `PolicyDecision` |
| `prismal/identity/provider.py` | `IdentityProvider` + `LocalIdentityProvider`, `OidcIdentityProvider`, `FakeIdentityProvider` |
| `prismal/identity/did.py` | DID issue/resolve/verify (`did:key`, `did:web`) |
| `prismal/identity/vault.py` | `CredentialVault` + `EnvVault`, `FileVault`, `FakeVault` |
| `prismal/identity/delegation.py` | `mint_on_behalf`, `propagate`, `revoke` |
| `prismal/identity/policy.py` | `PolicyEngine`, `load_identity_policies` |
| `prismal/agents/extension/ports.py` | `IdentityPort`, `CredentialVaultPort`, `PolicyPort` (extend) |
| `prismal/core/config.py` | `identity_*` settings |
| `prismal/core/exceptions.py` | `IdentityError` hierarchy |

---

## SPEC-IDN-TYP-001: Value objects (`identity/types.py`)

```python
DID = str   # e.g. "did:key:z6Mk…" or "did:web:org.example:agents:coder"


@dataclass(frozen=True)
class Scope:
    """A least-privilege grant, e.g. 'tools:write_file' or 'rag:read'."""
    resource: str            # "tools:write_file", "rag:*", "a2a:delegate"
    def matches(self, action: str, resource: str) -> bool: ...


@dataclass(frozen=True)
class AgentIdentity:
    did: DID
    agent_name: str                       # links to the runtime agent
    org_id: str | None = None             # tenant (Phase R)
    scopes: tuple[Scope, ...] = ()        # least-privilege ceiling
    credential_ref: str | None = None     # opaque handle; NEVER the secret
    expires_at: float | None = None       # epoch; None = no TTL


@dataclass(frozen=True)
class Credential:
    """Resolved at the action boundary only; never serialized into state."""
    ref: str
    value: SecretStr
    scopes: tuple[Scope, ...] = ()


@dataclass(frozen=True)
class OnBehalfToken:
    token_id: str
    subject: str               # the user the agent acts for
    scopes: tuple[Scope, ...]
    expires_at: float
    chain: tuple[DID, ...] = ()   # delegation path (audit)


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HITL = "require_hitl"


@dataclass(frozen=True)
class PolicyDecision:
    effect: PolicyEffect
    rule: str
    reason: str = ""
```

## SPEC-IDN-PRV-001: Identity provider (`identity/provider.py`)

```python
@runtime_checkable
class IdentityPort(Protocol):
    def issue(self, *, agent_name: str, org_id: str | None = None,
              scopes: Sequence[Scope] = ()) -> AgentIdentity: ...
    def resolve(self, did: DID) -> AgentIdentity | None: ...
    def verify(self, did: DID) -> bool: ...
    def revoke(self, did: DID) -> None: ...


class LocalIdentityProvider:
    """did:key issuance; in-process registry (SQLite-backed via PermissionManager).
    Zero-config default — keeps parity when identity is enabled without an IdP."""

class OidcIdentityProvider:
    """Adapter over a corporate IdP (Entra/Okta). Maps an OIDC subject to an
    AgentIdentity; async token exchange. SDK imports stay isolated here."""

class FakeIdentityProvider:
    """Deterministic, I/O-free test double."""
```

## SPEC-IDN-DID-001: DID operations (`identity/did.py`)

```python
def issue_did_key(public_key: bytes) -> DID: ...
def issue_did_web(domain: str, path: str) -> DID: ...           # did:web for A2A
async def resolve_did(did: DID) -> dict: ...                    # DID Document
async def verify_did(did: DID, *, signature: bytes | None = None) -> bool: ...
def did_document(identity: AgentIdentity) -> dict:
    """Produce the DID Document embedded in the A2A Agent Card (see
    agent-card-did.example.json)."""
```

**Acceptance:** a `did:key` round-trips issue→resolve→verify offline; a `did:web` resolves over HTTPS and a tampered signature fails `verify_did`.

## SPEC-IDN-VLT-001: Credential vault (`identity/vault.py`)

```python
@runtime_checkable
class CredentialVaultPort(Protocol):
    def resolve(self, credential_ref: str, *, scopes: Sequence[Scope] = ()) -> Credential: ...
    def store(self, ref: str, value: SecretStr, *, scopes: Sequence[Scope] = ()) -> None: ...


class EnvVault:
    """Resolves secrets via the injected ConfigSourcePort (Phase W) — never reads
    os.environ directly. Returns a Credential whose value is a SecretStr."""

class FileVault: ...     # encrypted file backend
class FakeVault: ...      # deterministic test double
```

- The resolved `Credential.value` is used **only** at the action boundary and is redacted from audit. The `credential_ref` is what travels in identity metadata.

**Acceptance:** `resolve()` returns a `SecretStr`; the secret never appears in `AgentState`, logs, or audit (spy-verified); out-of-scope resolution raises `ScopeError`.

## SPEC-IDN-DEL-001: On-behalf-of delegation (`identity/delegation.py`)

```python
def mint_on_behalf(*, subject: str, scopes: Sequence[Scope], ttl_s: int,
                   issuer: DID) -> OnBehalfToken: ...
def propagate(token: OnBehalfToken, *, via: DID) -> OnBehalfToken:
    """Append `via` to the delegation chain; scopes may only narrow, never widen."""
def revoke(token_id: str) -> None: ...
def validate(token: OnBehalfToken, *, action: str, resource: str) -> bool: ...
```

**Acceptance:** scopes can only narrow along `propagate`; an expired or revoked token fails `validate`; the chain is audited.

## SPEC-IDN-POL-001: Policy engine (`identity/policy.py`)

```python
@dataclass(frozen=True)
class IdentityPolicy:
    identity: str = "*"      # glob over DID or agent_name
    action: str = "*"        # "tool_call" | "file_write" | "a2a_delegate" | "*"
    resource: str = "*"      # glob: "tools:write_file", "rag:*", "https://api.example/*"
    effect: PolicyEffect = PolicyEffect.ALLOW
    require_scope: str | None = None    # the scope the identity must hold


@runtime_checkable
class PolicyPort(Protocol):
    def allow(self, *, identity: AgentIdentity, action: str, resource: str,
              context: Mapping[str, Any] | None = None) -> PolicyDecision: ...


class PolicyEngine:
    def __init__(self, policies: list[IdentityPolicy], *,
                 tool_policy: "ToolPolicyEngine | None" = None,   # Phase H delegate
                 settings: Settings | None = None) -> None: ...

    def allow(self, *, identity, action, resource, context=None) -> PolicyDecision:
        """1) If a ToolPolicyEngine is wired and action is a tool call, delegate the
        (agent, tool, args) decision first. 2) Enforce least-privilege scopes
        (identity.scopes must cover `resource`). 3) Apply identity policies
        (most-specific-wins). REQUIRE_HITL is surfaced to ActionInterceptor →
        hitl_gate(). Identity-agnostic when identity_enabled is off."""


def load_identity_policies(path: str | None = None) -> list[IdentityPolicy]:
    """Load + validate config/identity_policies.yaml (see identity_policies.example.yaml)."""
```

- Integration: `ActionInterceptor.check()` consults `PolicyEngine` (via the `_tool_call_checker` seam) when `identity_enabled`. `DENY` → blocked + audited with DID; `REQUIRE_HITL` → `hitl_gate()`.

**Acceptance:** an action whose `resource` is outside `identity.scopes` is denied; `identity=did:* action=file_write resource=/etc/* effect=deny` blocks; a tool-level rule still resolves via the delegated `ToolPolicyEngine`.

## SPEC-IDN-INT-001: ActionInterceptor / Permissions / Audit integration

- `security/action_interceptor.py`: when `identity_enabled`, `check()` calls `PolicyEngine.allow(identity=current_identity, action, resource, context)` before the existing checks; `REQUIRE_HITL` routes to `hitl_gate()`.
- `security/permissions.py`: `PermissionManager` grants gain an optional `identity` (DID) key and honour the identity TTL.
- `security/audit.py`: every record gains an `identity` field (DID); secrets/tokens are redacted (only refs/ids logged).

## SPEC-IDN-CMP-001: Composition (`composition/runtime.py` extension)

```python
# build_runtime gains identity composition (opt-in):
def build_runtime(settings=None, *, org_id=None, ...) -> RuntimeContext:
    """When settings.identity_enabled, compose IdentityProvider + CredentialVault +
    PolicyEngine per org_id and expose them on the RuntimeContext (identity_provider,
    credential_vault, policy_engine). aclose() releases them. Mirrors how tool/vector
    providers are composed."""
```

## SPEC-IDN-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `identity_enabled` | `bool` | `False` | Master opt-in toggle |
| `identity_mode` | `str` | `"warn"` | Policy default `off`\|`warn`\|`enforce` |
| `identity_provider` | `str` | `"local"` | `local` (did:key) \| `oidc` (Entra/Okta) |
| `identity_did_method` | `str` | `"key"` | `key` (internal) \| `web` (A2A-exposed) |
| `identity_did_web_domain` | `str` | `""` | Domain for `did:web` issuance |
| `identity_vault` | `str` | `"env"` | `env` (ConfigSourcePort) \| `file` |
| `identity_policy_path` | `str` | `"config/identity_policies.yaml"` | Policy file |
| `identity_on_behalf_enabled` | `bool` | `False` | Enable OAuth on-behalf-of delegation |
| `identity_on_behalf_ttl_s` | `int` | `900` | On-behalf token TTL (seconds) |
| `oidc_issuer` / `oidc_client_id` | `str` | `""` | OIDC adapter config (host-supplied) |

Env prefix `PRISMAL_` (e.g. `PRISMAL_IDENTITY_ENABLED`, `PRISMAL_IDENTITY_PROVIDER`). `_validate_identity` rejects an unknown `identity_mode`/`identity_provider`/`identity_did_method` at load time, and an `oidc` provider without `oidc_issuer`.

## SPEC-IDN-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class IdentityError(PrismalError): ...
class DidVerificationError(IdentityError): ...
class ScopeError(IdentityError): ...            # action outside identity.scopes
class PolicyDenied(IdentityError): ...          # enforce-mode deny (caught at the seam)
class CredentialResolutionError(IdentityError): ...
class IdentityConfigError(IdentityError): ...
class DelegationError(IdentityError): ...        # scope widening / expired token
```

## SPEC-IDN-OTEL-001: Counters (`monitoring/otel.py` extension)

`prismal.identity_issued_total{org}`, `prismal.policy_decisions_total{effect}`, `prismal.credential_resolved_total{vault}`, `prismal.did_verify_total{result}`.

## Acceptance Criteria (per requirement)

| Requirement (PLAN) | Acceptance criterion |
|---|---|
| RF-IDN-001 | Each agent/tenant gets an `AgentIdentity` with a verifiable DID; `verify()` true |
| RF-IDN-002 | `LocalIdentityProvider` works offline; `OidcIdentityProvider` maps an OIDC subject (fake adapter test) |
| RF-IDN-003 | Per-agent credentials carry scopes; secret never in state/logs (spy); out-of-scope → `ScopeError` |
| RF-IDN-004 | On-behalf token narrows scopes along the chain; expired/revoked → `validate` false |
| RF-IDN-005 | `PolicyEngine.allow` integrated with `ActionInterceptor`; deny/HITL honoured; delegates to `ToolPolicyEngine` |
| RF-IDN-006 | `did_document()` produces the A2A Agent Card DID; remote DID verified before delegation |
| RF-IDN-007 | Every audit record carries the `identity` DID; secrets redacted |
| RF-IDN-008 | Identity composed per `org_id`; `identity_enabled=False` ⇒ graph snapshot unchanged |
