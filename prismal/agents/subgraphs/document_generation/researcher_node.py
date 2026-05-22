"""Researcher node for document_generation subgraph.

Iterates over the outline produced by ``planner_node`` and, for each
section, gathers notes. When a RAG engine is injected, its search results
are preferred; the LLM is always consulted (cheap, provides a baseline).
Notes are stored at
``state["metadata"]["document_generation"]["research"]`` as
``{section_title: notes_text}``.

If there is no outline in state (no planner run), returns ``{}`` — the
researcher has nothing to do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("prismal.subgraphs.document_generation.researcher")

_PROMPT = (
    "You are a researcher gathering notes for one section of a document. "
    "Produce 2-4 bullet points of factual content for the given section "
    "title. Plain text only, no markdown headers."
)


def make_researcher_node(
    llm: Any,
    rag_engine: Any | None = None,
    k: int = 3,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that gathers notes per outline section."""

    async def researcher_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("document_generation") or {}
        outline: list[str] = list(existing.get("outline") or [])
        if not outline:
            return {}

        research: dict[str, str] = {}
        for section in outline:
            rag_snippet = ""
            if rag_engine is not None:
                try:
                    chunks = rag_engine.search(section, k=k) or []
                except Exception as exc:
                    logger.warning(
                        "document_researcher_rag_error",
                        section=section,
                        error=str(exc),
                    )
                    chunks = []
                rag_snippet = "\n".join(f"- {getattr(c, 'content', str(c))}" for c in chunks)

            builder = SecurePromptBuilder()
            user = f"Section: {section}"
            if rag_snippet:
                user += f"\n\nRetrieved context:\n{rag_snippet}"
            prompted = builder.build(system=_PROMPT, user=user)
            try:
                response = await llm.ainvoke(
                    [
                        SystemMessage(content=prompted[0]["content"]),
                        HumanMessage(content=prompted[1]["content"]),
                    ]
                )
            except Exception as exc:
                logger.warning(
                    "document_researcher_error",
                    section=section,
                    error=str(exc),
                )
                notes = rag_snippet or ""
            else:
                notes = str(response.content).strip()
            research[section] = notes

        logger.info("document_research_done", n_sections=len(research))
        updated = {**existing, "research": research}
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "document_generation": updated,
            }
        }

    return researcher_node


__all__ = ["make_researcher_node"]
