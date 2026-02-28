"""Unit tests for DocumentProcessorFactory (lightagent.rag.loaders).

Tests follow the TDD spec from T-060.  We use tmp_path to create real
on-disk files for txt / md / csv / json and unittest.mock to avoid needing
real PDF/DOCX binaries.

We do NOT test file-not-found or I/O errors — the underlying LangChain
loaders own that responsibility.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path  # noqa: TC003  -- used at runtime: tmp_path / "..."
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from lightagent.rag.loaders import (
    DocumentProcessorFactory,
    UnsupportedDocumentTypeError,
)

# ── SUPPORTED_EXTENSIONS constant ──────────────────────────────────────────────


def test_supported_extensions_is_frozenset() -> None:
    """SUPPORTED_EXTENSIONS must be a frozenset."""
    assert isinstance(DocumentProcessorFactory.SUPPORTED_EXTENSIONS, frozenset)


def test_supported_extensions_contains_pdf() -> None:
    """SUPPORTED_EXTENSIONS must include .pdf."""
    assert ".pdf" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_contains_docx() -> None:
    """SUPPORTED_EXTENSIONS must include .docx."""
    assert ".docx" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_contains_txt() -> None:
    """SUPPORTED_EXTENSIONS must include .txt."""
    assert ".txt" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_contains_md() -> None:
    """SUPPORTED_EXTENSIONS must include .md."""
    assert ".md" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_contains_csv() -> None:
    """SUPPORTED_EXTENSIONS must include .csv."""
    assert ".csv" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_contains_json() -> None:
    """SUPPORTED_EXTENSIONS must include .json."""
    assert ".json" in DocumentProcessorFactory.SUPPORTED_EXTENSIONS


def test_supported_extensions_has_exactly_six() -> None:
    """SUPPORTED_EXTENSIONS must have exactly 6 entries."""
    assert len(DocumentProcessorFactory.SUPPORTED_EXTENSIONS) == 6


# ── .txt loading ──────────────────────────────────────────────────────────────


def test_load_txt_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .txt file must return a non-empty list[Document]."""
    txt_file = tmp_path / "hello.txt"
    txt_file.write_text("Hello, world!\n", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)

    assert isinstance(docs, list)
    assert len(docs) >= 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_txt_document_contains_content(tmp_path: Path) -> None:
    """Documents loaded from a .txt file must contain the file content."""
    txt_file = tmp_path / "content.txt"
    txt_file.write_text("Sample text content", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)

    combined = " ".join(d.page_content for d in docs)
    assert "Sample text content" in combined


def test_load_txt_sets_metadata_source(tmp_path: Path) -> None:
    """Documents loaded from .txt must have metadata['source'] set to file path."""
    txt_file = tmp_path / "meta.txt"
    txt_file.write_text("metadata test", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)

    assert len(docs) >= 1
    assert docs[0].metadata["source"] == str(txt_file)


# ── .md loading ───────────────────────────────────────────────────────────────


