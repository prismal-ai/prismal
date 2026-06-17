"""Tests for the DID operations (Phase IDN — SPEC-IDN-DID-001).

ID2 "done when": a ``did:key`` round-trips issue→resolve→verify offline; a
tampered signature fails ``verify_did``; a ``did:web`` resolves over HTTPS.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from prismal.identity.did import (
    did_document,
    issue_did_key,
    issue_did_web,
    resolve_did,
    verify_did,
)
from prismal.identity.types import AgentIdentity, Scope


def _keypair() -> tuple[Ed25519PrivateKey, bytes]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub


# ── did:key issue / resolve ───────────────────────────────────────────────────


def test_issue_did_key_has_multibase_prefix() -> None:
    """A did:key is the multibase-base58btc of the multicodec-tagged key."""
    _, pub = _keypair()
    did = issue_did_key(pub)
    assert did.startswith("did:key:z6Mk")  # Ed25519 multicodec → z6Mk… prefix


async def test_did_key_resolves_to_self_describing_document() -> None:
    """resolve_did rebuilds the DID Document from the key embedded in the DID."""
    _, pub = _keypair()
    did = issue_did_key(pub)
    doc = await resolve_did(did)
    assert doc["id"] == did
    vm = doc["verificationMethod"][0]
    assert vm["controller"] == did
    assert vm["type"] == "Ed25519VerificationKey2020"
    assert vm["publicKeyMultibase"].startswith("z6Mk")


# ── did:key verify ─────────────────────────────────────────────────────────────


async def test_verify_did_key_structural_is_true() -> None:
    """A well-formed did:key verifies offline without a signature."""
    _, pub = _keypair()
    assert await verify_did(issue_did_key(pub)) is True


async def test_verify_did_key_with_valid_signature() -> None:
    """A signature over the DID by the matching private key verifies."""
    priv, pub = _keypair()
    did = issue_did_key(pub)
    sig = priv.sign(did.encode())
    assert await verify_did(did, signature=sig) is True


async def test_verify_did_key_with_tampered_signature_fails() -> None:
    """A signature over a different payload fails verification."""
    priv, pub = _keypair()
    did = issue_did_key(pub)
    sig = priv.sign(b"not the did")
    assert await verify_did(did, signature=sig) is False


async def test_verify_malformed_did_is_false() -> None:
    """An unparseable / unsupported DID never raises — it returns False."""
    assert await verify_did("did:bogus:xyz") is False
    assert await verify_did("not-a-did") is False


# ── did:web issue / resolve ────────────────────────────────────────────────────


def test_issue_did_web_without_path() -> None:
    assert issue_did_web("org.example", "") == "did:web:org.example"


def test_issue_did_web_with_path() -> None:
    assert issue_did_web("org.example", "agents/coder") == "did:web:org.example:agents:coder"


@respx.mock
async def test_resolve_did_web_fetches_over_https() -> None:
    """did:web resolves to https://<domain>/<path>/did.json."""
    did = "did:web:org.example:agents:coder"
    document = {"id": did, "verificationMethod": []}
    route = respx.get("https://org.example/agents/coder/did.json").mock(
        return_value=httpx.Response(200, json=document)
    )
    resolved = await resolve_did(did)
    assert route.called
    assert resolved["id"] == did


@respx.mock
async def test_resolve_did_web_root_uses_well_known() -> None:
    """A did:web with no path resolves to /.well-known/did.json."""
    did = "did:web:org.example"
    route = respx.get("https://org.example/.well-known/did.json").mock(
        return_value=httpx.Response(200, json={"id": did})
    )
    await resolve_did(did)
    assert route.called


# ── did_document() for the A2A Agent Card ──────────────────────────────────────


def test_did_document_for_did_key_recovers_embedded_key() -> None:
    _, pub = _keypair()
    ident = AgentIdentity(did=issue_did_key(pub), agent_name="coder")
    doc = did_document(ident)
    assert doc["@context"][0] == "https://www.w3.org/ns/did/v1"
    assert doc["id"] == ident.did
    assert doc["authentication"] == [f"{ident.did}#key-1"]
    assert doc["assertionMethod"] == [f"{ident.did}#key-1"]


