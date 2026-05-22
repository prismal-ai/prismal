"""Document loaders for the Prismal RAG system.

Provides ``DocumentProcessorFactory``, which maps file extensions to the
appropriate LangChain document loader and returns a normalised
``list[Document]`` with ``metadata["source"]`` guaranteed to be set to the
absolute file-path string.

Supported extensions: .pdf, .docx, .txt, .md, .csv, .json

Example::

    from pathlib import Path
    from prismal.rag.loaders import DocumentProcessorFactory

    factory = DocumentProcessorFactory()
    docs = factory.load(Path("report.pdf"))
    for doc in docs:
        print(doc.metadata["source"], len(doc.page_content))
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  -- used at runtime: path.suffix, str(path)
from typing import TYPE_CHECKING

from langchain_community.document_loaders import (
    CSVLoader,
    Docx2txtLoader,
    JSONLoader,
    PyPDFLoader,
    TextLoader,
)

from prismal.core.exceptions import PrismalError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.documents import Document

logger = get_logger("prismal.rag.loaders")

# ── Exceptions ────────────────────────────────────────────────────────────────


class UnsupportedDocumentTypeError(PrismalError):
    """Raised when a file has an extension not supported by DocumentProcessorFactory.

    Args:
        extension: The unsupported file extension (e.g. ``".xlsx"``).
    """

    def __init__(self, extension: str) -> None:
        """Initialise UnsupportedDocumentTypeError."""
        self.extension = extension
        super().__init__(
            f"Unsupported document type: '{extension}'. "
            "Supported extensions: "
            f"{sorted(DocumentProcessorFactory.SUPPORTED_EXTENSIONS)}"
        )


# ── Factory ───────────────────────────────────────────────────────────────────


class DocumentProcessorFactory:
    """Factory that loads documents from disk using the correct LangChain loader.

    Selects the loader based on the file extension and ensures every returned
    ``Document`` has ``metadata["source"]`` set to the absolute path string of
    the source file.

    Class Attributes:
        SUPPORTED_EXTENSIONS: Frozenset of supported file extension strings
            (lower-case, including the leading dot).
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}
    )

    def load(self, path: Path) -> list[Document]:
        """Load documents from *path* using the appropriate LangChain loader.

        The file extension is matched case-insensitively.  All returned
        ``Document`` objects are guaranteed to have ``metadata["source"]``
        set to ``str(path)``.

        Args:
            path: Absolute or relative path to the document.  Must have a
                supported extension; see ``SUPPORTED_EXTENSIONS``.

        Returns:
            A non-empty list of ``Document`` objects whose
            ``metadata["source"]`` is the string representation of *path*.

        Raises:
            UnsupportedDocumentTypeError: If the file extension is not in
                ``SUPPORTED_EXTENSIONS``.
        """
        extension = path.suffix.lower()

        if extension not in self.SUPPORTED_EXTENSIONS:
            logger.warning(
                "unsupported_document_type",
                path=str(path),
                extension=extension,
            )
            raise UnsupportedDocumentTypeError(extension)

        logger.debug("loading_document", path=str(path), extension=extension)
        docs = self._load_by_extension(path, extension)
        docs = self._ensure_source_metadata(docs, path)

        logger.info(
            "document_loaded",
            path=str(path),
            extension=extension,
            num_docs=len(docs),
        )
        return docs

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_by_extension(self, path: Path, extension: str) -> list[Document]:
        """Dispatch to the correct LangChain loader based on *extension*.

        Args:
            path: Path to the document file.
            extension: Lower-cased file extension including the leading dot.

        Returns:
            Raw list of ``Document`` objects from the loader.
        """
        path_str = str(path)

        if extension == ".pdf":
            return PyPDFLoader(path_str).load()

        if extension == ".docx":
            return Docx2txtLoader(path_str).load()

        if extension in {".txt", ".md"}:
            return TextLoader(path_str, encoding="utf-8").load()

        if extension == ".csv":
            return CSVLoader(path_str).load()

        # extension == ".json" — only remaining supported case
        return JSONLoader(path_str, jq_schema=".", text_content=False).load()

    @staticmethod
    def _ensure_source_metadata(docs: list[Document], path: Path) -> list[Document]:
        """Set ``metadata["source"]`` to ``str(path)`` on every document.

        Loaders may set a different value (e.g. CSVLoader uses row numbers as
        the source) or omit the key entirely.  This method normalises the
        field so callers have a consistent guarantee.

        Args:
            docs: Documents returned by a LangChain loader.
            path: The original file path passed to ``load()``.

        Returns:
            The same list with ``metadata["source"]`` set on every document.
        """
        path_str = str(path)
        for doc in docs:
            doc.metadata["source"] = path_str
        return docs


# Module-level alias so callers can import the set without instantiating the
# factory or triggering mock interference in tests.
SUPPORTED_EXTENSIONS: frozenset[str] = DocumentProcessorFactory.SUPPORTED_EXTENSIONS

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "DocumentProcessorFactory",
    "UnsupportedDocumentTypeError",
]
