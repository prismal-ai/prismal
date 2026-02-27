"""Unit tests for ProviderRegistry."""

from unittest.mock import MagicMock, patch

import pytest

from lightagent.core.config import Settings
from lightagent.providers.registry import ModelInfo, ProviderRegistry, TokenUsage


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
    """Settings with deterministic test values (no real API keys)."""
    return Settings(
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


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_uses_default_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() with no args must use settings.default_model."""
    registry.get_llm()
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-5"


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_uses_provided_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model=...) must use the supplied model string."""
    registry.get_llm(model="gpt-4o")
    assert mock_cls.call_args.kwargs["model"] == "gpt-4o"


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_passes_streaming(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(streaming=True) must set streaming=True on ChatLiteLLM."""
    registry.get_llm(streaming=True)
    assert mock_cls.call_args.kwargs["streaming"] is True


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_uses_settings_temperature(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() with no temperature must use settings.temperature."""
    registry.get_llm()
    assert mock_cls.call_args.kwargs["temperature"] == 0.5


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_overrides_temperature(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(temperature=...) must override settings.temperature."""
    registry.get_llm(temperature=0.0)
    assert mock_cls.call_args.kwargs["temperature"] == 0.0


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_returns_base_chat_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm() must return the ChatLiteLLM instance."""
    from langchain_core.language_models import BaseChatModel
    mock_instance = MagicMock(spec=BaseChatModel)
    mock_cls.return_value = mock_instance
    result = registry.get_llm()
    assert result is mock_instance


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_ollama_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model='ollama/llama3') must route to Ollama via LiteLLM."""
    registry.get_llm(model="ollama/llama3")
    assert mock_cls.call_args.kwargs["model"] == "ollama/llama3"


@patch("lightagent.providers.registry.ChatLiteLLM")
def test_get_llm_gemini_model(mock_cls: MagicMock, registry: ProviderRegistry) -> None:
    """get_llm(model='gemini/gemini-1.5-pro') must route to Google Gemini."""
    registry.get_llm(model="gemini/gemini-1.5-pro")
    assert mock_cls.call_args.kwargs["model"] == "gemini/gemini-1.5-pro"
