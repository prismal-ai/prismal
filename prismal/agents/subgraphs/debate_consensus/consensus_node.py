"""Consensus node for debate_consensus subgraph (terminal).

Synthesises a single final consensus from all accumulated positions and
computes the Jaccard-based agreement score (reused from
:mod:`lightagent.agents.patterns.debate`). Appends an ``AIMessage`` with
the consensus text so the caller sees the final answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.patterns.debate import pairwise_jaccard
from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.patterns.debate import DebatePosition

logger = get_logger("lightagent.subgraphs.debate_consensus.consensus")

_PROMPT = (
    "You are a neutral synthesiser. Given the positions of a debate, "
    "produce ONE concise consensus (3-5 sentences) that integrates the "
    "strongest points from each side. Paraphrase — do not copy any position "
    "verbatim."
)


def make_consensus_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that produces the final consensus."""

    async def consensus_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("debate_consensus") or {}
        positions: list[DebatePosition] = list(existing.get("positions") or [])
        if not positions:
            logger.info("debate_consensus_skip", reason="no_positions")
            return {}

        messages = state.get("messages") or []
        query = str(messages[-1].content) if messages else ""
        positions_text = "\n\n".join(f"[{p.role}] {p.content}" for p in positions)
        user = f"Query: {query}\n\nFinal positions:\n{positions_text}"
        builder = SecurePromptBuilder()
        prompted = builder.build(system=_PROMPT, user=user)
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning("debate_consensus_error", error=str(exc))
            consensus = positions[0].content
        else:
            consensus = str(response.content).strip()

        agreement = pairwise_jaccard([p.content for p in positions])
        logger.info(
            "debate_consensus_done",
            n_positions=len(positions),
            agreement=agreement,
        )

        updated = {
            **existing,
            "consensus": consensus,
            "agreement_score": agreement,
        }
        return {
            "messages": [AIMessage(content=consensus)],
            "metadata": {
                **(state.get("metadata") or {}),
                "debate_consensus": updated,
            },
        }

    return consensus_node


__all__ = ["make_consensus_node"]
