"""Embeddings factory for the LightAgent RAG system.

Provides ``EmbeddingsFactory``, which maps the ``settings.embeddings_model``
configuration value to the appropriate LangChain embeddings implementation.

Supported providers:

- ``"openai"``      → :class:`~langchain_openai.OpenAIEmbeddings`
  (model: ``text-embedding-3-small``)
- ``"huggingface"`` → :class:`~langchain_community.embeddings.HuggingFaceEmbeddings`
  (model: ``all-MiniLM-L6-v2``)
- ``"ollama"``      → :class:`~langchain_community.embeddings.OllamaEmbeddings`
  (model: ``nomic-embed-text``)

Example::

    from prismal.rag.embeddings import EmbeddingsFactory

    embeddings = EmbeddingsFactory.create()
    vectors = embeddings.embed_documents(["Hello, world!"])
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_community.embeddings import HuggingFaceEmbeddings, OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from prismal.core.config import get_settings
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from prismal.core.config import Settings

logger = get_logger("lightagent.rag.embeddings")


class EmbeddingsFactory:
    """Factory that creates the correct LangChain embeddings based on app settings.

    All construction is delegated to the relevant LangChain class; this factory
    solely handles provider selection and logs the chosen provider at INFO level.

    Supported values for ``settings.embeddings_model``:
    ``"openai"``, ``"huggingface"``, ``"ollama"``.
    """

    @staticmethod
    def create(settings: Settings | None = None) -> Embeddings:
        """Instantiate and return the configured embeddings implementation.

        If *settings* is ``None``, the global application settings are loaded
        via :func:`~lightagent.core.config.get_settings`.

        Args:
            settings: Optional :class:`~lightagent.core.config.Settings` instance
                to use.  When ``None``, ``get_settings()`` is called automatically.

        Returns:
            A :class:`~langchain_core.embeddings.Embeddings` instance ready for
            use in embedding and retrieval operations.

        Raises:
            ValueError: If ``settings.embeddings_model`` is not one of the
                recognised provider keys.  Under normal operation this cannot
                occur because the field uses a ``Literal`` type, but defensive
                validation is applied nonetheless.
        """
        resolved: Settings = settings if settings is not None else get_settings()
        provider: str = resolved.embeddings_model

        if provider == "openai":
            logger.info("embeddings_provider_selected", provider="openai")
            return OpenAIEmbeddings(model="text-embedding-3-small")

        if provider == "huggingface":
            logger.info("embeddings_provider_selected", provider="huggingface")
            return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        if provider == "ollama":
            logger.info("embeddings_provider_selected", provider="ollama")
            return OllamaEmbeddings(model="nomic-embed-text")

        msg = (
            f"Unknown embeddings_model: {provider!r}. "
            "Valid options are: 'openai', 'huggingface', 'ollama'."
        )
        raise ValueError(msg)


__all__ = ["EmbeddingsFactory"]
