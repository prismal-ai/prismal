"""Unit tests for ChromaVectorStore (lightagent.rag.vector_store).

Tests follow the TDD spec from T-062.  Both ``Chroma`` and ``EmbeddingsFactory``
are mocked so no real ChromaDB instances or embedding models are created.

Coverage targets:
- __init__ calls EmbeddingsFactory.create() with the provided settings
- __init__ creates Chroma with correct collection_name, embedding_function,
  persist_directory
- add_documents() delegates to self._chroma.add_documents() and returns IDs
- add_documents() logs how many documents were added
- similarity_search() delegates to self._chroma.similarity_search_with_score()
  and returns the result
- similarity_search() logs the query and k value
- delete_collection() delegates to self._chroma.delete_collection()
- collection_name property returns the collection name from __init__
- ChromaStoreError is defined and subclasses LightAgentError
- Default collection name is "default"
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightagent.core.exceptions import LightAgentError
from lightagent.rag.vector_store import ChromaStoreError, ChromaVectorStore

# ── Constants ─────────────────────────────────────────────────────────────────

CHROMA_PATH = "lightagent.rag.vector_store.Chroma"
EMBEDDINGS_FACTORY_PATH = "lightagent.rag.vector_store.EmbeddingsFactory"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(chroma_path: str = "data/db/chroma") -> MagicMock:
    """Return a minimal mock Settings object."""
    settings = MagicMock()
    settings.chroma_path = chroma_path
    return settings


def _build_store(
    collection_name: str = "test_col",
    settings: MagicMock | None = None,
) -> tuple[ChromaVectorStore, MagicMock, MagicMock]:
    """Construct a ChromaVectorStore under full mock context.

    Returns:
        (store_instance, mock_chroma_instance, mock_embeddings_instance)
    """
    if settings is None:
        settings = _make_settings()

    mock_embeddings: MagicMock = MagicMock()
    mock_chroma_instance: MagicMock = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = mock_embeddings
        mock_chroma_cls.return_value = mock_chroma_instance
        store = ChromaVectorStore(collection_name=collection_name, settings=settings)

    return store, mock_chroma_instance, mock_embeddings


# ── ChromaStoreError ──────────────────────────────────────────────────────────


def test_chroma_store_error_is_defined() -> None:
    """ChromaStoreError must be importable from lightagent.rag.vector_store."""
    assert ChromaStoreError is not None


def test_chroma_store_error_subclasses_light_agent_error() -> None:
    """ChromaStoreError must subclass LightAgentError."""
    assert issubclass(ChromaStoreError, LightAgentError)


def test_chroma_store_error_is_exception() -> None:
    """ChromaStoreError must be raiseable as an Exception."""
    with pytest.raises(ChromaStoreError):
        raise ChromaStoreError("something went wrong")


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_init_calls_embeddings_factory_create_with_settings() -> None:
    """__init__ must call EmbeddingsFactory.create(settings=<provided settings>)."""
    settings = _make_settings()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = MagicMock()

        ChromaVectorStore(collection_name="col", settings=settings)

    mock_factory.create.assert_called_once_with(settings=settings)


def test_init_creates_chroma_with_correct_collection_name() -> None:
    """__init__ must pass collection_name to the Chroma constructor."""
    settings = _make_settings()
    mock_embeddings = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = mock_embeddings
        mock_chroma_cls.return_value = MagicMock()

        ChromaVectorStore(collection_name="my_collection", settings=settings)

    call_kwargs = mock_chroma_cls.call_args.kwargs
    assert call_kwargs["collection_name"] == "my_collection"


def test_init_creates_chroma_with_correct_embedding_function() -> None:
    """__init__ must pass embedding_function=<embeddings> to Chroma."""
    settings = _make_settings()
    mock_embeddings = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = mock_embeddings
        mock_chroma_cls.return_value = MagicMock()

        ChromaVectorStore(collection_name="col", settings=settings)

    call_kwargs = mock_chroma_cls.call_args.kwargs
    assert call_kwargs["embedding_function"] is mock_embeddings


def test_init_creates_chroma_with_correct_persist_directory() -> None:
    """__init__ must pass persist_directory=settings.chroma_path to Chroma."""
    settings = _make_settings(chroma_path="/custom/chroma/path")
    mock_embeddings = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = mock_embeddings
        mock_chroma_cls.return_value = MagicMock()

        ChromaVectorStore(collection_name="col", settings=settings)

    call_kwargs = mock_chroma_cls.call_args.kwargs
    assert call_kwargs["persist_directory"] == "/custom/chroma/path"


def test_init_default_collection_name_is_default() -> None:
    """ChromaVectorStore() with no collection_name arg must use 'default'."""
    settings = _make_settings()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = MagicMock()

        store = ChromaVectorStore(settings=settings)

    assert store.collection_name == "default"


def test_init_calls_get_settings_when_settings_is_none() -> None:
    """__init__(settings=None) must call get_settings() to resolve config."""
    fake_settings = _make_settings()

    with (
        patch("lightagent.rag.vector_store.get_settings", return_value=fake_settings) as mock_gs,
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = MagicMock()

        ChromaVectorStore(settings=None)

    mock_gs.assert_called_once()


# ── collection_name property ──────────────────────────────────────────────────


def test_collection_name_property_returns_constructor_value() -> None:
    """collection_name property must return the name passed to __init__."""
    store, _, _ = _build_store(collection_name="my_col")
    assert store.collection_name == "my_col"


def test_collection_name_property_returns_custom_name() -> None:
    """collection_name property must reflect any string passed to __init__."""
    store, _, _ = _build_store(collection_name="documents_v2")
    assert store.collection_name == "documents_v2"


# ── add_documents ─────────────────────────────────────────────────────────────


def test_add_documents_delegates_to_chroma() -> None:
    """add_documents() must call self._chroma.add_documents(documents)."""
    settings = _make_settings()
    docs = [MagicMock(), MagicMock()]
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.add_documents.return_value = ["id1", "id2"]

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.add_documents(docs)

    mock_chroma_instance.add_documents.assert_called_once_with(docs)
    assert result == ["id1", "id2"]


def test_add_documents_returns_ids_from_chroma() -> None:
    """add_documents() must return exactly what self._chroma.add_documents returns."""
    settings = _make_settings()
    docs = [MagicMock()]
    expected_ids = ["abc-123", "def-456", "ghi-789"]
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.add_documents.return_value = expected_ids

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.add_documents(docs)

    assert result == expected_ids


def test_add_documents_logs_document_count() -> None:
    """add_documents() must log at INFO level with the number of documents added."""
    settings = _make_settings()
    docs = [MagicMock(), MagicMock(), MagicMock()]
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.add_documents.return_value = ["a", "b", "c"]

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
        patch("lightagent.rag.vector_store.logger") as mock_logger,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        store.add_documents(docs)

    mock_logger.info.assert_called_once()
    call_kwargs = mock_logger.info.call_args
    # The count of documents (3) must appear somewhere in the log call
    all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
    assert any(arg == 3 or "3" in str(arg) for arg in all_args)


# ── similarity_search ─────────────────────────────────────────────────────────


def test_similarity_search_delegates_to_chroma_with_score() -> None:
    """similarity_search() must call similarity_search_with_score(query, k)."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    expected_results = [(MagicMock(), 0.9), (MagicMock(), 0.7)]
    mock_chroma_instance.similarity_search_with_score.return_value = expected_results

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.similarity_search("test query", k=2)

    mock_chroma_instance.similarity_search_with_score.assert_called_once_with("test query", k=2)
    assert result == expected_results


