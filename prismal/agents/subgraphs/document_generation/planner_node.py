"""Planner node for document_generation subgraph.

Takes the user's topic (last message) and asks the LLM for a numbered list
of section titles. Stores the parsed outline at
``state["metadata"]["document_generation"]["outline"]`` as
``list[str]``.

Parsing is permissive: lines matching ``"<n>. <title>"`` are extracted; if
nothing matches, the non-empty lines themselves are used, ensuring the
researcher step always has *some* outline to work from.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.document_generation.planner")

_PROMPT = (
    "You are a documentation planner. Given a topic, output a concise "
    "numbered outline of 3-6 section titles. Reply with ONLY the numbered "
    "list (one section per line, like '1. Title'). No preamble."
)

_NUMBERED_RE = re.compile(r"^\d+[\.\)]\s*(.+)$")


def make_planner_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that plans the document outline."""

    async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        topic = str(messages[-1].content)

        builder = SecurePromptBuilder()
        prompted = builder.build(system=_PROMPT, user=topic)
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning("document_planner_error", error=str(exc))
            outline: list[str] = [topic]
        else:
            outline = _parse_outline(str(response.content))
            if not outline:
                outline = [topic]

        logger.info("document_plan_built", n_sections=len(outline))
        existing = dict(state.get("metadata") or {}).get("document_generation") or {}
        updated = {**existing, "outline": outline}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "document_generation": updated,
            }
        }

    return planner_node


def _parse_outline(raw: str) -> list[str]:
    titles: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _NUMBERED_RE.match(stripped)
        if match:
            titles.append(match.group(1).strip())
        else:
            titles.append(stripped)
    return titles


__all__ = ["make_planner_node"]
