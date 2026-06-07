"""output node — render the final assistant message (SPEC-KOK-SG-001).

Appends one assistant message with the decision + rationale (and the action
result / blocked reason when present).  On an upstream error state it emits a
plain error notice instead — the graph always terminates with a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

from prismal.agents.subgraphs.kokoro._helpers import get_kokoro
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.agents.kokoro.judge import Verdict

logger = get_logger("prismal.subgraphs.kokoro.output")


async def output_node(state: dict[str, Any]) -> dict[str, Any]:
    """Append the Kokoro verdict (or error notice) as an assistant message."""
    kokoro = get_kokoro(state)

    error = kokoro.get("error")
    if error:
        return {"messages": [AIMessage(content=f"Kokoro could not deliberate: {error}")]}

    verdict: Verdict | None = kokoro.get("verdict")
    if verdict is None:  # defensive — judge node always writes verdict or error
        return {"messages": [AIMessage(content="Kokoro produced no verdict.")]}

    parts: list[str] = [verdict.decision]
    if verdict.rationale:
        parts.append(f"\nRationale: {verdict.rationale}")
    if verdict.dissent_retained:
        dissent = "; ".join(verdict.dissent_retained)
        parts.append(f"\nDissent retained: {dissent}")

    action = verdict.action
    if action is not None:
        if action.executed:
            parts.append(f"\nAction '{action.tool_name}' executed: {action.result}")
        elif action.blocked_reason:
            parts.append(f"\nAction '{action.tool_name}' blocked: {action.blocked_reason}")

    logger.info(
        "kokoro_output_rendered",
        has_action=action is not None,
        dissent_count=len(verdict.dissent_retained),
    )
    return {"messages": [AIMessage(content="\n".join(parts))]}


__all__ = ["output_node"]
