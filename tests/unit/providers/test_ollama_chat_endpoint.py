"""``ollama/`` vs ``ollama_chat/`` (TKE9-04 follow-up).

With ``ollama/<m>`` LiteLLM calls ``/api/generate`` and emulates tool calls as
text; in the TKE9-04 pilot every local model then failed a 3-call tool loop
(the call printed as text, or looped). ``ollama_chat/<m>`` uses Ollama's native
tool calling. ``ollama_chat_endpoint`` (opt-in) rewrites the prefix; with it off
the model string is unchanged and a one-time warning names the fix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from prismal.core.config import Settings
from prismal.providers import registry as registry_mod
from prismal.providers.registry import ProviderRegistry


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "llm_provider": "",
        "default_model": "ollama/qwen3:14b",
        "fallback_model": "",
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_warned() -> None:
    registry_mod._OLLAMA_GENERATE_WARNED.clear()


def _built_models(settings: Settings, **get_kw: object) -> list[str]:
    with patch("prismal.providers.registry.ChatLiteLLM") as cls:
        cls.return_value = MagicMock()
        ProviderRegistry(settings=settings).get_llm_with_fallback(**get_kw)  # type: ignore[arg-type]
    return [c.kwargs["model"] for c in cls.call_args_list]


def test_setting_defaults_to_off() -> None:
    assert _settings().ollama_chat_endpoint is False


def test_off_keeps_the_model_string_unchanged() -> None:
    assert _built_models(_settings()) == ["ollama/qwen3:14b"]


def test_on_rewrites_primary_and_fallback_to_ollama_chat() -> None:
    settings = _settings(ollama_chat_endpoint=True, fallback_model="ollama/qwen3:8b")
    assert _built_models(settings) == ["ollama_chat/qwen3:14b", "ollama_chat/qwen3:8b"]


def test_on_rewrites_an_explicit_model() -> None:
    assert _built_models(_settings(ollama_chat_endpoint=True), model="ollama/llama3.2") == [
        "ollama_chat/llama3.2"
    ]


@pytest.mark.parametrize("model", ["ollama_chat/qwen3:14b", "claude-sonnet-4-5", "gpt-4o"])
def test_on_leaves_other_models_alone(model: str) -> None:
    assert _built_models(_settings(ollama_chat_endpoint=True), model=model) == [model]


def test_off_warns_once_per_model_about_text_emulated_tools() -> None:
    with capture_logs() as logs:
        _built_models(_settings())
        _built_models(_settings())
    warned = [e for e in logs if e["event"] == "provider.ollama_generate_endpoint"]
    assert len(warned) == 1
    assert warned[0]["log_level"] == "warning"
    assert warned[0]["model"] == "ollama/qwen3:14b"
    assert "ollama_chat" in warned[0]["hint"]


def test_on_does_not_warn() -> None:
    with capture_logs() as logs:
        _built_models(_settings(ollama_chat_endpoint=True))
    assert not [e for e in logs if e["event"] == "provider.ollama_generate_endpoint"]


def test_rewritten_model_keeps_the_ollama_timeout() -> None:
    settings = _settings(ollama_chat_endpoint=True, llm_ollama_timeout_seconds=123)
    with patch("prismal.providers.registry.ChatLiteLLM") as cls:
        cls.return_value = MagicMock()
        ProviderRegistry(settings=settings).get_llm()
    assert cls.call_args.kwargs["request_timeout"] == 123.0
