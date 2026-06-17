"""A2AToolProvider — remote A2A skills as tools (Phase I — SPEC-A2A-005).

Exposes each *skill* of allowlisted remote agents as a ``BaseTool`` named
``a2a__{agent}__{skill}``, conforming to the Phase Y ``ToolProviderPort`` so the
host can drop it into a ``CompositeToolProvider``. ``get_tools`` is **sync and
never raises** (port contract); remote results are sanitized before returning.

Discovery is async (the well-known card fetch), but the port is sync — so
agents passed as *URLs* are only exposed after :meth:`prepare` is awaited
(typically inside the async ``build_runtime``). Agents passed as resolved
:class:`AgentCard` objects are exposed immediately, no network needed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from prismal.a2a.types import A2AMessage, A2APart, AgentCard, AgentSkill
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from prismal.a2a.client import A2AConnectionManager

logger = get_logger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("_", name.lower()).strip("_")


class A2AToolProvider:
    """Remote A2A agents' skills, surfaced as LangChain tools (SPEC-A2A-005)."""

    def __init__(
        self,
        agents: list[str | AgentCard],
        *,
        manager: A2AConnectionManager | None = None,
    ) -> None:
        self._manager = manager
        # Resolved cards keyed by their endpoint URL.
        self._cards: dict[str, AgentCard] = {}
        # URLs awaiting discovery (only those given as plain strings).
        self._pending: list[str] = []
        for agent in agents:
            if isinstance(agent, AgentCard):
                self._cards[agent.url] = agent
            elif isinstance(agent, str):
                self._pending.append(agent)
            else:
                logger.warning("a2a.provider.bad_agent", type=type(agent).__name__)

    async def prepare(self) -> None:
        """Discover the Agent Cards of any URL-only agents (call once, async)."""
        from prismal.a2a.client import A2AClient

        for url in list(self._pending):
            try:
                client = (
                    await self._manager.get_client(url)
                    if self._manager is not None
                    else A2AClient(url)
                )
                card = await client.discover()
                self._cards[card.url] = card
                self._pending.remove(url)
            except Exception as exc:  # never abort host startup
                logger.warning("a2a.provider.discover_failed", url=url, error=str(exc))

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[BaseTool]:
        """Return remote skills as tools, filtered by *capabilities* (never raises)."""
        del agent_name  # A2A tools are offered to any requesting agent
        try:
            tools: list[BaseTool] = []
            for card in self._cards.values():
                for skill in card.skills:
                    if capabilities is not None and not (set(skill.tags) & set(capabilities)):
                        continue
                    tools.append(self._make_tool(card, skill))
            return tools
        except Exception as exc:  # pragma: no cover - defensive (port contract)
            logger.warning("a2a.provider.get_tools_error", error=str(exc))
            return []

    def _make_tool(self, card: AgentCard, skill: AgentSkill) -> BaseTool:
        from langchain_core.tools import StructuredTool

        tool_name = f"a2a__{_slug(card.name)}__{skill.id}"

        async def _call(query: str) -> str:
            return await self._delegate(card, skill.id, query)

        description = (
            f"Delegate to remote A2A agent '{card.name}' skill '{skill.name}': {skill.description}"
        )
        return StructuredTool.from_function(
            coroutine=_call, name=tool_name, description=description
        )

    async def _delegate(self, card: AgentCard, skill_id: str, query: str) -> str:
        from prismal.a2a.client import A2AClient
        from prismal.security import InputSanitizer

        client: Any
        if self._manager is not None:
            client = await self._manager.get_client(card.url)
        else:
            client = A2AClient(card)
        msg = A2AMessage(role="user", parts=[A2APart(kind="text", text=query)], message_id="tool")
        texts: list[str] = []
        try:
            async for artifact in client.send_task(msg, skill_id=skill_id):
                for part in artifact.parts:
                    if part.kind == "text" and part.text:
                        texts.append(part.text)
        except Exception as exc:
            logger.warning("a2a.provider.delegate_failed", agent=card.name, error=str(exc))
            return f"[a2a] remote agent '{card.name}' unavailable: {exc}"
        return InputSanitizer().sanitize("\n".join(texts)) if texts else ""


__all__ = ["A2AToolProvider"]
