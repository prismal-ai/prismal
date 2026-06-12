"""W3 — new Settings fields + ConfigSourceError + strict mode (Phase W)."""

from __future__ import annotations

import pytest

from prismal.core.config import build_settings
from prismal.core.config_source import (
    FakeConfigSource,
    MappingConfigSource,
    set_config_source,
)
from prismal.core.exceptions import ConfigSourceError, PrismalError


class TestNewFields:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_tavily_api_key_default_empty(self) -> None:
        assert build_settings(FakeConfigSource({})).tavily_api_key.get_secret_value() == ""

    def test_tavily_api_key_from_prefixed(self) -> None:
        s = build_settings(FakeConfigSource({"PRISMAL_TAVILY_API_KEY": "tvly-1"}))
        assert s.tavily_api_key.get_secret_value() == "tvly-1"

    def test_tavily_api_key_from_bare_alias(self) -> None:
        s = build_settings(FakeConfigSource({"TAVILY_API_KEY": "tvly-bare"}))
        assert s.tavily_api_key.get_secret_value() == "tvly-bare"

    def test_config_source_strict_default_false(self) -> None:
        assert build_settings(FakeConfigSource({})).config_source_strict is False


class TestConfigSourceError:
    def test_is_prismal_error(self) -> None:
        assert issubclass(ConfigSourceError, PrismalError)

    def test_message_includes_source_and_cause(self) -> None:
        err = ConfigSourceError("vault", "connection refused")
        assert "vault" in str(err)
        assert "connection refused" in str(err)

    def test_message_without_cause(self) -> None:
        assert "none" in str(ConfigSourceError("none"))


class TestStrictMode:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_strict_with_no_source_raises(self, monkeypatch) -> None:
        # config_source_strict comes from the ambient default source.
        monkeypatch.setenv("PRISMAL_LLM_PROVIDER", "")  # neutralise repo .env
        monkeypatch.setenv("PRISMAL_CONFIG_SOURCE_STRICT", "true")
        set_config_source(None)
        with pytest.raises(ConfigSourceError):
            build_settings()

    def test_strict_with_injected_source_does_not_raise(self, monkeypatch) -> None:
        monkeypatch.setenv("PRISMAL_CONFIG_SOURCE_STRICT", "true")
        set_config_source(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "ok"}))
        # an injected global source satisfies strict mode
        assert build_settings().default_model == "ok"

    def test_non_strict_with_no_source_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("PRISMAL_LLM_PROVIDER", "")  # neutralise repo .env
        monkeypatch.setenv("PRISMAL_CONFIG_SOURCE_STRICT", "false")
        set_config_source(None)
        # falls back to EnvConfigSource, no raise
        assert build_settings() is not None
