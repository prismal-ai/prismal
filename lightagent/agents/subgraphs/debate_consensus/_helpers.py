"""Shared helpers for debate_consensus role nodes.

All role nodes (proponent / opponent / moderator) share the same shape:
take the user's query + any prior :class:`DebatePosition` list in state,
ask the LLM for this role's stance, append a new ``DebatePosition``.

The only differences per role are the role label and the prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.agents.patterns.debate import DebatePosition
from lightagent.core.logging import get_logger
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.debate_consensus._helpers")


def make_role_node(
    llm: Any,
    role: str,
    system_prompt: str,
    agent_index: int,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node for one debate role.

    The node reads the user query (last message), formats prior positions
    into the user prompt, calls the LLM, and appends a new
    :class:`DebatePosition` to ``state["metadata"]["debate_consensus"]["positions"]``.
    """

    async def role_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        query = str(messages[-1].content)

        existing = dict(state.get("metadata") or {}).get("debate_consensus") or {}
        prior: list[DebatePosition] = list(existing.get("positions") or [])

        user_parts = [f"Query: {query}"]
        if prior:
            prior_text = "\n\n".join(f"[{p.role}] {p.content}" for p in prior)
            user_parts.append(f"Prior positions:\n{prior_text}")

        builder = SecurePromptBuilder()
        prompted = builder.build(system=system_prompt, user="\n\n".join(user_parts))
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning(
                "debate_consensus_role_error",
                role=role,
                error=str(exc),
            )
            # Degrade: append a placeholder position so downstream nodes see
            # something and the graph does not stall.
            content = f"({role} failed to produce a position)"
        else:
            content = str(response.content).strip()

        new_position = DebatePosition(
            agent_id=f"agent_{agent_index}",
            role=role,
            content=content,
            round=1,
        )
        positions = [*prior, new_position]
        logger.info(
            "debate_consensus_role_done",
            role=role,
            n_positions=len(positions),
        )
        updated = {**existing, "positions": positions}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "debate_consensus": updated,
            }
        }

    return role_node


__all__ = ["make_role_node"]
