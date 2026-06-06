"""Tests for the global tool-count cap in :mod:`prismal.agents.tool_registry`.

OpenAI rejects ``tools`` arrays longer than 128 entries with a
``BadRequestError``.  ``get_tools_for_agent`` (delegating to the injected
``CompositeToolProvider`` since Fase Y) must enforce a hard upper bound
(``_MAX_TOTAL_TOOLS``) so the merged MCP + skills + stubs list never
exceeds the provider limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

if TYPE_CHECKING:
    import pytest

from prismal.agents import tool_registry
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    StubToolProvider,
)


def _make_tools(prefix: str, count: int) -> list[BaseTool]:
    """Build *count* trivial tools whose names start with *prefix*."""

    tools: list[BaseTool] = []
    for i in range(count):

        @tool(f"{prefix}_{i}")
        def _stub(x: str = "") -> str:
            """Stub tool used by tool_registry cap tests."""
            return x

        tools.append(_stub)
    return tools


def test_get_tools_for_agent_caps_at_max_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When MCP + skills exceed ``_MAX_TOTAL_TOOLS`` the tail is dropped."""

    fake_mcp = _make_tools("mcp", 60)
    fake_skill = _make_tools("skill", 90)

    provider = CompositeToolProvider(
        [
            FakeToolProvider(default=fake_mcp),
            FakeToolProvider(default=fake_skill),
            StubToolProvider(),
        ]
    )
    monkeypatch.setattr(tool_registry, "_provider", provider)

    tools = tool_registry.get_tools_for_agent("researcher")

    assert len(tools) == tool_registry._MAX_TOTAL_TOOLS
    assert len(tools) <= 128, "Must stay below the OpenAI 128-tool limit"
    # Priority order: MCP first, then skills.  All 60 MCP tools survive.
    mcp_names = {t.name for t in tools if t.name.startswith("mcp_")}
    assert len(mcp_names) == 60


def test_get_tools_for_agent_no_cap_when_under_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is a no-op when the merged list is under the limit."""

    fake_mcp = _make_tools("mcp", 10)
    fake_skill = _make_tools("skill", 5)

    provider = CompositeToolProvider(
        [
            FakeToolProvider(default=fake_mcp),
            FakeToolProvider(default=fake_skill),
            StubToolProvider(),
        ]
    )
    monkeypatch.setattr(tool_registry, "_provider", provider)

    tools = tool_registry.get_tools_for_agent("researcher")

    # 15 live tools + however many researcher stubs are not shadowed.
    assert len(tools) >= 15
    assert len(tools) < tool_registry._MAX_TOTAL_TOOLS


def test_max_total_tools_under_openai_limit() -> None:
    """The configured cap must stay strictly below OpenAI's 128 limit."""

    assert tool_registry._MAX_TOTAL_TOOLS < 128
