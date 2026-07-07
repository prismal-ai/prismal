"""Tests: ToolProviderPort's optional phase keyword is a non-breaking widening (Phase LH — LH2-02)."""

from __future__ import annotations

import inspect

from prismal.agents.extension.ports import ToolProviderPort


def test_fake_tool_provider_without_phase_still_conforms() -> None:
    from prismal.agents.extension.providers import FakeToolProvider

    provider = FakeToolProvider()
    assert isinstance(provider, ToolProviderPort)


def test_get_tools_declares_optional_phase_keyword() -> None:
    sig = inspect.signature(ToolProviderPort.get_tools)
    phase_param = sig.parameters["phase"]
    assert phase_param.default is None
    assert phase_param.kind is inspect.Parameter.KEYWORD_ONLY
