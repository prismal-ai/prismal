"""Vision LLM provider wrapper (Fase F, SPEC-MM-PROV-003).

Thin formalisation over :class:`ProviderRegistry` returning a vision-capable
``BaseChatModel`` (Anthropic / OpenAI / Gemini via LiteLLM). Models accept
``HumanMessage`` content blocks of ``{"type": "image_url", ...}`` plus text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from prismal.core.config import Settings


def get_vision_llm(
    model: str | None = None,
    *,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a vision-capable chat model.

    Resolution order: explicit ``model`` → ``settings.vision_model`` →
    ``settings.cua_vision_model`` → ``settings.default_model``.

    Args:
        model: LiteLLM model string override.
        settings: Injectable settings.

    Returns:
        A ``BaseChatModel`` that accepts image content blocks.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    resolved = (
        model
        or settings.vision_model
        or settings.cua_vision_model
        or settings.default_model
    )
    return ProviderRegistry(settings).get_llm(resolved)


__all__ = ["get_vision_llm"]
