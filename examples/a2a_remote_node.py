"""Delegate to a remote A2A agent — outbound node/tools demo (Phase I).

Drives the outbound side with a fake in-process A2A client (no network, no LLM):

1. Wrap a remote agent as a prismal graph node (``A2AAgentNode.as_node``) and run
   it over a minimal state — the remote answer is sanitized and merged into
   ``state["messages"]``.
2. Expose the remote agent's skills as tools (``A2AToolProvider``) conforming to
   the Phase Y ``ToolProviderPort`` and invoke one.

In production you pass a real Agent Card URL; ``A2AConnectionManager`` enforces
the outbound allowlist and pools real ``A2AClient`` connections.

Run::

    python examples/a2a_remote_node.py
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage

from prismal.a2a import A2AAgentNode, A2AToolProvider
from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, AgentCard, AgentSkill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

CARD = AgentCard(
    name="billing",
    description="Remote billing agent",
    url="https://billing.acme/a2a",
    version="1.0.0",
    skills=[AgentSkill(id="create_invoice", name="Create Invoice", description="Issue an invoice")],
)


class FakeRemoteClient:
    """Stand-in for A2AClient — yields a single artifact, no network."""

    async def send_task(
        self, message: A2AMessage, *, skill_id: str | None = None
    ) -> AsyncIterator[A2AArtifact]:
        text = message.parts[0].text if message.parts else ""
        yield A2AArtifact(
            artifact_id="a1",
            parts=[A2APart(kind="text", text=f"invoice created for: {text}")],
        )

    async def aclose(self) -> None:
        pass


class FakeManager:
    def __init__(self, client: FakeRemoteClient) -> None:
        self._client = client

    async def get_client(self, url: str) -> FakeRemoteClient:
        return self._client


async def main() -> None:
    # 1. Remote agent as a graph node.
    node = A2AAgentNode(CARD, client=FakeRemoteClient(), skill_id="create_invoice").as_node(
        name="billing_agent"
    )
    state = {"messages": [HumanMessage(content="bill customer #42 for $99")]}
    update = await node(state)
    answer = next(m for m in update["messages"] if isinstance(m, AIMessage)).content
    print("node delegation →", answer)
    print("metadata.a2a   →", update["metadata"]["a2a"])

    # 2. Remote skills as tools.
    provider = A2AToolProvider([CARD], manager=FakeManager(FakeRemoteClient()))
    tools = provider.get_tools(agent_name="researcher")
    print("\nexposed tools  →", [t.name for t in tools])
    result = await tools[0].ainvoke({"query": "invoice for ACME"})
    print("tool call      →", result)


if __name__ == "__main__":
    asyncio.run(main())
