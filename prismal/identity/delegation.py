"""OAuth on-behalf-of delegation (SPEC-IDN-DEL-001).

When a user delegates to an agent, a scoped, short-TTL :class:`OnBehalfToken` is
minted and propagated along the delegation chain (supervisor → agent → sub-agent
→ A2A peer), enabling fine-grained audit and revocation. Scopes may only
**narrow** along the chain, never widen.

The token carries no signed secret material — only an opaque ``token_id``,
subject, scopes, expiry, and the delegation chain — so it is safe to thread
through ``state["metadata"]["identity"]`` and survive checkpointing.

Revocation is an in-process registry (revoked token ids), mirroring the budget /
hardening per-run registries — never persisted into checkpointed state.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import DelegationError
from prismal.core.logging import get_logger
from prismal.identity.types import OnBehalfToken, Scope

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence

    from prismal.identity.types import DID

logger = get_logger("prismal.identity.delegation")

# In-process revocation registry (token ids). Never enters checkpointed state.
_revoked: set[str] = set()


def _covers(granted: Sequence[Scope], resource: str) -> bool:
    return any(g.matches("", resource) for g in granted)


def mint_on_behalf(
    *, subject: str, scopes: Sequence[Scope], ttl_s: int, issuer: DID
) -> OnBehalfToken:
    """Mint a scoped, short-TTL on-behalf token for ``subject``, issued by ``issuer``."""
    token = OnBehalfToken(
        token_id=uuid.uuid4().hex,
        subject=subject,
        scopes=tuple(scopes),
        expires_at=time.time() + ttl_s,
        chain=(issuer,),
    )
    logger.info(
        "on_behalf.mint",
        token_id=token.token_id,
        issuer=issuer,
        scopes=[s.resource for s in scopes],
    )
    return token


def propagate(
    token: OnBehalfToken, *, via: DID, scopes: Sequence[Scope] | None = None
) -> OnBehalfToken:
    """Append ``via`` to the chain; optionally narrow scopes (never widen).

    (Implementation note: the SPEC-IDN-DEL-001 signature is extended with an
    optional ``scopes`` to express narrowing at a hop; omitting it keeps the
    current scopes. A requested scope not covered by the token's current scopes
    is a widening attempt and raises :class:`DelegationError`.)
    """
    if scopes is None:
        new_scopes = token.scopes
    else:
        for want in scopes:
            if not _covers(token.scopes, want.resource):
                raise DelegationError(
                    f"propagate may not widen scope {want.resource!r} "
                    f"(held: {[s.resource for s in token.scopes]})"
                )
        new_scopes = tuple(scopes)
    propagated = replace(token, scopes=new_scopes, chain=(*token.chain, via))
    logger.info(
        "on_behalf.propagate", token_id=token.token_id, via=via, chain=list(propagated.chain)
    )
    return propagated


def revoke(token_id: str) -> None:
    """Revoke ``token_id`` so subsequent :func:`validate` calls fail."""
    _revoked.add(token_id)
    logger.info("on_behalf.revoke", token_id=token_id)


def validate(token: OnBehalfToken, *, action: str, resource: str, now: float | None = None) -> bool:  # noqa: ARG001 — `action` reserved by the SPEC-IDN-DEL-001 signature for richer future rules
    """Return whether ``token`` authorizes ``(action, resource)`` right now.

    False when the token is revoked, expired, or does not carry a scope covering
    ``resource``. Never raises.
    """
    if token.token_id in _revoked:
        return False
    if (now if now is not None else time.time()) >= token.expires_at:
        return False
    return _covers(token.scopes, resource)


# ── State threading (state["metadata"]["identity"]) ────────────────────────────


def _to_state(token: OnBehalfToken) -> dict[str, Any]:
    return {
        "token_id": token.token_id,
        "subject": token.subject,
        "scopes": [s.resource for s in token.scopes],
        "expires_at": token.expires_at,
        "chain": list(token.chain),
    }


def _from_state(data: dict[str, Any]) -> OnBehalfToken:
    return OnBehalfToken(
        token_id=data["token_id"],
        subject=data["subject"],
        scopes=tuple(Scope(r) for r in data["scopes"]),
        expires_at=data["expires_at"],
        chain=tuple(data["chain"]),
    )


def put_on_behalf(state: MutableMapping[str, Any], token: OnBehalfToken) -> None:
    """Store the on-behalf token under ``state['metadata']['identity']['on_behalf']``."""
    metadata = state.setdefault("metadata", {})
    identity = metadata.setdefault("identity", {})
    identity["on_behalf"] = _to_state(token)


def get_on_behalf(state: MutableMapping[str, Any]) -> OnBehalfToken | None:
    """Read the on-behalf token from state, or ``None`` if absent."""
    payload = state.get("metadata", {}).get("identity", {}).get("on_behalf")
    return _from_state(payload) if payload else None


__all__ = [
    "get_on_behalf",
    "mint_on_behalf",
    "propagate",
    "put_on_behalf",
    "revoke",
    "validate",
]
