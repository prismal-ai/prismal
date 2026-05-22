"""Unit tests for ProviderRegistry."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.fallbacks import RunnableWithFallbacks
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.providers.registry import ModelInfo, ProviderRegistry, TokenUsage


def test_model_info_has_id_and_provider() -> None:
    """ModelInfo must expose id and provider fields."""
    m = ModelInfo(id="gpt-4o", provider="openai")
    assert m.id == "gpt-4o"
    assert m.provider == "openai"


def test_model_info_available_defaults_true() -> None:
    """ModelInfo.available must default to True."""
    m = ModelInfo(id="gpt-4o", provider="openai")
    assert m.available is True


def test_model_info_available_can_be_false() -> None:
    """ModelInfo.available can be set to False."""
    m = ModelInfo(id="gpt-4o", provider="openai", available=False)
    assert m.available is False


def test_token_usage_has_session_id() -> None:
    """TokenUsage must store session_id."""
    u = TokenUsage(session_id="sess-123")
    assert u.session_id == "sess-123"


def test_token_usage_defaults_to_zero() -> None:
    """TokenUsage numeric fields must default to zero."""
    u = TokenUsage(session_id="sess-abc")
    assert u.total_tokens == 0
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.estimated_cost == 0.0


@pytest.fixture
def settings() -> Settings:
    """Settings with deterministic test values (no real API keys).

    Passes ``_env_file=None`` so the developer's local ``.env`` can never
    leak into the test run — otherwise an environment with
    ``PRISMAL_LLM_PROVIDER=ollama`` would trip the provider resolver
    validator when the test fixture pins a non-Ollama model.
    """
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="",
        default_model="claude-sonnet-4-5",
        fallback_model="gpt-4o-mini",
        temperature=0.5,
        max_tokens=1024,
        timeout_seconds=30,
        retry_attempts=2,
    )


@pytest.fixture
def registry(settings: Settings) -> ProviderRegistry:
    """ProviderRegistry bound to test settings."""
    return ProviderRegistry(settings=settings)


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_uses_default_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() with no args must use settings.default_model."""
    registry.get_llm()
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-5"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_uses_provided_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model=...) must use the supplied model string."""
    registry.get_llm(model="gpt-4o")
    assert mock_cls.call_args.kwargs["model"] == "gpt-4o"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_passes_streaming(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(streaming=True) must set streaming=True on ChatLiteLLM."""
    registry.get_llm(streaming=True)
    assert mock_cls.call_args.kwargs["streaming"] is True


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_uses_settings_temperature(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() with no temperature must use settings.temperature."""
    registry.get_llm()
    assert mock_cls.call_args.kwargs["temperature"] == 0.5


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_overrides_temperature(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(temperature=...) must override settings.temperature."""
    registry.get_llm(temperature=0.0)
    assert mock_cls.call_args.kwargs["temperature"] == 0.0


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_returns_base_chat_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() must return the ChatLiteLLM instance."""
    mock_instance = MagicMock(spec=BaseChatModel)
    mock_cls.return_value = mock_instance
    result = registry.get_llm()
    assert result is mock_instance


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_ollama_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model='ollama/llama3') must route to Ollama via LiteLLM."""
    registry.get_llm(model="ollama/llama3")
    assert mock_cls.call_args.kwargs["model"] == "ollama/llama3"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_gemini_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model='gemini/gemini-1.5-pro') must route to Google Gemini."""
    registry.get_llm(model="gemini/gemini-1.5-pro")
    assert mock_cls.call_args.kwargs["model"] == "gemini/gemini-1.5-pro"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_anthropic_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model='claude-sonnet-4-5') must route to Anthropic via LiteLLM."""
    registry.get_llm(model="claude-sonnet-4-5")
    assert mock_cls.call_args.kwargs["model"] == "claude-sonnet-4-5"


def test_get_available_models_with_anthropic_key(settings: Settings) -> None:
    """Anthropic models appear when ANTHROPIC_API_KEY is set."""
    settings.anthropic_api_key = SecretStr("sk-ant-test")
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    ids = [m.id for m in models]
    assert "claude-sonnet-4-5" in ids


def test_get_available_models_with_openai_key(settings: Settings) -> None:
    """OpenAI models appear when OPENAI_API_KEY is set."""
    settings.openai_api_key = SecretStr("sk-openai-test")
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    ids = [m.id for m in models]
    assert "gpt-4o" in ids
    assert "gpt-4o-mini" in ids


def test_get_available_models_with_google_key(settings: Settings) -> None:
    """Google models appear when GOOGLE_API_KEY is set."""
    settings.google_api_key = SecretStr("google-test-key")
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    ids = [m.id for m in models]
    assert "gemini/gemini-1.5-pro" in ids


def test_get_available_models_no_keys_returns_ollama(settings: Settings) -> None:
    """Ollama model always appears regardless of API keys."""
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    providers = [m.provider for m in models]
    assert "ollama" in providers


def test_get_available_models_no_anthropic_key_excludes_claude(
    settings: Settings,
) -> None:
    """Anthropic models must not appear when API key is empty.

    Explicitly zeroes all provider keys after Settings construction to
    override any values loaded from .env or os.environ (both of which
    Pydantic Settings reads at construction time).
    """
    settings.anthropic_api_key = SecretStr("")
    settings.openai_api_key = SecretStr("")
    settings.google_api_key = SecretStr("")
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    ids = [m.id for m in models]
    assert "claude-sonnet-4-5" not in ids


def test_get_available_models_returns_model_info_instances(settings: Settings) -> None:
    """get_available_models() must return ModelInfo objects."""
    reg = ProviderRegistry(settings=settings)
    models = reg.get_available_models()
    assert all(isinstance(m, ModelInfo) for m in models)


def test_get_token_usage_new_session_returns_zeros(registry: ProviderRegistry) -> None:
    """get_token_usage() for unknown session_id must return all-zero TokenUsage."""
    usage = registry.get_token_usage("new-session-id")
    assert usage.session_id == "new-session-id"
    assert usage.total_tokens == 0
    assert usage.estimated_cost == 0.0


def test_track_usage_accumulates_tokens(registry: ProviderRegistry) -> None:
    """track_usage() must accumulate prompt + completion tokens."""
    registry.track_usage("s1", prompt_tokens=100, completion_tokens=50)
    usage = registry.get_token_usage("s1")
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150


def test_track_usage_multiple_calls_accumulate(registry: ProviderRegistry) -> None:
    """Multiple track_usage() calls must sum into the same session."""
    registry.track_usage("s2", prompt_tokens=50, completion_tokens=25)
    registry.track_usage("s2", prompt_tokens=50, completion_tokens=25)
    usage = registry.get_token_usage("s2")
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150


def test_track_usage_accumulates_cost(registry: ProviderRegistry) -> None:
    """track_usage() must accumulate estimated_cost."""
    registry.track_usage("s3", prompt_tokens=100, completion_tokens=50, estimated_cost=0.001)
    registry.track_usage("s3", prompt_tokens=100, completion_tokens=50, estimated_cost=0.002)
    usage = registry.get_token_usage("s3")
    assert abs(usage.estimated_cost - 0.003) < 1e-9


def test_track_usage_sessions_are_independent(registry: ProviderRegistry) -> None:
    """Token usage must be tracked independently per session_id."""
    registry.track_usage("alpha", prompt_tokens=100, completion_tokens=50)
    registry.track_usage("beta", prompt_tokens=200, completion_tokens=100)
    assert registry.get_token_usage("alpha").total_tokens == 150
    assert registry.get_token_usage("beta").total_tokens == 300


def test_get_token_usage_returns_token_usage_instance(
    registry: ProviderRegistry,
) -> None:
    """get_token_usage() must return a TokenUsage instance."""
    usage = registry.get_token_usage("any")
    assert isinstance(usage, TokenUsage)


def test_fallback_model_differs_from_default(registry: ProviderRegistry) -> None:
    """settings.fallback_model must differ from settings.default_model."""
    assert registry._settings.fallback_model != registry._settings.default_model


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_with_fallback_returns_runnable_with_fallbacks(
    mock_cls: MagicMock, registry: ProviderRegistry
) -> None:
    """get_llm_with_fallback() must return a RunnableWithFallbacks."""
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_llm.with_fallbacks.return_value = MagicMock(spec=RunnableWithFallbacks)
    mock_cls.return_value = mock_llm
    result = registry.get_llm_with_fallback()
    assert isinstance(result, RunnableWithFallbacks)


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_with_fallback_uses_fallback_model(
    mock_cls: MagicMock, registry: ProviderRegistry
) -> None:
    """get_llm_with_fallback() must configure the fallback model."""
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_llm.with_fallbacks.return_value = MagicMock(spec=RunnableWithFallbacks)
    mock_cls.return_value = mock_llm
    registry.get_llm_with_fallback()
    # ChatLiteLLM must be called twice: once for primary, once for fallback
    assert mock_cls.call_count == 2
    models_used = [call.kwargs["model"] for call in mock_cls.call_args_list]
    assert "claude-sonnet-4-5" in models_used
    assert "gpt-4o-mini" in models_used


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_with_fallback_skips_wrapper_when_empty(
    mock_cls: MagicMock,
) -> None:
    """Ollama-only setup (fallback_model='') returns the primary directly."""
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_cls.return_value = mock_llm

    ollama_settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="ollama",
        default_model="",
        fallback_model="",
    )
    ollama_registry = ProviderRegistry(settings=ollama_settings)
    result = ollama_registry.get_llm_with_fallback()

    # No fallback wrapper — ChatLiteLLM called exactly once for the primary.
    assert mock_cls.call_count == 1
    assert mock_cls.call_args.kwargs["model"].startswith("ollama_chat/")
    assert result is mock_llm
    mock_llm.with_fallbacks.assert_not_called()


def test_provider_registry_exports_ollama_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProviderRegistry mirrors settings.ollama_base_url into os.environ."""
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

    custom = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ollama_base_url="http://ollama.internal:11434",
    )
    ProviderRegistry(settings=custom)

    import os

    assert os.environ.get("OLLAMA_API_BASE") == "http://ollama.internal:11434"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_uses_ollama_timeout_for_ollama_models(
    mock_cls: MagicMock,
) -> None:
    """Ollama models use the dedicated (longer) ollama timeout."""
    settings_ollama = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="",
        default_model="ollama_chat/llama3.1",
        fallback_model="",
        timeout_seconds=60,
        llm_ollama_timeout_seconds=300,
    )
    registry = ProviderRegistry(settings=settings_ollama)
    registry.get_llm()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["request_timeout"] == 300.0
    assert kwargs["model"] == "ollama_chat/llama3.1"


@patch("prismal.providers.registry.ChatLiteLLM")
def test_get_llm_uses_cloud_timeout_for_non_ollama_models(
    mock_cls: MagicMock,
) -> None:
    """Cloud providers still use the short default timeout."""
    settings_cloud = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="",
        default_model="claude-sonnet-4-5",
        fallback_model="",
        timeout_seconds=60,
        llm_ollama_timeout_seconds=300,
    )
    registry = ProviderRegistry(settings=settings_cloud)
    registry.get_llm()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["request_timeout"] == 60.0


def test_provider_map_default_ollama_is_ollama_chat_prefix() -> None:
    """The auto-resolver maps llm_provider='ollama' to an ollama_chat/*
    model so LiteLLM uses Ollama's native /api/chat tool-calling path
    instead of prompt-based emulation."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="ollama",
        default_model="",
        fallback_model="",
    )
    assert s.default_model.startswith("ollama_chat/")
    assert s.fallback_model == ""
