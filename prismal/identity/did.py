"""W3C DID operations for agent identity (SPEC-IDN-DID-001).

Two methods are supported:

* ``did:key`` — self-contained, offline: the Ed25519 public key is multicodec-
  tagged, base58btc-encoded and embedded in the identifier itself, so a
  ``did:key`` round-trips issue→resolve→verify with no network. Used for
  internal agents.
* ``did:web`` — resolvable over HTTPS at ``/.well-known/did.json`` (or a path-
  derived location); used for agents exposed via A2A so external parties can
  fetch the DID Document.

Only the general-purpose ``cryptography`` and ``httpx`` libraries are used (no
LLM-provider SDK — Rule #4 is about model providers, honoured by keeping those
in ``prismal/providers/``). base58btc is implemented inline to avoid a new
dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from prismal.core.exceptions import DidVerificationError

if TYPE_CHECKING:
    from prismal.identity.types import DID, AgentIdentity

# Multicodec varint prefix for an Ed25519 public key (0xed → varint 0xed 0x01).
_ED25519_MULTICODEC = b"\xed\x01"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_DID_DOCUMENT_FILENAME = "did.json"
_ED25519_2020_CONTEXT = [
    "https://www.w3.org/ns/did/v1",
    "https://w3id.org/security/suites/ed25519-2020/v1",
]


# ── base58btc (Bitcoin alphabet) ───────────────────────────────────────────────


def _b58encode(data: bytes) -> str:
    """Encode bytes as base58btc, preserving leading-zero bytes as ``1``."""
    n = int.from_bytes(data, "big")
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n, 58)
        chars.append(_BASE58_ALPHABET[rem])
    pad = len(data) - len(data.lstrip(b"\x00"))
    return _BASE58_ALPHABET[0] * pad + "".join(reversed(chars))


def _b58decode(text: str) -> bytes:
    """Decode a base58btc string back to bytes (inverse of :func:`_b58encode`)."""
    n = 0
    for char in text:
        index = _BASE58_ALPHABET.find(char)
        if index == -1:
            raise DidVerificationError(f"Invalid base58 character: {char!r}")
        n = n * 58 + index
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip(_BASE58_ALPHABET[0]))
    return b"\x00" * pad + body


# ── multibase helpers ──────────────────────────────────────────────────────────


def _multibase_for_pubkey(public_key: bytes) -> str:
    """Return the ``z…`` multibase-base58btc of a multicodec-tagged Ed25519 key."""
    return "z" + _b58encode(_ED25519_MULTICODEC + public_key)


def _pubkey_from_multibase(multibase: str) -> bytes:
    """Recover the raw 32-byte Ed25519 key from a ``z…`` multibase string."""
    if not multibase.startswith("z"):
        raise DidVerificationError(f"Unsupported multibase prefix: {multibase[:1]!r}")
    decoded = _b58decode(multibase[1:])
    if not decoded.startswith(_ED25519_MULTICODEC):
        raise DidVerificationError("Not an Ed25519 multicodec key")
    return decoded[len(_ED25519_MULTICODEC) :]


# ── issue ──────────────────────────────────────────────────────────────────────


def issue_did_key(public_key: bytes) -> DID:
    """Issue a ``did:key`` for a raw 32-byte Ed25519 public key."""
    return "did:key:" + _multibase_for_pubkey(public_key)


def issue_did_web(domain: str, path: str) -> DID:
    """Issue a ``did:web`` identifier for ``domain`` and an optional ``/``-path."""
    identifier = domain
    segments = [seg for seg in path.split("/") if seg]
    if segments:
        identifier += ":" + ":".join(segments)
    return "did:web:" + identifier


# ── resolve ──────────────────────────────────────────────────────────────────


def _did_document_from_key(did: DID, multibase: str) -> dict[str, Any]:
    """Build a W3C DID Document for ``did`` with one Ed25519 verification method."""
    key_id = f"{did}#key-1"
    return {
        "@context": list(_ED25519_2020_CONTEXT),
        "id": did,
        "verificationMethod": [
            {
                "id": key_id,
                "type": "Ed25519VerificationKey2020",
                "controller": did,
                "publicKeyMultibase": multibase,
            }
        ],
        "authentication": [key_id],
        "assertionMethod": [key_id],
    }


def _did_web_url(did: DID) -> str:
    """Map a ``did:web`` identifier to its DID Document URL (W3C did-web rules)."""
    identifier = did[len("did:web:") :]
    domain, *segments = identifier.split(":")
    if segments:
        return f"https://{domain}/" + "/".join(segments) + f"/{_DID_DOCUMENT_FILENAME}"
    return f"https://{domain}/.well-known/{_DID_DOCUMENT_FILENAME}"


async def resolve_did(did: DID) -> dict[str, Any]:
    """Resolve ``did`` to its DID Document.

    ``did:key`` is resolved offline (the key is embedded); ``did:web`` is fetched
    over HTTPS. Raises :class:`DidVerificationError` for an unsupported or
    unreachable DID.
    """
    if did.startswith("did:key:"):
        multibase = did[len("did:key:") :]
        # Validate the embedded key before advertising the document.
        _pubkey_from_multibase(multibase)
        return _did_document_from_key(did, multibase)

    if did.startswith("did:web:"):
        url = _did_web_url(did)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                document: dict[str, Any] = response.json()
                return document
        except httpx.HTTPError as exc:
            raise DidVerificationError(f"Could not resolve {did}: {exc}") from exc

    raise DidVerificationError(f"Unsupported DID method: {did!r}")


# ── verify ─────────────────────────────────────────────────────────────────────


def _verify_signature(public_key: bytes, signature: bytes, payload: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        return True
    except (InvalidSignature, ValueError):
        return False


async def verify_did(did: DID, *, signature: bytes | None = None) -> bool:
    """Return whether ``did`` is valid (and, if given, the signature over the DID).

    Never raises — an unparseable DID, an unresolvable ``did:web`` or a bad
    signature all return ``False``. With ``signature=None`` it is a structural /
    resolvability check.
    """
    try:
        if did.startswith("did:key:"):
            public_key = _pubkey_from_multibase(did[len("did:key:") :])
        elif did.startswith("did:web:"):
            document = await resolve_did(did)
            methods = document.get("verificationMethod") or []
            if not methods:
                return signature is None and bool(document.get("id"))
            public_key = _pubkey_from_multibase(methods[0]["publicKeyMultibase"])
        else:
            return False
    except (DidVerificationError, KeyError):
        return False

    if signature is None:
        return True
    return _verify_signature(public_key, signature, did.encode())


# ── DID Document for the A2A Agent Card ────────────────────────────────────────


def did_document(identity: AgentIdentity, *, public_key: bytes | None = None) -> dict[str, Any]:
    """Produce the W3C DID Document embedded in the A2A Agent Card.

    For a ``did:key`` identity the public key is recovered from the DID itself.
    For a ``did:web`` identity the host must supply the published ``public_key``
    (the key is not embedded in the identifier). Raises
    :class:`DidVerificationError` otherwise.

    (Implementation note: the SPEC-IDN-DID-001 signature is extended with an
    optional keyword-only ``public_key`` so a ``did:web`` document can be built
    without changing the frozen :class:`AgentIdentity` value object.)
    """
    did = identity.did
    if did.startswith("did:key:"):
        multibase = did[len("did:key:") :]
        _pubkey_from_multibase(multibase)  # validate
    elif did.startswith("did:web:"):
        if public_key is None:
            raise DidVerificationError(f"did_document for {did} requires the published public_key")
        multibase = _multibase_for_pubkey(public_key)
    else:
        raise DidVerificationError(f"Unsupported DID method: {did!r}")
    return _did_document_from_key(did, multibase)


__all__ = [
    "did_document",
    "issue_did_key",
    "issue_did_web",
    "resolve_did",
    "verify_did",
]
