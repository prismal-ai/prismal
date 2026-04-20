"""Response-generator node for customer_service subgraph.

Produces the final customer-facing answer as an ``AIMessage``. Retrieved
context (from ``faq_retrieval_node``) is folded into the prompt so the
response is grounded; if retrieval was empty we still generate, but the
prompt asks the LLM to acknowledge the gap rather than invent facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.core.logging import get_logger
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.customer_service.response_generator")

_SYSTEM_PROMPT = (
    "You are a customer-service agent. Answer the user's query in 2-4 "
    "concise sentences. Use the provided knowledge-base context; if context "
    "is empty or doesn't cover the query, explicitly say so instead of "
    "inventing details."
)


def make_response_generator_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that generates the final answer."""

    async def response_generator_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        query = str(messages[-1].content)

        cs = (state.get("metadata") or {}).get("customer_service") or {}
        retrieved = cs.get("retrieved") or []
        context_blob = "\n\n".join(
            f"[{i + 1}] {getattr(c, 'content', str(c))}"
            for i, c in enumerate(retrieved)
        ) or "(no knowledge-base context available)"
        user = f"Query: {query}\n\nKnowledge base:\n{context_blob}"

        builder = SecurePromptBuilder()
        prompted = builder.build(system=_SYSTEM_PROMPT, user=user)
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning("customer_service_response_error", error=str(exc))
            answer = (
                "I'm sorry — I couldn't produce an answer right now. "
                "Please try again in a moment."
            )
        else:
            answer = str(response.content).strip()

        return {"messages": [AIMessage(content=answer)]}

    return response_generator_node


__all__ = ["make_response_generator_node"]