def test_similarity_search_returns_results_from_chroma() -> None:
    """similarity_search() must return the list of (Document, float) from Chroma."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    doc1, doc2 = MagicMock(), MagicMock()
    expected = [(doc1, 0.95), (doc2, 0.82)]
    mock_chroma_instance.similarity_search_with_score.return_value = expected

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.similarity_search("query", k=5)

    assert result is expected


def test_similarity_search_uses_default_k_of_5() -> None:
    """similarity_search(query) with no k must default to k=5."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.similarity_search_with_score.return_value = []

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        store.similarity_search("query")

    mock_chroma_instance.similarity_search_with_score.assert_called_once_with("query", k=5)


def test_similarity_search_logs_query_and_k() -> None:
    """similarity_search() must log at INFO level including the query and k."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.similarity_search_with_score.return_value = []

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
        patch("lightagent.rag.vector_store.logger") as mock_logger,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        store.similarity_search("my search query", k=3)

    mock_logger.info.assert_called_once()
    call_kwargs = mock_logger.info.call_args
    all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
    assert any("my search query" in str(arg) for arg in all_args)
    assert any(arg == 3 or "3" in str(arg) for arg in all_args)


# ── delete_collection ─────────────────────────────────────────────────────────


def test_delete_collection_delegates_to_chroma() -> None:
    """delete_collection() must call self._chroma.delete_collection()."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        store.delete_collection()

    mock_chroma_instance.delete_collection.assert_called_once_with()


def test_delete_collection_returns_none() -> None:
    """delete_collection() must return None."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.delete_collection.return_value = None

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.delete_collection()

    assert result is None


# ── delete_by_source ──────────────────────────────────────────────────────────


def test_delete_by_source_calls_chroma_delete_with_where_filter() -> None:
    """delete_by_source(source) must call self._chroma.delete(where={"source": source})."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        store.delete_by_source("/path/to/file.txt")

    mock_chroma_instance.delete.assert_called_once_with(where={"source": "/path/to/file.txt"})


def test_delete_by_source_returns_none() -> None:
    """delete_by_source() must return None."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        result = store.delete_by_source("/some/path.md")

    assert result is None


def test_delete_by_source_swallows_exception_when_collection_empty() -> None:
    """delete_by_source() must not raise if the underlying Chroma.delete raises."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.delete.side_effect = Exception("no documents match")

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        # Must not raise
        store.delete_by_source("/nonexistent/file.txt")


# ── Exception wrapping ────────────────────────────────────────────────────────


def test_add_documents_raises_chroma_store_error_on_failure() -> None:
    """add_documents() must wrap any underlying exception in ChromaStoreError."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.add_documents.side_effect = Exception("chroma is down")

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        with pytest.raises(ChromaStoreError, match="chroma is down"):
            store.add_documents([MagicMock()])


def test_similarity_search_raises_chroma_store_error_on_failure() -> None:
    """similarity_search() must wrap any underlying exception in ChromaStoreError."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.similarity_search_with_score.side_effect = Exception("index corrupt")

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        with pytest.raises(ChromaStoreError, match="index corrupt"):
            store.similarity_search("query")


def test_delete_collection_raises_chroma_store_error_on_failure() -> None:
    """delete_collection() must wrap any underlying exception in ChromaStoreError."""
    settings = _make_settings()
    mock_chroma_instance = MagicMock()
    mock_chroma_instance.delete_collection.side_effect = Exception("collection missing")

    with (
        patch(EMBEDDINGS_FACTORY_PATH) as mock_factory,
        patch(CHROMA_PATH) as mock_chroma_cls,
    ):
        mock_factory.create.return_value = MagicMock()
        mock_chroma_cls.return_value = mock_chroma_instance

        store = ChromaVectorStore(collection_name="col", settings=settings)
        with pytest.raises(ChromaStoreError, match="collection missing"):
            store.delete_collection()
