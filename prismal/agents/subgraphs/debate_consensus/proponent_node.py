"""Proponent node for debate_consensus subgraph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.debate_consensus._helpers import make_role_node

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_PROMPT = (
    "You are the PROPONENT in a structured debate. Produce a concise stance "
    "(2-4 sentences) arguing IN FAVOUR of the proposition in the user's "
    "query. Ground your argument; do not hedge."
)


def make_proponent_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node producing the proponent position."""
    return make_role_node(
        llm=llm,
        role="proponent",
        system_prompt=_PROMPT,
        agent_index=0,
    )


__all__ = ["make_proponent_node"]
