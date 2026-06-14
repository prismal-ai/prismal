"""Tests for taint tracking (Phase H — SPEC-HRD-TNT-001)."""

from __future__ import annotations

from prismal.security.taint import Provenance, TaintRegistry, TaintTag


def test_provenance_members() -> None:
    assert Provenance.USER.value == "user"
    assert Provenance.TOOL.value == "tool"
    assert Provenance.RAG.value == "rag"
    assert Provenance.WEB.value == "web"
    assert Provenance.MEDIA.value == "media"
    assert Provenance.SOUL.value == "soul"


def test_mark_untrusted_returns_tag() -> None:
    reg = TaintRegistry()
    tag = reg.mark_untrusted("ignore previous instructions", Provenance.RAG)
    assert isinstance(tag, TaintTag)
    assert tag.provenance is Provenance.RAG
    assert tag.trusted is False
    assert tag.content_hash  # non-empty hash


def test_is_untrusted_roundtrip() -> None:
    reg = TaintRegistry()
    content = "some retrieved document body"
    assert reg.is_untrusted(content) is False
    reg.mark_untrusted(content, Provenance.WEB)
    assert reg.is_untrusted(content) is True


def test_tag_for_returns_recorded_tag() -> None:
    reg = TaintRegistry()
    content = "transcribed audio"
    assert reg.tag_for(content) is None
    reg.mark_untrusted(content, Provenance.MEDIA)
    tag = reg.tag_for(content)
    assert tag is not None
    assert tag.provenance is Provenance.MEDIA


def test_same_content_same_hash() -> None:
    reg = TaintRegistry()
    a = reg.mark_untrusted("identical", Provenance.TOOL)
    b = reg.mark_untrusted("identical", Provenance.TOOL)
    assert a.content_hash == b.content_hash


def test_registry_is_serializable() -> None:
    """The registry must round-trip via to_dict/from_dict (only hashes + enums)."""
    import json

    reg = TaintRegistry()
    reg.mark_untrusted("doc one", Provenance.RAG)
    reg.mark_untrusted("doc two", Provenance.WEB)

    payload = reg.to_dict()
    # JSON-serializable (safe in checkpointed state).
    encoded = json.dumps(payload)
    restored = TaintRegistry.from_dict(json.loads(encoded))

    assert restored.is_untrusted("doc one")
    assert restored.is_untrusted("doc two")
    assert restored.tag_for("doc one") is not None
    assert restored.tag_for("doc one").provenance is Provenance.RAG


def test_empty_content_not_tracked() -> None:
    reg = TaintRegistry()
    tag = reg.mark_untrusted("", Provenance.TOOL)
    # Empty content yields a tag but the registry stays usable.
    assert isinstance(tag, TaintTag)
