"""Unit tests for RAGEngine (prismal.rag.engine).

Tests follow the TDD spec from T-064.  ChromaVectorStore, DocumentProcessorFactory,
and CRAGPipeline are all mocked so no real ChromaDB instances, embedding models,
or LLM calls are made.

Coverage targets:
- RAGEngine.__init__ creates ChromaVectorStore with collection_name and settings
- RAGEngine.__init__ passes settings through correctly
- index_file() calls DocumentProcessorFactory().load(path) and store.add_documents(docs)
- index_file() calls store.delete_by_source() before adding (AC-005-7)
- index_file() returns the number of chunks indexed
- index_file() propagates UnsupportedDocumentTypeError for unsupported extensions
- index_directory() recursively indexes supported files, skips unsupported
- index_directory() returns total chunk count across all files
- index_directory() catches per-file errors without crashing
- search() calls store.similarity_search(query, k=k) and returns list[RetrievedChunk]
- search() converts (Document, float) tuples to RetrievedChunk correctly
- search() defaults k=5
- query() creates CRAGPipeline and calls pipeline.run(query)
- query() returns CRAGResult
- create_collection() returns a new RAGEngine with the given collection name
- delete_collection() calls store.delete_collection()
- collection_name property returns the correct value
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.rag.crag import CRAGResult, RetrievedChunk

# ── Patch paths ───────────────────────────────────────────────────────────────

CHROMA_VECTOR_STORE_PATH = "prismal.rag.engine.ChromaVectorStore"
DOCUMENT_PROCESSOR_FACTORY_PATH = "prismal.rag.engine.DocumentProcessorFactory"
CRAG_PIPELINE_PATH = "prismal.rag.engine.CRAGPipeline"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings() -> MagicMock:
    """Return a minimal mock Settings object."""
    return MagicMock()


def _make_document(
    content: str = "doc content",
    source: str = "/path/to/file.txt",
    chunk_id: str = "0",
) -> MagicMock:
    """Return a mock Document with page_content and metadata."""
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source, "chunk_id": chunk_id}
    return doc


def _build_engine(
    collection_name: str = "test_col",
    settings: MagicMock | None = None,
) -> tuple[object, MagicMock]:
    """Construct a RAGEngine under full mock context.

    Returns:
        (engine_instance, mock_store_instance)
    """
    if settings is None:
        settings = _make_settings()

    mock_store_instance = MagicMock()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store_instance
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name=collection_name, settings=settings)

    return engine, mock_store_instance


# ── RAGEngine.__init__ ────────────────────────────────────────────────────────


def test_init_creates_chroma_vector_store_with_collection_name() -> None:
    """__init__ must create ChromaVectorStore with the given collection_name."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        RAGEngine(collection_name="my_col", settings=settings)

    mock_store_cls.assert_called_once_with(collection_name="my_col", settings=settings)


def test_init_creates_chroma_vector_store_with_settings() -> None:
    """__init__ must pass the settings to ChromaVectorStore."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        RAGEngine(collection_name="col", settings=settings)

    call_kwargs = mock_store_cls.call_args.kwargs
    assert call_kwargs["settings"] is settings


def test_init_default_collection_name_is_default() -> None:
    """RAGEngine() with no collection_name arg must use 'default'."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)

    assert engine.collection_name == "default"


