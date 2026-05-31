"""Tests for vision/multimodal LLM wrappers (Fase F, SPEC-MM-PROV-003/004)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prismal.core.config import Settings
from prismal.providers import multimodal as mm_module
from prismal.providers import vision as vision_module


class TestVisionLLM:
    def test_explicit_model_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = MagicMock()
        get_llm = MagicMock(return_value=sentinel)
        monkeypatch.setattr(
            vision_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        result = vision_module.get_vision_llm("gpt-4o")
        assert result is sentinel
        get_llm.assert_called_once_with("gpt-4o")

    def test_defaults_to_vision_model_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            vision_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        vision_module.get_vision_llm(settings=Settings(vision_model="claude-vision-x"))
        get_llm.assert_called_once_with("claude-vision-x")

    def test_falls_back_to_cua_vision_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            vision_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        vision_module.get_vision_llm(
            settings=Settings(vision_model="", cua_vision_model="claude-opus-4-6")
        )
        get_llm.assert_called_once_with("claude-opus-4-6")


class TestMultimodalLLM:
    def test_defaults_to_multimodal_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            mm_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        mm_module.get_multimodal_llm(settings=Settings())
        get_llm.assert_called_once_with("gemini/gemini-2.0-flash")

    def test_explicit_model_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            mm_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        mm_module.get_multimodal_llm("gpt-4o", settings=Settings())
        get_llm.assert_called_once_with("gpt-4o")

    def test_resolves_settings_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            mm_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        mm_module.get_multimodal_llm()  # settings=None → resolved internally
        get_llm.assert_called_once()

    def test_falls_back_to_default_model_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_llm = MagicMock()
        monkeypatch.setattr(
            mm_module, "ProviderRegistry", lambda *a, **k: MagicMock(get_llm=get_llm)
        )
        mm_module.get_multimodal_llm(
            settings=Settings(multimodal_model="", default_model="claude-sonnet-4-6")
        )
        get_llm.assert_called_once_with("claude-sonnet-4-6")
