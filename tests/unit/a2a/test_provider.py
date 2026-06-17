"""A2AToolProvider — remote skills as tools (Phase I — SPEC-A2A-005)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from prismal.a2a.provider import A2AToolProvider
from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, AgentCard, AgentSkill
from prismal.agents.extension import (
    CompositeToolProvider,
    StubToolProvider,
    ToolProviderPort,
    conforms_to,
)

pytestmark = pytest.mark.unit


def _card() -> AgentCard:
    return AgentCard(
        name="billing",
        description="Billing agent",
        url="https://billing.acme/a2a",
        version="1.0.0",
        skills=[
            AgentSkill(
                id="create_invoice", name="Create Invoice", description="d", tags=["finance"]
            ),
            AgentSkill(id="refund", name="Refund", description="d", tags=["finance", "support"]),
        ],
    )


class _FakeClient:
    def __init__(self, text: str = "done") -> None:
        self._text = text

    async def send_task(
        self, message: A2AMessage, *, skill_id: str | None = None
    ) -> AsyncIterator[A2AArtifact]:
        yield A2AArtifact(artifact_id="a1", parts=[A2APart(kind="text", text=self._text)])

    async def aclose(self) -> None:  # pragma: no cover - not used
        pass


class _FakeManager:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get_client(self, url: str) -> _FakeClient:
        return self._client


class TestA2AToolProviderEnumeration:
    def test_conforms_to_tool_provider_port(self) -> None:
        provider = A2AToolProvider([_card()])
        assert conforms_to(provider, ToolProviderPort)

    def test_exposes_one_tool_per_skill(self) -> None:
        provider = A2AToolProvider([_card()])
        tools = provider.get_tools(agent_name="researcher")
        names = {t.name for t in tools}
        assert names == {"a2a__billing__create_invoice", "a2a__billing__refund"}

    def test_capabilities_filter_narrows_skills(self) -> None:
        provider = A2AToolProvider([_card()])
        tools = provider.get_tools(agent_name="researcher", capabilities=["support"])
        assert {t.name for t in tools} == {"a2a__billing__refund"}

    def test_url_only_agent_skipped_before_discovery(self) -> None:
        provider = A2AToolProvider(["https://billing.acme/.well-known/agent-card.json"])
        assert provider.get_tools(agent_name="researcher") == []

    def test_enumeration_never_raises(self) -> None:
        provider = A2AToolProvider([object()])  # type: ignore[list-item]
        assert provider.get_tools(agent_name="researcher") == []


class TestA2AToolProviderComposition:
    def test_composable_in_composite(self) -> None:
        composite = CompositeToolProvider([A2AToolProvider([_card()]), StubToolProvider()])
        tools = composite.get_tools(agent_name="researcher")
        assert any(t.name.startswith("a2a__billing__") for t in tools)


class TestA2AToolExecution:
    async def test_tool_invokes_remote_and_sanitizes(self) -> None:
        provider = A2AToolProvider([_card()], manager=_FakeManager(_FakeClient("invoice ok")))
        tool = next(
            t
            for t in provider.get_tools(agent_name="researcher")
            if t.name.endswith("create_invoice")
        )
        result = await tool.ainvoke({"query": "bill the customer"})
        assert "invoice ok" in result

    async def test_tool_sanitizes_control_chars(self) -> None:
        provider = A2AToolProvider([_card()], manager=_FakeManager(_FakeClient("bad\x00\x07out")))
        tool = next(t for t in provider.get_tools(agent_name="researcher"))
        result = await tool.ainvoke({"query": "x"})
        assert "\x00" not in result and "\x07" not in result

    async def test_tool_returns_message_on_delegate_failure(self) -> None:
        class _BoomClient:
            async def send_task(self, message, *, skill_id=None):  # type: ignore[no-untyped-def]
                raise RuntimeError("network down")
                yield  # pragma: no cover

        provider = A2AToolProvider([_card()], manager=_FakeManager(_BoomClient()))  # type: ignore[arg-type]
        tool = next(t for t in provider.get_tools(agent_name="researcher"))
        result = await tool.ainvoke({"query": "x"})
        assert "unavailable" in result.lower()


class TestA2AToolProviderPrepare:
    async def test_prepare_discovers_url_only_agents(self) -> None:
        import httpx
        import respx

        card_url = "https://billing.acme/.well-known/agent-card.json"
        card_json = {
            "name": "billing",
            "description": "d",
            "url": "https://billing.acme/a2a",
            "version": "1.0.0",
            "skills": [{"id": "invoice", "name": "Invoice", "description": "d"}],
        }
        with respx.mock:
            respx.get(card_url).mock(return_value=httpx.Response(200, json=card_json))
            provider = A2AToolProvider([card_url])
            assert provider.get_tools(agent_name="researcher") == []
            await provider.prepare()
        tools = provider.get_tools(agent_name="researcher")
        assert any(t.name == "a2a__billing__invoice" for t in tools)

    async def test_prepare_swallows_discovery_failure(self) -> None:
        import httpx
        import respx

        card_url = "https://down.acme/.well-known/agent-card.json"
        with respx.mock:
            respx.get(card_url).mock(side_effect=httpx.ConnectError("down"))
            provider = A2AToolProvider([card_url])
            await provider.prepare()  # must not raise
        assert provider.get_tools(agent_name="researcher") == []
