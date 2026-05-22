"""Tests for RAG pipeline monitoring instrumentation (T-136)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_otel_manager_is_available_for_rag() -> None:
    """OTelManager can be instantiated for RAG instrumentation."""
    from prismal.monitoring.otel import OTelManager, _NoOpSpan

    OTelManager._instance = None
    OTelManager._initialized = False
    with patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        otel = OTelManager()
    with otel.start_span("rag.query") as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("prismal.query_len", 42)
        span.set_attribute("prismal.num_results", 5)


def test_rag_query_span_attributes() -> None:
    """RAG spans support standard prismal attributes."""
    from prismal.monitoring.otel import _NoOpSpan

    span = _NoOpSpan()
    span.set_attribute("prismal.query_len", 25)
    span.set_attribute("prismal.num_docs", 3)
    span.set_attribute("prismal.latency_ms", 142.5)
    # No exceptions = pass


@pytest.mark.asyncio
async def test_rag_engine_imports_correctly() -> None:
    """RAG engine module imports without error."""
    try:
        import prismal.rag.engine as _  # noqa: F401
    except ImportError:
        pytest.skip("RAG engine dependencies not available")


def test_rag_engine_search_creates_otel_span() -> None:
    """RAGEngine.search() creates an OTEL span with query_len and num_results."""
    mock_otel = MagicMock()
    mock_span = MagicMock()
    mock_otel.start_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_otel.start_span.return_value.__exit__ = MagicMock(return_value=False)

    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    with (
        patch("prismal.rag.engine.ChromaVectorStore") as mock_store_cls,
        patch("prismal.rag.engine.OTelManager", return_value=mock_otel),
    ):
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=MagicMock())
        engine.search("test query", k=3)

    mock_otel.start_span.assert_called_with("rag.search")


def test_rag_engine_search_increments_rag_queries_counter() -> None:
    """RAGEngine.search() increments the rag_queries OTEL counter."""
    mock_otel = MagicMock()
    mock_span = MagicMock()
    mock_otel.start_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_otel.start_span.return_value.__exit__ = MagicMock(return_value=False)

    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    with (
        patch("prismal.rag.engine.ChromaVectorStore") as mock_store_cls,
        patch("prismal.rag.engine.OTelManager", return_value=mock_otel),
    ):
        mock_store_cls.return_value = mock_store
        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=MagicMock())
        engine.search("some query")

    mock_otel.increment_counter.assert_called_with("rag_queries")


@pytest.mark.asyncio
async def test_rag_engine_query_creates_otel_span() -> None:
    """RAGEngine.query() creates an OTEL span for the full CRAG pipeline."""
    from prismal.rag.crag import CRAGResult

    mock_otel = MagicMock()
    mock_span = MagicMock()
    mock_otel.start_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_otel.start_span.return_value.__exit__ = MagicMock(return_value=False)

    mock_store = MagicMock()
    mock_crag_result = CRAGResult(answer="answer", sources=[], used_web_fallback=False)
    mock_pipeline_instance = MagicMock()
    mock_pipeline_instance.run = AsyncMock(return_value=mock_crag_result)

    with (
        patch("prismal.rag.engine.ChromaVectorStore") as mock_store_cls,
        patch("prismal.rag.engine.CRAGPipeline") as mock_pipeline_cls,
        patch("prismal.rag.engine.OTelManager", return_value=mock_otel),
    ):
        mock_store_cls.return_value = mock_store
        mock_pipeline_cls.return_value = mock_pipeline_instance

        from prismal.rag.engine import RAGEngine

        engine = RAGEngine(settings=MagicMock())
        result = await engine.query("what is RAG?")

    mock_otel.start_span.assert_called_with("rag.query")
    assert result is mock_crag_result


def test_crag_pipeline_imports_otel_manager() -> None:
    """crag module imports OTelManager from prismal.monitoring.otel."""
    import prismal.rag.crag as crag_module

    assert hasattr(crag_module, "OTelManager")


def test_rag_engine_imports_otel_manager() -> None:
    """engine module imports OTelManager from prismal.monitoring.otel."""
    import prismal.rag.engine as engine_module

    assert hasattr(engine_module, "OTelManager")