def test_init_stores_settings_when_provided() -> None:
    """__init__ must store the provided settings."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name="col", settings=settings)

    assert engine._settings is settings


def test_init_calls_get_settings_when_none() -> None:
    """__init__(settings=None) must call get_settings() to resolve config."""
    fake_settings = _make_settings()

    get_settings_path = "prismal.rag.engine.get_settings"
    with (
        patch(get_settings_path, return_value=fake_settings) as mock_gs,
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
    ):
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        RAGEngine(settings=None)

    mock_gs.assert_called_once()


# ── collection_name property ──────────────────────────────────────────────────


def test_collection_name_property_returns_constructor_value() -> None:
    """collection_name property must return the name passed to __init__."""
    engine, _ = _build_engine(collection_name="my_collection")
    assert engine.collection_name == "my_collection"


def test_collection_name_property_returns_custom_name() -> None:
    """collection_name property must reflect any string passed to __init__."""
    engine, _ = _build_engine(collection_name="domain_docs_v2")
    assert engine.collection_name == "domain_docs_v2"


# ── index_file ────────────────────────────────────────────────────────────────


def test_index_file_calls_delete_by_source_before_adding() -> None:
    """index_file() must call store.delete_by_source(str(path)) before adding docs.

    This is the AC-005-7 duplicate-prevention requirement.
    """
    settings = _make_settings()
    docs = [_make_document()]
    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id1"]

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = docs
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        path = Path("/some/file.txt")
        engine.index_file(path)

    mock_store.delete_by_source.assert_called_once_with(str(path))


def test_index_file_delete_by_source_called_before_add_documents() -> None:
    """delete_by_source() must be called BEFORE add_documents() (order matters)."""
    settings = _make_settings()
    docs = [_make_document()]
    call_order: list[str] = []

    mock_store = MagicMock()
    mock_store.delete_by_source.side_effect = lambda _: call_order.append("delete")
    mock_store.add_documents.side_effect = lambda _: call_order.append("add") or ["id1"]

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = docs
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        engine.index_file(Path("/file.txt"))

    assert call_order == ["delete", "add"]


def test_index_file_calls_factory_load_with_path() -> None:
    """index_file() must call DocumentProcessorFactory().load(path)."""
    settings = _make_settings()
    docs = [_make_document(), _make_document()]
    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id1", "id2"]

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = docs
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        path = Path("/data/report.txt")
        engine.index_file(path)

    mock_factory_instance.load.assert_called_once_with(path)


def test_index_file_calls_store_add_documents() -> None:
    """index_file() must call store.add_documents() with the loaded documents."""
    settings = _make_settings()
    docs = [_make_document("content A"), _make_document("content B")]
    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id1", "id2"]

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = docs
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        engine.index_file(Path("/data/doc.txt"))

    mock_store.add_documents.assert_called_once_with(docs)


def test_index_file_returns_chunk_count() -> None:
    """index_file() must return the number of documents loaded."""
    settings = _make_settings()
    docs = [_make_document(), _make_document(), _make_document()]
    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id1", "id2", "id3"]

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = docs
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        result = engine.index_file(Path("/data/doc.txt"))

    assert result == 3


def test_index_file_propagates_unsupported_document_type_error() -> None:
    """index_file() must propagate UnsupportedDocumentTypeError for bad extensions."""
    from prismal.rag.loaders import UnsupportedDocumentTypeError

    settings = _make_settings()
    mock_store = MagicMock()

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.side_effect = UnsupportedDocumentTypeError(".xlsx")
        mock_factory_cls.return_value = mock_factory_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        with pytest.raises(UnsupportedDocumentTypeError):
            engine.index_file(Path("/data/spreadsheet.xlsx"))


# ── index_directory ───────────────────────────────────────────────────────────


def test_index_directory_indexes_supported_files_recursively() -> None:
    """index_directory() must call index_file() for each supported file found."""
    settings = _make_settings()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Supported files
        (tmp / "a.txt").write_text("hello")
        (tmp / "b.md").write_text("world")
        sub = tmp / "sub"
        sub.mkdir()
        (sub / "c.pdf").write_text("nested")
        # Unsupported file
        (tmp / "d.xlsx").write_text("skip me")

        mock_store = MagicMock()
        mock_store.add_documents.return_value = ["id1"]
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = [_make_document()]

        with (
            patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
            patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
        ):
            mock_store_cls.return_value = mock_store
            mock_factory_cls.return_value = mock_factory_instance

            from prismal.rag.engine import RAGEngine

            engine = RAGEngine(settings=settings)
            engine.index_directory(tmp)

            # factory.load should have been called for .txt, .md, .pdf (not .xlsx)
            assert mock_factory_instance.load.call_count == 3
            all_calls = mock_factory_instance.load.call_args_list
            loaded_paths = {c.args[0].suffix for c in all_calls}
            assert loaded_paths == {".txt", ".md", ".pdf"}


def test_index_directory_skips_unsupported_files() -> None:
    """index_directory() must not call index_file() for unsupported extensions."""
    settings = _make_settings()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "good.txt").write_text("supported")
        (tmp / "bad.xlsx").write_text("not supported")
        (tmp / "also_bad.zip").write_text("also not supported")

        mock_store = MagicMock()
        mock_store.add_documents.return_value = ["id1"]
        mock_factory_instance = MagicMock()
        mock_factory_instance.load.return_value = [_make_document()]

        with (
            patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
            patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
        ):
            mock_store_cls.return_value = mock_store
            mock_factory_cls.return_value = mock_factory_instance

            from prismal.rag.engine import RAGEngine

            engine = RAGEngine(settings=settings)
            engine.index_directory(tmp)

            # Only the .txt file should be loaded
            assert mock_factory_instance.load.call_count == 1
            loaded_suffix = mock_factory_instance.load.call_args.args[0].suffix
            assert loaded_suffix == ".txt"


def test_index_directory_returns_total_chunk_count() -> None:
    """index_directory() must return the sum of chunks across all indexed files."""
    settings = _make_settings()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "file1.txt").write_text("one")
        (tmp / "file2.md").write_text("two")

        mock_store = MagicMock()
        # First file returns 2 chunks, second returns 3 chunks
        mock_store.add_documents.side_effect = [["id1", "id2"], ["id3", "id4", "id5"]]
        mock_factory_instance = MagicMock()
        # First call returns 2 docs, second returns 3 docs
        mock_factory_instance.load.side_effect = [
            [_make_document(), _make_document()],
            [_make_document(), _make_document(), _make_document()],
        ]

        with (
            patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
            patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
        ):
            mock_store_cls.return_value = mock_store
            mock_factory_cls.return_value = mock_factory_instance

            from prismal.rag.engine import RAGEngine

            engine = RAGEngine(settings=settings)
            total = engine.index_directory(tmp)

    assert total == 5


def test_index_directory_catches_per_file_errors_without_crashing() -> None:
    """index_directory() must not raise even when one file fails to load."""
    settings = _make_settings()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "good.txt").write_text("works fine")
        (tmp / "bad.txt").write_text("will fail")

        mock_store = MagicMock()
        mock_store.add_documents.return_value = ["id1"]
        mock_factory_instance = MagicMock()
        # First call succeeds, second call raises
        mock_factory_instance.load.side_effect = [
            [_make_document()],
            RuntimeError("disk read error"),
        ]

        with (
            patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
            patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
        ):
            mock_store_cls.return_value = mock_store
            mock_factory_cls.return_value = mock_factory_instance

            from prismal.rag.engine import RAGEngine

            engine = RAGEngine(settings=settings)
            # Should not raise even though one file failed
            total = engine.index_directory(tmp)

    # Only the successful file's chunk is counted
    assert total == 1


def test_index_directory_returns_zero_for_empty_directory() -> None:
    """index_directory() must return 0 when the directory has no supported files."""
    settings = _make_settings()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "ignore.zip").write_text("unsupported")

        mock_store = MagicMock()

        with (
            patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
            patch(DOCUMENT_PROCESSOR_FACTORY_PATH) as mock_factory_cls,
        ):
            mock_store_cls.return_value = mock_store
            mock_factory_instance = MagicMock()
            mock_factory_cls.return_value = mock_factory_instance

            from prismal.rag.engine import RAGEngine

            engine = RAGEngine(settings=settings)
            total = engine.index_directory(tmp)

    assert total == 0
    mock_factory_instance.load.assert_not_called()


# ── search ────────────────────────────────────────────────────────────────────


def test_search_calls_store_similarity_search() -> None:
    """search() must call store.similarity_search(query, k=k)."""
    settings = _make_settings()
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        engine.search("my query", k=3)

    mock_store.similarity_search.assert_called_once_with("my query", k=3)


def test_search_uses_default_k_of_5() -> None:
    """search(query) with no k must default to k=5."""
    settings = _make_settings()
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        engine.search("query")

    mock_store.similarity_search.assert_called_once_with("query", k=5)


def test_search_returns_list_of_retrieved_chunks() -> None:
    """search() must return a list of RetrievedChunk objects."""
    settings = _make_settings()
    doc = _make_document("chunk text", "/path/to/doc.txt", "chunk-1")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.85)]

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        results = engine.search("query")

    assert len(results) == 1
    assert isinstance(results[0], RetrievedChunk)


def test_search_converts_document_float_tuples_to_retrieved_chunks() -> None:
    """search() must correctly map (Document, float) tuples to RetrievedChunk fields."""
    settings = _make_settings()
    doc = _make_document("chunk content", "/path/to/source.txt", "chunk-42")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.92)]

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        results = engine.search("test query")

    assert len(results) == 1
    chunk = results[0]
    assert chunk.source == "/path/to/source.txt"
    assert chunk.chunk_id == "chunk-42"
    assert chunk.relevance_score == pytest.approx(0.92)
    assert chunk.content == "chunk content"


def test_search_source_falls_back_to_unknown_when_missing() -> None:
    """search() must use 'unknown' when doc.metadata has no 'source' key."""
    settings = _make_settings()
    doc = MagicMock()
    doc.page_content = "content"
    doc.metadata = {}  # No source key
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.7)]

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        results = engine.search("query")

    assert results[0].source == "unknown"


def test_search_chunk_id_falls_back_to_index_when_missing() -> None:
    """search() must use the document index when metadata has no 'chunk_id'."""
    settings = _make_settings()
    doc = MagicMock()
    doc.page_content = "content"
    doc.metadata = {"source": "/some/file.txt"}  # No chunk_id
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc, 0.7)]

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        results = engine.search("query")

    assert results[0].chunk_id == "0"


def test_search_returns_multiple_chunks_in_order() -> None:
    """search() must return all (Document, float) pairs as RetrievedChunks."""
    settings = _make_settings()
    doc1 = _make_document("first", "/a.txt", "0")
    doc2 = _make_document("second", "/b.txt", "1")
    doc3 = _make_document("third", "/c.txt", "2")
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [(doc1, 0.9), (doc2, 0.8), (doc3, 0.7)]

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        results = engine.search("query", k=3)

    assert len(results) == 3
    assert results[0].content == "first"
    assert results[1].content == "second"
    assert results[2].content == "third"


# ── query ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_creates_crag_pipeline_with_store_and_settings() -> None:
    """query() must create a CRAGPipeline(vector_store=self._store, settings=...) ."""
    settings = _make_settings()
    mock_store = MagicMock()
    mock_crag_result = CRAGResult(answer="the answer", sources=[], used_web_fallback=False)

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(CRAG_PIPELINE_PATH) as mock_pipeline_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock(return_value=mock_crag_result)
        mock_pipeline_cls.return_value = mock_pipeline_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        await engine.query("test question")

    mock_pipeline_cls.assert_called_once_with(vector_store=mock_store, settings=settings)


@pytest.mark.asyncio
async def test_query_calls_pipeline_run_with_query() -> None:
    """query() must call pipeline.run(query) with the user query string."""
    settings = _make_settings()
    mock_store = MagicMock()
    mock_crag_result = CRAGResult(answer="answer", sources=[], used_web_fallback=False)

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(CRAG_PIPELINE_PATH) as mock_pipeline_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock(return_value=mock_crag_result)
        mock_pipeline_cls.return_value = mock_pipeline_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        await engine.query("what is RAG?")

    mock_pipeline_instance.run.assert_called_once_with("what is RAG?")


@pytest.mark.asyncio
async def test_query_returns_crag_result() -> None:
    """query() must return the CRAGResult from the pipeline."""
    settings = _make_settings()
    mock_store = MagicMock()
    expected_result = CRAGResult(answer="specific answer text", sources=[], used_web_fallback=True)

    with (
        patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls,
        patch(CRAG_PIPELINE_PATH) as mock_pipeline_cls,
    ):
        mock_store_cls.return_value = mock_store
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock(return_value=expected_result)
        mock_pipeline_cls.return_value = mock_pipeline_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        result = await engine.query("any query")

    assert result is expected_result


# ── create_collection ─────────────────────────────────────────────────────────


def test_create_collection_returns_new_rag_engine() -> None:
    """create_collection() must return a new RAGEngine instance."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name="old_col", settings=settings)
        new_engine = engine.create_collection("new_col")

    assert new_engine is not engine
    assert isinstance(new_engine, RAGEngine)


