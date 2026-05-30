"""Tests for the extension-surface settings (Fase X)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


class TestExtensionSettingsDefaults:
    def test_plugin_defaults(self) -> None:
        s = Settings()
        assert s.plugins_autodiscover is True
        assert s.plugins_allowlist == []
        assert s.plugins_denylist == []
        assert s.plugins_groups_enabled == ["subgraphs", "nodes", "tools", "rag_engines"]

    def test_decorator_defaults(self) -> None:
        s = Settings()
        assert s.extension_default_security == "standard"
        assert s.extension_default_audit is True
        assert s.extension_default_timeout_s is None


class TestExtensionSettingsOverrides:
    def test_allowlist_and_denylist_overridable(self) -> None:
        s = Settings(
            plugins_allowlist=["prismal_x_finance"],
            plugins_denylist=["broken_plugin"],
        )
        assert s.plugins_allowlist == ["prismal_x_finance"]
        assert s.plugins_denylist == ["broken_plugin"]

    def test_env_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRISMAL_PLUGINS_AUTODISCOVER", "false")
        monkeypatch.setenv("PRISMAL_EXTENSION_DEFAULT_SECURITY", "strict")
        s = Settings()
        assert s.plugins_autodiscover is False
        assert s.extension_default_security == "strict"

    def test_invalid_security_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(extension_default_security="paranoid")
