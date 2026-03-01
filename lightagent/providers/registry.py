"""
Provider registry — LiteLLM-backed LLM factory with usage tracking.

Provides provider-agnostic LLM access via ChatLiteLLM. All provider routing is handled
by LiteLLM; no provider-specific imports here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_community.chat_models import ChatLiteLLM

from lightagent.core.config import Settings, get_settings
from lightagent.core.logging import get_logger
from lightagent.monitoring.langfuse_client import LangfuseManager
from lightagent.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel, LanguageModelInput
    from langchain_core.messages import BaseMessage
    from langchain_core.runnables.fallbacks import RunnableWithFallbacks

logger = get_logger("lightagent.providers.registry")


@dataclass
class ModelInfo:
    """Metadata for a configured LLM model."""

    id: str
    provider: str
    available: bool = True


@dataclass
class TokenUsage:
    """Cumulative token usage and cost for a session."""

    session_id: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0


class ProviderRegistry:
    """
    LiteLLM-backed registry for provider-agnostic LLM access.

    All provider routing (Anthropic, OpenAI, Google, Ollama, etc.) is
    handled transparently by LiteLLM. No provider-specific code lives here.

    Usage::

        registry = ProviderRegistry()
        llm = registry.get_llm()  # uses settings.default_model
        llm = registry.get_llm("gpt-4o")  # override for this call
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize the registry.

        Args:
            settings: Optional Settings override (useful for testing).
                      None = load from environment via get_settings().
        """
        self._settings = settings or get_settings()
        self._usage: dict[str, TokenUsage] = {}

    def get_llm(
        self,
        model: str | None = None,
        streaming: bool = False,
        temperature: float | None = None,
    ) -> BaseChatModel:
        """
        Return a LangChain-compatible LLM for the specified model.

        Falls back to ``settings.default_model`` when *model* is None.
        Provider routing is transparent: pass ``"gpt-4o"``, ``"claude-sonnet-4-5"``,
        ``"gemini/gemini-1.5-pro"``, or ``"ollama/llama3"`` and LiteLLM will
        route to the correct provider.

        Args:
            model: LiteLLM model string. None = use ``settings.default_model``.
            streaming: Whether to enable streaming output.
            temperature: Sampling temperature. None = use ``settings.temperature``.

        Returns:
            A ``ChatLiteLLM`` instance (implements ``BaseChatModel``).
        """
        resolved_model = model if model is not None else self._settings.default_model
        temp = temperature if temperature is not None else self._settings.temperature

        logger.debug(
            "creating_llm",
            model=resolved_model,
            streaming=streaming,
            temperature=temp,
        )

        langfuse = LangfuseManager()
        otel = OTelManager()

        with otel.start_span(
            "provider.get_llm",
            attributes={
                "lightagent.model": resolved_model,
                "lightagent.streaming": streaming,
                "lightagent.temperature": temp,
            },
        ):
            otel.increment_counter("llm_requests", attributes={"model": resolved_model})
            llm = ChatLiteLLM(
                model=resolved_model,
                streaming=streaming,
                temperature=temp,
                max_tokens=self._settings.max_tokens,
                request_timeout=float(self._settings.timeout_seconds),
                max_retries=self._settings.retry_attempts,
            )
            # Inject Langfuse callback handler if available
            handler = langfuse.get_callback_handler()
            if handler is not None:
                llm.callbacks = [handler]
            return llm

    def get_llm_with_fallback(
        self,
        model: str | None = None,
        streaming: bool = False,
        temperature: float | None = None,
    ) -> RunnableWithFallbacks[LanguageModelInput, BaseMessage]:
        """
        Return a primary LLM wrapped with automatic fallback.

        If the primary model raises any exception (network, quota, timeout),
        LangChain will automatically retry with ``settings.fallback_model``.

        Args:
            model: Primary model string. None = ``settings.default_model``.
            streaming: Whether to enable streaming output.
            temperature: Sampling temperature. None = ``settings.temperature``.

        Returns:
            A ``RunnableWithFallbacks`` wrapping primary and fallback models.
        """
        primary = self.get_llm(
            model=model, streaming=streaming, temperature=temperature
        )
        fallback = self.get_llm(
            model=self._settings.fallback_model,
            streaming=streaming,
            temperature=temperature,
        )
        return primary.with_fallbacks([fallback])

    def get_available_models(self) -> list[ModelInfo]:
        """
        Return list of models available given the configured API keys.

        A model is included only when its required API key is non-empty
        (or for Ollama, which needs no key). Does not verify liveness.

        Returns:
            List of ``ModelInfo`` for all configured providers.
        """
        models: list[ModelInfo] = []
        s = self._settings

        if s.anthropic_api_key.get_secret_value():
            models.extend(
                [
                    ModelInfo(id="claude-sonnet-4-5", provider="anthropic"),
                    ModelInfo(id="claude-opus-4-6", provider="anthropic"),
                    ModelInfo(id="claude-haiku-4-5-20251001", provider="anthropic"),
                ]
            )

        if s.openai_api_key.get_secret_value():
            models.extend(
                [
                    ModelInfo(id="gpt-4o", provider="openai"),
                    ModelInfo(id="gpt-4o-mini", provider="openai"),
                ]
            )

        if s.google_api_key.get_secret_value():
            models.extend([
                # LiteLLM requires the 'gemini/' provider prefix for routing
                ModelInfo(id="gemini/gemini-1.5-pro", provider="google"),
                ModelInfo(id="gemini/gemini-2.0-flash", provider="google"),
            ])

        # Ollama requires no key — always listed as potentially available
        models.append(ModelInfo(id="ollama/llama3", provider="ollama"))

        return models

    def get_token_usage(self, session_id: str) -> TokenUsage:
        """
        Return cumulative token usage for a session.

        If no usage has been tracked yet for *session_id*, returns a
        ``TokenUsage`` with all fields at zero.

        Args:
            session_id: Unique session identifier.

        Returns:
            ``TokenUsage`` with accumulated totals for the session.
        """
        return self._usage.get(session_id, TokenUsage(session_id=session_id))

    def track_usage(
        self,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float = 0.0,
    ) -> None:
        """
        Record token usage for a session.

        Accumulates usage across multiple calls for the same session_id.
        Thread-safety is not guaranteed — use one registry per async context.

        Args:
            session_id: Unique session identifier.
            prompt_tokens: Number of input/prompt tokens used.
            completion_tokens: Number of output/completion tokens generated.
            estimated_cost: Estimated USD cost for this call.
        """
        if session_id not in self._usage:
            self._usage[session_id] = TokenUsage(session_id=session_id)
        usage = self._usage[session_id]
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.total_tokens += prompt_tokens + completion_tokens
        usage.estimated_cost += estimated_cost
        otel = OTelManager()
        total = prompt_tokens + completion_tokens
        otel.increment_counter("llm_tokens", total, attributes={"session_id": session_id})
        logger.debug(
            "usage_tracked",
            session_id=session_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total=usage.total_tokens,
        )


__all__ = ["ModelInfo", "ProviderRegistry", "TokenUsage"]
