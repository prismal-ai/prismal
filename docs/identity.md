# Agent Identity & Access Governance (Phase IDN)

Per-agent identity, scoped credentials, on-behalf-of delegation, and an
identity-aware policy engine — so every agent (internal or A2A-exposed) has a
verifiable identity, least-privilege credentials, and auditable access policies.

**Opt-in.** Everything is gated by `settings.identity_enabled` (default
`False`). With the flag off the compiled supervisor graph is byte-for-byte
unchanged and no seam is observable. The layer is additive — it never replaces
the existing `PermissionManager` / `ActionInterceptor` / `AuditLogger`.

It follows the same hexagonal-port playbook as Phases Y/Z/W: the core depends on
`IdentityPort` / `CredentialVaultPort` / `PolicyPort` Protocols, and the host
composes concrete providers via `build_runtime`.

## Concepts

| Concept | Type | Notes |
|---|---|---|
| `AgentIdentity` | value object | `did`, `agent_name`, `org_id`, `scopes`, `credential_ref` — **never the secret** |
| DID | `did:key` / `did:web` | `did:key` is self-contained & offline (Ed25519); `did:web` resolves over HTTPS (A2A) |
| `Scope` | value object | least-privilege grant, e.g. `tools:write_file`, `rag:*` |
| `Credential` | value object | secret held in a `SecretStr`, resolved only at the action boundary |
| `OnBehalfToken` | value object | scoped, short-TTL user delegation; scopes only narrow along the chain |
| `PolicyEngine` | engine | `allow(identity, action, resource)` → `allow | deny | require_hitl` |

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `identity_enabled` | `False` | Master opt-in toggle |
| `identity_mode` | `"warn"` | `off` \| `warn` \| `enforce` |
| `identity_provider` | `"local"` | `local` (did:key) \| `oidc` (Entra/Okta) |
| `identity_did_method` | `"key"` | `key` (internal) \| `web` (A2A-exposed) |
| `identity_did_web_domain` | `""` | Domain for `did:web` issuance |
| `identity_vault` | `"env"` | `env` (via `ConfigSourcePort`) \| `file` (encrypted) |
| `identity_policy_path` | `config/identity_policies.yaml` | Declarative policy file |
| `identity_on_behalf_enabled` | `False` | Enable on-behalf-of delegation |
| `identity_on_behalf_ttl_s` | `900` | On-behalf token TTL (seconds) |
| `oidc_issuer` / `oidc_client_id` | `""` | OIDC adapter config (host-supplied) |

All use the `PRISMAL_` prefix (e.g. `PRISMAL_IDENTITY_ENABLED=true`). An unknown
`identity_mode`/`provider`/`did_method`/`vault`, or `oidc` without an issuer,
raises `IdentityConfigError` at load time.

## Quick start

```python
from prismal.identity import LocalIdentityProvider, Scope

provider = LocalIdentityProvider()
coder = provider.issue(agent_name="coder", scopes=(Scope("tools:write_file"),))
assert provider.verify(coder.did)        # verifiable did:key
```

### Policies

`PolicyEngine.allow(identity, action, resource, context)` is evaluated **before**
an action, through the existing `ActionInterceptor` seam:

1. If a tool policy is wired and `action == "tool_call"`, the `(agent, tool,
   args)` decision is **delegated** to the Phase H `ToolPolicyEngine` first (no
   rule duplication); a delegated `DENY` short-circuits, `REQUIRE_HITL` raises
   the floor to HITL.
2. The most-specific matching `IdentityPolicy` wins (identity/action/resource
   globs; the identity glob matches the DID **or** the agent_name).
3. `ALLOW` enforces `require_scope` against `identity.scopes` (least privilege);
   no match → `DENY`.

Policies are declared in `config/identity_policies.yaml` (a worked example ships
in the repo):

```yaml
default: deny            # least privilege
policies:
  - identity: "coder"
    action: "tool_call"
    resource: "tools:write_file"
    effect: allow
    require_scope: "tools:write_file"
  - identity: "coder"
    action: "tool_call"
    resource: "tools:delete_file"
    effect: require_hitl
```

`DENY` is audited with the identity DID and raised as `PolicyDenied`;
`REQUIRE_HITL` is routed through `subgraphs/gates.py::hitl_gate()`.

### Credentials

A `CredentialVault` resolves an opaque `credential_ref` to a `Credential` whose
secret lives in a `SecretStr` — it never reaches `AgentState`, logs, or audit.

```python
from pydantic import SecretStr
from prismal.identity import EnvVault, Scope

vault = EnvVault()                                   # reads via ConfigSourcePort
cred = vault.resolve("OPENAI_API_KEY")               # -> Credential(SecretStr)
```

* `EnvVault` resolves through the injected `ConfigSourcePort` (Phase W) — it
  never reads `os.environ` directly.
* `FileVault` is encrypted at rest (Fernet); the key sits in a sidecar file the
  host protects with filesystem permissions (created `0600`, lazily).
* `FakeVault` is a deterministic in-memory test double.

An out-of-scope `resolve(ref, scopes=...)` raises `ScopeError`.

### On-behalf-of delegation

```python
from prismal.identity import mint_on_behalf, propagate, validate, Scope

token = mint_on_behalf(subject="user@example.com", scopes=(Scope("rag:*"),),
                       ttl_s=900, issuer=coder.did)
hop = propagate(token, via="did:key:zSubAgent", scopes=(Scope("rag:read"),))  # narrow only
validate(hop, action="tool_call", resource="rag:read")   # True; rag:write -> False
```

Scopes may only **narrow** along `propagate` (widening raises `DelegationError`);
an expired or revoked token fails `validate`.

## Composition (host)

```python
from prismal.composition import build_runtime

ctx = await build_runtime(settings, org_id="acme")   # identity_enabled=True
ctx.identity_provider   # IdentityPort
ctx.credential_vault    # CredentialVaultPort
ctx.policy_engine       # PolicyPort (delegates to Phase H when hardening_enabled)
```

When `identity_enabled` is off these three are `None`.

## A2A (Phase I)

`did_document(identity)` produces the W3C DID Document embedded in the A2A Agent
Card served at `/.well-known/agent-card.json`. Inbound remote DIDs are verified
with `verify_did(...)` before the `PolicyEngine` authorizes a delegated action.

## Observability

OTel counters: `prismal.identity_issued_total{org}`,
`prismal.policy_decisions_total{effect}`,
`prismal.credential_resolved_total{vault}`, `prismal.did_verify_total{result}`.

## Rollout

1. Ship behind `identity_enabled=False` (no observable wiring).
2. Enable in `warn` mode in staging; tune scopes/policies from
   `policy_decisions_total`.
3. Flip to `enforce`; turn on on-behalf-of for delegated flows; issue `did:web`
   for A2A-exposed agents.

See `examples/agent_identity.py` for a runnable, LLM-free demo.