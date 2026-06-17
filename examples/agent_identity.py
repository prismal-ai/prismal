"""Agent identity & access governance demo with fakes (Phase IDN).

Drives the identity layer without any LLM, IdP, or network:

1. Issue a verifiable per-agent identity (a ``did:key``) with least-privilege
   scopes from a ``LocalIdentityProvider``.
2. Store and resolve a scoped secret through a ``FakeVault`` — only a
   ``SecretStr`` ever leaves the vault, and an out-of-scope resolve is rejected.
3. Authorize tool calls through an identity ``PolicyEngine``: in-scope is
   allowed, out-of-scope is denied, a destructive op routes to HITL.
4. Mint and propagate an OAuth on-behalf-of token (scopes only narrow).

Run::

    python examples/agent_identity.py
"""

from __future__ import annotations

from pydantic import SecretStr

from prismal.core.exceptions import ScopeError
from prismal.identity import (
    EnvVault,
    FakeVault,
    IdentityPolicy,
    LocalIdentityProvider,
    PolicyEffect,
    PolicyEngine,
    Scope,
    mint_on_behalf,
    propagate,
    validate,
)


def main() -> None:
    # 1. Issue a verifiable identity for the `coder` agent, scoped to file writes.
    provider = LocalIdentityProvider()
    coder = provider.issue(agent_name="coder", scopes=(Scope("tools:write_file"),))
    print(f"issued: {coder.did}")
    print(f"verify: {provider.verify(coder.did)}  scopes={[s.resource for s in coder.scopes]}")

    # 2. Store + resolve a scoped secret. The raw value never prints.
    vault: FakeVault | EnvVault = FakeVault()
    vault.store("openai", SecretStr("sk-secret"), scopes=(Scope("net:egress"),))
    cred = vault.resolve("openai", scopes=(Scope("net:egress"),))
    print(f"resolved credential ref={cred.ref!r} value={cred.value!r}")  # masked
    try:
        vault.resolve("openai", scopes=(Scope("tools:write_file"),))
    except ScopeError as exc:
        print(f"out-of-scope resolve rejected: {exc}")

    # 3. Authorize tool calls through the policy engine.
    engine = PolicyEngine(
        [
            IdentityPolicy(
                "coder", "tool_call", "tools:write_file", PolicyEffect.ALLOW, "tools:write_file"
            ),
            IdentityPolicy("coder", "tool_call", "tools:delete_file", PolicyEffect.REQUIRE_HITL),
            IdentityPolicy("*", "tool_call", "*", PolicyEffect.DENY),  # default-deny catch-all
        ]
    )
    for resource in ("tools:write_file", "tools:delete_file", "secrets:db"):
        decision = engine.allow(identity=coder, action="tool_call", resource=resource)
        print(f"policy {resource:<20} -> {decision.effect.value} ({decision.rule})")

    # 4. On-behalf-of delegation — scopes can only narrow along the chain.
    token = mint_on_behalf(
        subject="user@example.com",
        scopes=(Scope("rag:*"),),
        ttl_s=900,
        issuer=coder.did,
    )
    narrowed = propagate(token, via="did:key:zSubAgent", scopes=(Scope("rag:read"),))
    print(f"on-behalf chain: {narrowed.chain}")
    print(f"validate rag:read -> {validate(narrowed, action='tool_call', resource='rag:read')}")
    print(f"validate rag:write -> {validate(narrowed, action='tool_call', resource='rag:write')}")


if __name__ == "__main__":
    main()
