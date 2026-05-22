"""Unit tests for EmbeddingsFactory (lightagent.rag.embeddings).

Tests follow the TDD spec from T-061.  All three embedding class constructors
are mocked so no API calls or model downloads are triggered.

Coverage targets:
- OpenAI provider selection and constructor arguments
- HuggingFace provider selection and constructor arguments
- Ollama provider selection and constructor arguments
- Fallback to get_settings() when settings=None
- Direct Settings override (settings not None)
- Unknown embeddings_model raises ValueError
- Logger is called with INFO-level provider information
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prismal.rag.embeddings import EmbeddingsFactory

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(model: str) -> MagicMock:
    """Return a mock Settings object with embeddings_model set to *model*."""
    settings = MagicMock()
    settings.embeddings_model = model
    return settings


# ── OpenAI provider ───────────────────────────────────────────────────────────


def test_create_openai_returns_openai_embeddings_instance() -> None:
    """create() with embeddings_model='openai' must return OpenAIEmbeddings."""
    settings = _make_settings("openai")

    with patch("lightagent.rag.embeddings.OpenAIEmbeddings", autospec=True) as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        result = EmbeddingsFactory.create(settings=settings)

    assert result is mock_instance


def test_create_openai_uses_text_embedding_3_small() -> None:
    """create() with 'openai' must pass model='text-embedding-3-small'."""
    settings = _make_settings("openai")

    with patch("lightagent.rag.embeddings.OpenAIEmbeddings", autospec=True) as mock_cls:
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_cls.assert_called_once_with(model="text-embedding-3-small")


# ── HuggingFace provider ──────────────────────────────────────────────────────


def test_create_huggingface_returns_huggingface_embeddings_instance() -> None:
    """create() with 'huggingface' must return a HuggingFaceEmbeddings instance."""
    settings = _make_settings("huggingface")

    with patch("lightagent.rag.embeddings.HuggingFaceEmbeddings", autospec=True) as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        result = EmbeddingsFactory.create(settings=settings)

    assert result is mock_instance


def test_create_huggingface_uses_all_minilm_l6_v2() -> None:
    """create() with 'huggingface' must pass model_name='all-MiniLM-L6-v2'."""
    settings = _make_settings("huggingface")

    with patch("lightagent.rag.embeddings.HuggingFaceEmbeddings", autospec=True) as mock_cls:
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_cls.assert_called_once_with(model_name="all-MiniLM-L6-v2")


# ── Ollama provider ───────────────────────────────────────────────────────────


def test_create_ollama_returns_ollama_embeddings_instance() -> None:
    """create() with 'ollama' must return an OllamaEmbeddings instance."""
    settings = _make_settings("ollama")

    with patch("lightagent.rag.embeddings.OllamaEmbeddings", autospec=True) as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        result = EmbeddingsFactory.create(settings=settings)

    assert result is mock_instance


def test_create_ollama_uses_nomic_embed_text() -> None:
    """create() with 'ollama' must pass model='nomic-embed-text' to constructor."""
    settings = _make_settings("ollama")

    with patch("lightagent.rag.embeddings.OllamaEmbeddings", autospec=True) as mock_cls:
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_cls.assert_called_once_with(model="nomic-embed-text")


# ── get_settings() fallback ───────────────────────────────────────────────────


def test_create_calls_get_settings_when_settings_is_none() -> None:
    """create(settings=None) must call get_settings() to obtain configuration."""
    fake_settings = _make_settings("huggingface")

    with (
        patch(
            "lightagent.rag.embeddings.get_settings", return_value=fake_settings
        ) as mock_get_settings,
        patch("lightagent.rag.embeddings.HuggingFaceEmbeddings", autospec=True) as mock_cls,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=None)

    mock_get_settings.assert_called_once()


def test_create_does_not_call_get_settings_when_settings_provided() -> None:
    """create(settings=<object>) must NOT call get_settings()."""
    settings = _make_settings("ollama")

    with (
        patch("lightagent.rag.embeddings.get_settings") as mock_get_settings,
        patch("lightagent.rag.embeddings.OllamaEmbeddings", autospec=True) as mock_cls,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_get_settings.assert_not_called()


# ── Unknown model → ValueError ────────────────────────────────────────────────


def test_create_raises_value_error_for_unknown_model() -> None:
    """create() with an unrecognised embeddings_model must raise ValueError."""
    settings = _make_settings("unknown-provider")

    with pytest.raises(ValueError, match="unknown-provider"):
        EmbeddingsFactory.create(settings=settings)


def test_create_raises_value_error_for_empty_string_model() -> None:
    """create() with an empty-string embeddings_model must raise ValueError."""
    settings = _make_settings("")

    with pytest.raises(ValueError, match="Unknown embeddings_model"):
        EmbeddingsFactory.create(settings=settings)


# ── Logging ───────────────────────────────────────────────────────────────────


def test_create_logs_selected_provider() -> None:
    """create() must call logger.info() once with the selected provider name."""
    settings = _make_settings("openai")

    with (
        patch("lightagent.rag.embeddings.OpenAIEmbeddings", autospec=True) as mock_cls,
        patch("lightagent.rag.embeddings.logger") as mock_logger,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_logger.info.assert_called_once_with("embeddings_provider_selected", provider="openai")


def test_create_logs_huggingface_provider() -> None:
    """create() must log provider='huggingface' when that provider is selected."""
    settings = _make_settings("huggingface")

    with (
        patch("lightagent.rag.embeddings.HuggingFaceEmbeddings", autospec=True) as mock_cls,
        patch("lightagent.rag.embeddings.logger") as mock_logger,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_logger.info.assert_called_once_with("embeddings_provider_selected", provider="huggingface")


def test_create_logs_ollama_provider() -> None:
    """create() must log provider='ollama' when that provider is selected."""
    settings = _make_settings("ollama")

    with (
        patch("lightagent.rag.embeddings.OllamaEmbeddings", autospec=True) as mock_cls,
        patch("lightagent.rag.embeddings.logger") as mock_logger,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create(settings=settings)

    mock_logger.info.assert_called_once_with("embeddings_provider_selected", provider="ollama")


# ── Settings object accepted directly ─────────────────────────────────────────


def test_create_accepts_settings_object_directly() -> None:
    """create(settings=<Settings>) uses it directly; get_settings() not called."""
    from prismal.core.config import Settings

    # Build a real Settings object with embeddings_model='huggingface'
    real_settings = Settings(embeddings_model="huggingface")

    with (
        patch("lightagent.rag.embeddings.get_settings") as mock_get_settings,
        patch("lightagent.rag.embeddings.HuggingFaceEmbeddings", autospec=True) as mock_cls,
    ):
        mock_cls.return_value = MagicMock()

        result = EmbeddingsFactory.create(settings=real_settings)

    mock_get_settings.assert_not_called()
    assert result is mock_cls.return_value


def test_create_default_argument_is_none() -> None:
    """create() with no arguments defaults to settings=None and calls get_settings()."""
    fake_settings = _make_settings("ollama")

    with (
        patch(
            "lightagent.rag.embeddings.get_settings", return_value=fake_settings
        ) as mock_get_settings,
        patch("lightagent.rag.embeddings.OllamaEmbeddings", autospec=True) as mock_cls,
    ):
        mock_cls.return_value = MagicMock()

        EmbeddingsFactory.create()

    mock_get_settings.assert_called_once()
