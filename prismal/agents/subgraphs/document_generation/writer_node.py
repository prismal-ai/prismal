"""Writer node for document_generation subgraph.

Takes the ``outline`` (list of section titles) and ``research`` (per-section
notes) produced by earlier nodes, and asks the LLM to compose a full draft.
The draft is stored at
``state["metadata"]["document_generation"]["draft"]`` and flows downstream
to ``editor_node``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.document_generation.writer")

_PROMPT = (
    "You are a documentation writer. Given an outline and research notes per "
    "section, produce a complete, coherent draft document with a section for "
    "each outline entry. Use plain prose; do not add metadata commentary."
)


def make_writer_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that writes the full draft."""

    async def writer_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("document_generation") or {}
        outline: list[str] = list(existing.get("outline") or [])
        research: dict[str, str] = dict(existing.get("research") or {})
        if not outline:
            return {}

        sections_blob = "\n\n".join(
            f"## {title}\n{research.get(title, '(no notes)')}" for title in outline
        )
        user = f"Outline + notes:\n\n{sections_blob}"
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
            logger.warning("document_writer_error", error=str(exc))
            # Degrade gracefully: use the notes themselves as draft.
            draft = sections_blob
        else:
            draft = str(response.content).strip()

        logger.info("document_draft_built", draft_len=len(draft))
        updated = {**existing, "draft": draft}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "document_generation": updated,
            }
        }

    return writer_node


__all__ = ["make_writer_node"]
