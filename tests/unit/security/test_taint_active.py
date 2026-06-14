"""Tests for the per-run active taint registry helper (Phase H — H1-04)."""

from __future__ import annotations

from prismal.security.taint import (
    Provenance,
    TaintRegistry,
    get_active_taint_registry,
    mark_untrusted_active,
    use_taint_registry,
)


def test_no_active_registry_by_default() -> None:
    assert get_active_taint_registry() is None


def test_mark_untrusted_active_is_noop_without_registry() -> None:
    # Must not raise when no registry is active (the disabled path).
    tag = mark_untrusted_active("untrusted content", Provenance.RAG)
    assert tag is None


def test_use_taint_registry_sets_and_resets() -> None:
    reg = TaintRegistry()
    assert get_active_taint_registry() is None
    with use_taint_registry(reg):
        assert get_active_taint_registry() is reg
        tag = mark_untrusted_active("from a loader", Provenance.RAG)
        assert tag is not None
        assert tag.provenance is Provenance.RAG
    # Reset after the context exits.
    assert get_active_taint_registry() is None
    assert reg.is_untrusted("from a loader")


def test_use_taint_registry_restores_previous() -> None:
    outer = TaintRegistry()
    inner = TaintRegistry()
    with use_taint_registry(outer):
        with use_taint_registry(inner):
            assert get_active_taint_registry() is inner
        assert get_active_taint_registry() is outer