def test_did_document_for_did_web_uses_supplied_key() -> None:
    """For a did:web identity the published public key is supplied by the host."""
    _, pub = _keypair()
    ident = AgentIdentity(
        did="did:web:org.example:agents:coder",
        agent_name="coder",
        scopes=(Scope("tools:write_file"), Scope("rag:read")),
    )
    doc = did_document(ident, public_key=pub)
    assert doc["id"] == ident.did
    vm = doc["verificationMethod"][0]
    assert vm["controller"] == ident.did
    assert vm["publicKeyMultibase"].startswith("z6Mk")


def test_did_document_for_did_web_without_key_raises() -> None:
    """A did:web doc cannot be built without the published key."""
    from prismal.core.exceptions import DidVerificationError

    ident = AgentIdentity(did="did:web:org.example:agents:coder", agent_name="coder")
    with pytest.raises(DidVerificationError):
        did_document(ident)


def test_did_document_unsupported_method_raises() -> None:
    from prismal.core.exceptions import DidVerificationError

    ident = AgentIdentity(did="did:bogus:xyz", agent_name="coder")
    with pytest.raises(DidVerificationError):
        did_document(ident)


# ── did:web verify + error paths ───────────────────────────────────────────────


@respx.mock
async def test_verify_did_web_with_valid_signature() -> None:
    """A remote did:web is verified against the key in its published document."""
    priv, pub = _keypair()
    did = "did:web:org.example:agents:coder"
    document = did_document(AgentIdentity(did=did, agent_name="coder"), public_key=pub)
    respx.get("https://org.example/agents/coder/did.json").mock(
        return_value=httpx.Response(200, json=document)
    )
    signature = priv.sign(did.encode())
    assert await verify_did(did, signature=signature) is True


@respx.mock
async def test_verify_did_web_unreachable_is_false() -> None:
    respx.get("https://org.example/.well-known/did.json").mock(return_value=httpx.Response(404))
    assert await verify_did("did:web:org.example") is False


@respx.mock
async def test_resolve_did_web_unreachable_raises() -> None:
    from prismal.core.exceptions import DidVerificationError

    respx.get("https://org.example/.well-known/did.json").mock(return_value=httpx.Response(500))
    with pytest.raises(DidVerificationError):
        await resolve_did("did:web:org.example")


# ── malformed did:key never raises out of verify ──────────────────────────────


async def test_verify_did_key_bad_multibase_prefix_is_false() -> None:
    """A did:key whose multibase isn't base58btc ('z') returns False, not raises."""
    assert await verify_did("did:key:Qabc") is False


async def test_verify_did_key_invalid_base58_is_false() -> None:
    """A '0' is not in the base58 alphabet → False, not a raised error."""
    assert await verify_did("did:key:z0") is False


async def test_resolve_did_unsupported_method_raises() -> None:
    from prismal.core.exceptions import DidVerificationError

    with pytest.raises(DidVerificationError):
        await resolve_did("did:bogus:xyz")


@respx.mock
async def test_verify_did_web_without_methods_is_structural() -> None:
    """A did:web doc with no verificationMethod still verifies structurally."""
    did = "did:web:org.example"
    respx.get("https://org.example/.well-known/did.json").mock(
        return_value=httpx.Response(200, json={"id": did})
    )
    assert await verify_did(did) is True


async def test_resolve_did_key_non_ed25519_raises() -> None:
    """A well-formed multibase that isn't an Ed25519 multicodec key is rejected."""
    from prismal.core.exceptions import DidVerificationError
    from prismal.identity.did import _b58encode

    # 0x12 0x20 is the sha2-256 multicodec prefix, not Ed25519 (0xed 0x01).
    multibase = "z" + _b58encode(b"\x12\x20" + b"\x00" * 32)
    with pytest.raises(DidVerificationError):
        await resolve_did(f"did:key:{multibase}")
