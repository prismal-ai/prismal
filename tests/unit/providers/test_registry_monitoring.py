"""Tests for ProviderRegistry monitoring instrumentation (T-133)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lightagent.providers.registry import ProviderRegistry


def test_get_llm_increments_otel_counter() -> None:
    """get_llm increments the llm_requests OTEL counter."""
    mock_otel = MagicMock()
    mock_otel.start_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_otel.start_span.return_value.__exit__ = MagicMock(return_value=False)

    mock_langfuse = MagicMock()
    mock_langfuse.get_callback_handler.return_value = None

    mock_llm_instance = MagicMock()

    with (
        patch("lightagent.providers.registry.OTelManager", return_value=mock_otel),
        patch("lightagent.providers.registry.LangfuseManager", return_value=mock_langfuse),
        patch("lightagent.providers.registry.ChatLiteLLM", return_value=mock_llm_instance),
    ):
        registry = ProviderRegistry()
        registry.get_llm("ollama/llama3")

    mock_otel.increment_counter.assert_called()


def test_track_usage_records_otel_tokens() -> None:
    """track_usage records token count in OTEL metrics."""
    mock_otel = MagicMock()

    with patch("lightagent.providers.registry.OTelManager", return_value=mock_otel):
        registry = ProviderRegistry()
        registry.track_usage("session-1", prompt_tokens=100, completion_tokens=50)

    mock_otel.increment_counter.assert_called_with(
        "llm_tokens", 150, attributes={"session_id": "session-1"}
    )


def test_get_llm_injects_langfuse_handler() -> None:
    """get_llm injects the Langfuse callback handler when available."""
    mock_handler = MagicMock()
    mock_langfuse = MagicMock()
    mock_langfuse.get_callback_handler.return_value = mock_handler

    mock_otel = MagicMock()
    mock_otel.start_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_otel.start_span.return_value.__exit__ = MagicMock(return_value=False)

    mock_llm_instance = MagicMock()
    mock_llm_instance.callbacks = None

    with (
        patch("lightagent.providers.registry.OTelManager", return_value=mock_otel),
        patch("lightagent.providers.registry.LangfuseManager", return_value=mock_langfuse),
        patch("lightagent.providers.registry.ChatLiteLLM", return_value=mock_llm_instance),
    ):
        registry = ProviderRegistry()
        llm = registry.get_llm("ollama/llama3")

    # The handler should have been injected into callbacks
    assert mock_handler in (llm.callbacks or [])
