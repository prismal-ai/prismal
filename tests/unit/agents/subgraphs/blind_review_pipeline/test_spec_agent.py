"""Unit tests for the blind_review_pipeline spec agent node (Phase BRP2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage


@pytest.mark.asyncio
async def test_spec_agent_writes_spec_artifact() -> None:
    """spec_agent_node writes the injected spec_fn output to spec_artifact (SPEC-BRP-SPEC-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import make_spec_agent_node

    async def fake_spec_fn(goal: str) -> str:
        return f"SPEC for: {goal}"

    node = make_spec_agent_node(fake_spec_fn)
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="build a parser")],
        "metadata": {},
    }

    update = await node(state)

    assert update["metadata"]["blind_review"]["spec_artifact"] == "SPEC for: build a parser"


@pytest.mark.asyncio
async def test_default_spec_fn_resolves_configured_model_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default spec_fn wires the configured model + role-scoped tools (SPEC-BRP-SPEC-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import make_spec_agent_node
    from prismal.core.config import Settings

    calls: dict[str, Any] = {}

    class FakeLLM:
        async def ainvoke(self, messages: Any) -> Any:
            calls["messages"] = messages
            return SimpleNamespace(content="SPEC OUT")

    class FakeRegistry:
        def __init__(self, *, settings: Any = None) -> None:
            calls["registry_settings"] = settings

        def get_llm(self, model: str | None = None) -> FakeLLM:
            calls["model"] = model
            return FakeLLM()

    def fake_get_tools(
        agent_name: str, capabilities: list[str] | None = None, **_: Any
    ) -> list[Any]:
        calls["agent_name"] = agent_name
        calls["capabilities"] = capabilities
        return []

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("prismal.agents.tool_registry.get_tools_for_agent", fake_get_tools)

    settings = Settings(blind_review_spec_model="claude-test-spec")
    node = make_spec_agent_node(settings=settings)
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="build X")],
        "metadata": {},
    }

    update = await node(state)

    assert calls["model"] == "claude-test-spec"
    assert calls["agent_name"] == "spec_agent"
    assert calls["capabilities"] == settings.blind_review_spec_capabilities
    assert update["metadata"]["blind_review"]["spec_artifact"] == "SPEC OUT"


@pytest.mark.asyncio
async def test_default_spec_fn_binds_tools_and_reads_dict_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default spec_fn binds non-empty tools and reads dict-shaped messages (BRP6)."""
    from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import make_spec_agent_node
    from prismal.core.config import Settings

    calls: dict[str, Any] = {}

    class FakeLLM:
        def bind_tools(self, tools: Any) -> FakeLLM:
            calls["bound"] = tools
            return self

        async def ainvoke(self, messages: Any) -> Any:
            return SimpleNamespace(content="SPEC OUT")

    class FakeRegistry:
        def __init__(self, *, settings: Any = None) -> None:
            pass

        def get_llm(self, model: str | None = None) -> FakeLLM:
            return FakeLLM()

    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr(
        "prismal.agents.tool_registry.get_tools_for_agent",
        lambda *a, **k: ["tool_a"],
    )

    node = make_spec_agent_node(settings=Settings())
    # Dict-shaped message (no ``.content`` attribute) exercises the dict branch.
    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "build a widget"}],
        "metadata": {},
    }

    update = await node(state)

    assert calls["bound"] == ["tool_a"]
    assert update["metadata"]["blind_review"]["spec_artifact"] == "SPEC OUT"
