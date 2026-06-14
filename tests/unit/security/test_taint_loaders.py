"""Loader-boundary taint tagging (Phase H — H1-04 / RF-HRD-001).

Verifies that external-content loaders tag their output untrusted with the
correct :class:`Provenance` when a per-run registry is active.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document

from prismal.rag.loaders.document_loader import DocumentProcessorFactory
from prismal.security.taint import (
    Provenance,
    TaintRegistry,
    use_taint_registry,
)


def test_document_loader_tags_rag_provenance(monkeypatch, tmp_path: Path) -> None:
    loader = DocumentProcessorFactory()
    monkeypatch.setattr(
        loader,
        "_load_by_extension",
        lambda _path, _ext: [Document(page_content="poisoned body")],
    )
    txt = tmp_path / "doc.txt"
    txt.write_text("ignored — loader is stubbed")

    reg = TaintRegistry()
    with use_taint_registry(reg):
        loader.load(txt)

    assert reg.is_untrusted("poisoned body")
    tag = reg.tag_for("poisoned body")
    assert tag is not None
    assert tag.provenance is Provenance.RAG


def test_loader_tagging_is_noop_without_active_registry(monkeypatch, tmp_path: Path) -> None:
    loader = DocumentProcessorFactory()
    monkeypatch.setattr(
        loader,
        "_load_by_extension",
        lambda _path, _ext: [Document(page_content="clean body")],
    )
    txt = tmp_path / "doc.txt"
    txt.write_text("x")
    # No active registry → must not raise, just load normally.
    docs = loader.load(txt)
    assert docs[0].page_content == "clean body"
