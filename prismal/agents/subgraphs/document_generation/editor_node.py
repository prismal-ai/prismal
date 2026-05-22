"""Editor node for document_generation subgraph.

Polishes the draft produced by ``writer_node``. Stores the result at
``state["metadata"]["document_generation"]["edited"]``. If no ``draft`` is
present, the node returns ``{}`` (nothing to edit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.document_generation.editor")

_PROMPT = (
    "You are a copy editor. Polish the draft: fix grammar, improve clarity, "
    "tighten verbose passages, keep the author's voice. Do NOT add new "
    "information. Return only the edited document."
)


def make_editor_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that polishes the draft."""

    async def editor_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("document_generation") or {}
        draft = existing.get("draft")
        if not draft:
            return {}

        builder = SecurePromptBuilder()
        prompted = builder.build(system=_PROMPT, user=str(draft))
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning("document_editor_error", error=str(exc))
            # Degrade: keep the unedited draft as "edited" so downstream
            # still has something to format.
            edited = str(draft)
        else:
            edited = str(response.content).strip()

        logger.info("document_edit_done", edited_len=len(edited))
        updated = {**existing, "edited": edited}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "document_generation": updated,
            }
        }

    return editor_node


__all__ = ["make_editor_node"]
