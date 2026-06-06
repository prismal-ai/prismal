"""Natively-multimodal LLM provider wrapper (Fase F, SPEC-MM-PROV-004).

Returns a ``BaseChatModel`` that accepts audio + image + video + text in the
same message when the underlying model supports it (Gemini 2.x, GPT-4o,
Sonnet 4.6). Provider routing is handled by :class:`ProviderRegistry`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from prismal.core.config import Settings


def get_multimodal_llm(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a natively-multimodal chat model.

    Defaults to ``settings.multimodal_model`` (``gemini/gemini-2.0-flash``).

    Args:
        model: LiteLLM model string override.
        settings: Injectable settings.

    Returns:
        A ``BaseChatModel`` supporting multiple input modalities.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    resolved = model or settings.multimodal_model or settings.default_model
    return ProviderRegistry(settings).get_llm(resolved)


__all__ = ["get_multimodal_llm"]
