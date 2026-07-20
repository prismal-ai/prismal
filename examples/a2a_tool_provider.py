"""A2A remote agents as tools — offline provider composition (Phase I + Fase Y).

Builds an ``AgentCard`` in code (no network discovery), surfaces its skills as
``a2a__{agent}__{skill}`` tools via ``A2AToolProvider``, invokes one tool
against a fake in-process transport, and composes the provider with local
tools through ``CompositeToolProvider``.

In a real host you would instead let the composition root do the wiring::

    ctx = await build_runtime(settings, a2a_agents=[card])  # a2a_enabled=True

(``prismal.composition.runtime.build_runtime`` composes the same
``A2AToolProvider`` into the runtime tool port and discovers URL-only agents
via ``provider.prepare()``.)

Run::

    python examples/a2a_tool_provider.py
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from prismal.a2a.provider import A2AToolProvider
from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, AgentCard, AgentSkill
from prismal.agents.extension.providers import CompositeToolProvider, FakeToolProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The remote agent's card, built in code — no /.well-known discovery needed.
CARD = AgentCard(
    name="Research Hub",
    description="A remote research agent exposed over A2A.",
    url="https://research-hub.example/a2a",
    version="1.0.0",
    skills=[
        AgentSkill(
            id="web_search",
            name="Web search",
            description="Search the public web and return cited findings.",
            tags=["research"],
        ),
        AgentSkill(
            id="summarize",
            name="Summarize",
            description="Condense a document into key bullet points.",
            tags=["writing"],
        ),
    ],
)


class _FakeA2AClient:
    """In-process stand-in for ``A2AClient`` — yields one text artifact per task."""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    async def send_task(
        self, message: A2AMessage, *, skill_id: str | None = None
    ) -> AsyncIterator[A2AArtifact]:
        query = (message.parts[0].text or "") if message.parts else ""
        yield A2AArtifact(
            artifact_id="art-1",
            parts=[
                A2APart(
                    kind="text",
                    text=f"[{self._agent_name}/{skill_id}] findings for: {query}",
                )
            ],
        )


class _FakeConnectionManager:
    """Stand-in for ``A2AConnectionManager`` — hands out the fake client."""

    async def get_client(self, url: str) -> _FakeA2AClient:
        del url
        return _FakeA2AClient("research-hub")


@tool
def local_notes(topic: str) -> str:
    """Look up local research notes for *topic*."""
    return f"local notes about {topic}"


async def main() -> None:
    """Surface remote skills as tools, call one, then compose with local tools."""
    provider = A2AToolProvider(
        [CARD],
        manager=_FakeConnectionManager(),  # type: ignore[arg-type] — fake transport
    )

    # ── 1. Remote skills surfaced as a2a__{agent}__{skill} tools ──────────────
    all_tools = provider.get_tools(agent_name="researcher")
    print("All remote tools:", [t.name for t in all_tools])

    research_only = provider.get_tools(agent_name="researcher", capabilities=["research"])
    print("Filtered by capabilities=['research']:", [t.name for t in research_only])

    # ── 2. Invoke one tool through the fake transport (L1-sanitized result) ───
    answer = await research_only[0].ainvoke({"query": "quantum sensing startups"})
    print(f"\n{research_only[0].name} → {answer}")

    # ── 3. Compose with local tools via CompositeToolProvider (Fase Y) ────────
    composite = CompositeToolProvider([provider, FakeToolProvider({"researcher": [local_notes]})])
    merged = composite.get_tools(agent_name="researcher", capabilities=["research"])
    print("\nComposite tools for 'researcher':", [t.name for t in merged])


if __name__ == "__main__":
    asyncio.run(main())
