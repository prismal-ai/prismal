"""Unit tests for ProviderRegistry."""

from lightagent.providers.registry import ModelInfo, TokenUsage


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