def test_create_collection_new_engine_has_correct_collection_name() -> None:
    """create_collection(name) must create an engine with collection_name=name."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name="original", settings=settings)
        new_engine = engine.create_collection("brand_new")

    assert new_engine.collection_name == "brand_new"


def test_create_collection_new_engine_reuses_settings() -> None:
    """create_collection() must pass the current settings to the new RAGEngine."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name="original", settings=settings)
        new_engine = engine.create_collection("another")

    assert new_engine._settings is settings


def test_create_collection_does_not_mutate_original() -> None:
    """create_collection() must not change the original engine's collection name."""
    settings = _make_settings()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(collection_name="original", settings=settings)
        engine.create_collection("new_name")

    assert engine.collection_name == "original"


# ── delete_collection ─────────────────────────────────────────────────────────


def test_delete_collection_calls_store_delete_collection() -> None:
    """delete_collection() must call self._store.delete_collection()."""
    settings = _make_settings()
    mock_store = MagicMock()

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        engine.delete_collection()

    mock_store.delete_collection.assert_called_once_with()


def test_delete_collection_returns_none() -> None:
    """delete_collection() must return None."""
    settings = _make_settings()
    mock_store = MagicMock()
    mock_store.delete_collection.return_value = None

    with patch(CHROMA_VECTOR_STORE_PATH) as mock_store_cls:
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=settings)
        result = engine.delete_collection()

    assert result is None