def test_load_md_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .md file must return a non-empty list[Document]."""
    md_file = tmp_path / "readme.md"
    md_file.write_text("# Title\n\nSome markdown text.\n", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(md_file)

    assert isinstance(docs, list)
    assert len(docs) >= 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_md_sets_metadata_source(tmp_path: Path) -> None:
    """Documents loaded from .md must have metadata['source'] set to file path."""
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Heading\nBody text.", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(md_file)

    assert len(docs) >= 1
    assert docs[0].metadata["source"] == str(md_file)


# ── .csv loading ──────────────────────────────────────────────────────────────


def test_load_csv_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .csv file must return a non-empty list[Document]."""
    csv_file = tmp_path / "data.csv"
    csv_file.write_text(
        "name,age,city\nAlice,30,London\nBob,25,Paris\n", encoding="utf-8"
    )

    factory = DocumentProcessorFactory()
    docs = factory.load(csv_file)

    assert isinstance(docs, list)
    assert len(docs) >= 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_csv_sets_metadata_source(tmp_path: Path) -> None:
    """Documents loaded from .csv must have metadata['source'] set to file path."""
    csv_file = tmp_path / "rows.csv"
    csv_file.write_text("col1,col2\nval1,val2\n", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(csv_file)

    assert len(docs) >= 1
    # CSVLoader uses the row number as the source; we override it so check our override
    assert docs[0].metadata["source"] == str(csv_file)


# ── .json loading ─────────────────────────────────────────────────────────────


def test_load_json_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .json file must call JSONLoader and return its documents.

    JSONLoader requires the optional ``jq`` C-extension which may not be
    installed in every environment, so we mock it like PDF/DOCX.
    """
    json_file = tmp_path / "items.json"
    payload = [{"id": 1, "text": "first"}, {"id": 2, "text": "second"}]
    json_file.write_text(json.dumps(payload), encoding="utf-8")

    fake_doc = Document(
        page_content='{"id": 1, "text": "first"}',
        metadata={"source": str(json_file), "seq_num": 1},
    )

    with patch(
        "lightagent.rag.loaders.JSONLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(json_file)

    assert isinstance(docs, list)
    assert len(docs) == 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_json_calls_jsonloader_with_correct_args(tmp_path: Path) -> None:
    """load() must instantiate JSONLoader with jq_schema='.' and text_content=False."""
    json_file = tmp_path / "data.json"
    json_file.write_text('{"key": "value"}', encoding="utf-8")

    fake_doc = Document(
        page_content='{"key": "value"}',
        metadata={"source": str(json_file)},
    )

    with patch(
        "lightagent.rag.loaders.JSONLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        factory.load(json_file)

    mock_loader_cls.assert_called_once_with(
        str(json_file), jq_schema=".", text_content=False
    )


def test_load_json_sets_metadata_source(tmp_path: Path) -> None:
    """Documents from JSON load must have metadata['source'] set to the file path."""
    json_file = tmp_path / "data.json"
    json_file.write_text('{"key": "value"}', encoding="utf-8")

    # Loader returns doc WITHOUT source; factory must set it
    fake_doc = Document(page_content='{"key": "value"}', metadata={})

    with patch(
        "lightagent.rag.loaders.JSONLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(json_file)

    assert len(docs) >= 1
    assert docs[0].metadata["source"] == str(json_file)


# ── .pdf loading (mocked) ─────────────────────────────────────────────────────


def test_load_pdf_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .pdf path must call PyPDFLoader and return its documents."""
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")  # not a real PDF; loader is mocked

    fake_doc = Document(
        page_content="PDF page content",
        metadata={"source": str(pdf_file), "page": 0},
    )

    with patch(
        "lightagent.rag.loaders.PyPDFLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(pdf_file)

    assert isinstance(docs, list)
    assert len(docs) == 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_pdf_calls_pypdfloader_with_path(tmp_path: Path) -> None:
    """load() must instantiate PyPDFLoader with the string file path."""
    pdf_file = tmp_path / "slides.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    fake_doc = Document(
        page_content="slide text",
        metadata={"source": str(pdf_file)},
    )

    with patch(
        "lightagent.rag.loaders.PyPDFLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        factory.load(pdf_file)

    mock_loader_cls.assert_called_once_with(str(pdf_file))


def test_load_pdf_sets_metadata_source(tmp_path: Path) -> None:
    """Documents from PDF load must have metadata['source'] set to the file path."""
    pdf_file = tmp_path / "doc.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    # Loader returns doc WITHOUT source; factory must set it
    fake_doc = Document(page_content="page", metadata={})

    with patch(
        "lightagent.rag.loaders.PyPDFLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(pdf_file)

    assert docs[0].metadata["source"] == str(pdf_file)


# ── .docx loading (mocked) ────────────────────────────────────────────────────


def test_load_docx_returns_list_of_documents(tmp_path: Path) -> None:
    """load() with a .docx path must call Docx2txtLoader and return its documents."""
    docx_file = tmp_path / "letter.docx"
    docx_file.write_bytes(b"PK fake docx bytes")  # not real; loader is mocked

    fake_doc = Document(
        page_content="Dear Sir/Madam...",
        metadata={"source": str(docx_file)},
    )

    with patch(
        "lightagent.rag.loaders.Docx2txtLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(docx_file)

    assert isinstance(docs, list)
    assert len(docs) == 1
    assert all(isinstance(d, Document) for d in docs)


def test_load_docx_calls_docx2txtloader_with_path(tmp_path: Path) -> None:
    """load() must instantiate Docx2txtLoader with the string file path."""
    docx_file = tmp_path / "memo.docx"
    docx_file.write_bytes(b"PK fake")

    fake_doc = Document(page_content="memo body", metadata={"source": str(docx_file)})

    with patch(
        "lightagent.rag.loaders.Docx2txtLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        factory.load(docx_file)

    mock_loader_cls.assert_called_once_with(str(docx_file))


def test_load_docx_sets_metadata_source(tmp_path: Path) -> None:
    """Documents from DOCX load must have metadata['source'] set to the file path."""
    docx_file = tmp_path / "notes.docx"
    docx_file.write_bytes(b"PK fake")

    fake_doc = Document(page_content="notes", metadata={})

    with patch(
        "lightagent.rag.loaders.Docx2txtLoader", autospec=True
    ) as mock_loader_cls:
        mock_instance = MagicMock()
        mock_instance.load.return_value = [fake_doc]
        mock_loader_cls.return_value = mock_instance

        factory = DocumentProcessorFactory()
        docs = factory.load(docx_file)

    assert docs[0].metadata["source"] == str(docx_file)


# ── Unsupported extension ──────────────────────────────────────────────────────


def test_unsupported_extension_raises_error(tmp_path: Path) -> None:
    """load() with an unsupported extension must raise UnsupportedDocumentTypeError."""
    bad_file = tmp_path / "data.xlsx"
    bad_file.write_bytes(b"PK fake xlsx")

    factory = DocumentProcessorFactory()
    with pytest.raises(UnsupportedDocumentTypeError):
        factory.load(bad_file)


def test_unsupported_extension_error_message_contains_ext(tmp_path: Path) -> None:
    """UnsupportedDocumentTypeError message must include the unsupported extension."""
    bad_file = tmp_path / "archive.tar"
    bad_file.write_bytes(b"fake tar data")

    factory = DocumentProcessorFactory()
    with pytest.raises(UnsupportedDocumentTypeError, match=r"\.tar"):
        factory.load(bad_file)


def test_unsupported_extension_html(tmp_path: Path) -> None:
    """load() with .html extension must raise UnsupportedDocumentTypeError."""
    html_file = tmp_path / "page.html"
    html_file.write_text("<html><body>test</body></html>", encoding="utf-8")

    factory = DocumentProcessorFactory()
    with pytest.raises(UnsupportedDocumentTypeError):
        factory.load(html_file)


def test_unsupported_extension_no_extension(tmp_path: Path) -> None:
    """load() with a file that has no extension must raise UnsupportedDocumentTypeError."""  # noqa: E501
    no_ext_file = tmp_path / "datafile"
    no_ext_file.write_text("binary-ish content", encoding="utf-8")

    factory = DocumentProcessorFactory()
    with pytest.raises(UnsupportedDocumentTypeError):
        factory.load(no_ext_file)


# ── UnsupportedDocumentTypeError is a LightAgentError ─────────────────────────


def test_unsupported_document_type_error_is_lightagent_error() -> None:
    """UnsupportedDocumentTypeError must be a subclass of LightAgentError."""
    from lightagent.core.exceptions import LightAgentError

    assert issubclass(UnsupportedDocumentTypeError, LightAgentError)


def test_unsupported_document_type_error_can_be_raised_with_ext() -> None:
    """UnsupportedDocumentTypeError must accept an extension argument and store it."""
    err = UnsupportedDocumentTypeError(".xlsx")
    assert ".xlsx" in str(err)


# ── metadata["source"] invariant across all real loaders ─────────────────────


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("file.txt", "plain text"),
        ("readme.md", "# Heading"),
    ],
)
def test_metadata_source_is_absolute_string(
    tmp_path: Path, filename: str, content: str
) -> None:
    """metadata['source'] must equal str(path) — the absolute path string."""
    target = tmp_path / filename
    target.write_text(content, encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(target)

    assert len(docs) >= 1
    for doc in docs:
        assert doc.metadata["source"] == str(target)


def test_load_accepts_path_object(tmp_path: Path) -> None:
    """load() must accept a pathlib.Path argument (not only str)."""
    txt_file = tmp_path / "pathobj.txt"
    txt_file.write_text("path object test", encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)  # txt_file is already a Path

    assert isinstance(docs, list)
    assert len(docs) >= 1


def test_load_multiline_txt(tmp_path: Path) -> None:
    """load() must return the full content for multi-line text files."""
    content = textwrap.dedent(
        """\
        Line one.
        Line two.
        Line three.
        """
    )
    txt_file = tmp_path / "multi.txt"
    txt_file.write_text(content, encoding="utf-8")

    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)

    combined = "\n".join(d.page_content for d in docs)
    assert "Line one" in combined
    assert "Line two" in combined


def test_load_accepts_uppercase_extension(tmp_path: Path) -> None:
    """load() handles uppercase extensions case-insensitively."""
    txt_file = tmp_path / "DOCUMENT.TXT"
    txt_file.write_text("uppercase extension content", encoding="utf-8")
    factory = DocumentProcessorFactory()
    docs = factory.load(txt_file)
    assert len(docs) >= 1
    assert docs[0].metadata["source"] == str(txt_file)
