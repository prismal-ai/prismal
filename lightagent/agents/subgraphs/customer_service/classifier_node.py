"""Customer-service query classifier node.

Takes the latest user message in state and classifies it into one of:
``faq`` / ``complaint`` / ``technical`` / ``other``. Writes the category
(and the raw LLM response, for audit) to
``state["metadata"]["customer_service"]``.

Parsing is permissive: the LLM reply is lowercased and matched against the
known categories as a substring. Unparseable replies default to ``other``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.logging import get_logger
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.customer_service.classifier")

CUSTOMER_SERVICE_CATEGORIES: tuple[str, ...] = ("faq", "complaint", "technical", "other")

_CLASSIFIER_PROMPT = (
    "Classify the user's customer-service query into exactly one of four "
    "categories: faq, complaint, technical, other. Reply with only the "
    "category word. Use 'faq' for policy / how-to questions, 'complaint' for "
    "expressions of dissatisfaction, 'technical' for bug reports or product "
    "malfunctions, and 'other' when none of the above fits."
)


def make_classifier_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that classifies the latest user message."""

    async def classifier_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        query = str(messages[-1].content)

        builder = SecurePromptBuilder()
        prompted = builder.build(system=_CLASSIFIER_PROMPT, user=query)
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompted[0]["content"]),
                    HumanMessage(content=prompted[1]["content"]),
                ]
            )
        except Exception as exc:
            logger.warning("customer_service_classifier_error", error=str(exc))
            category = "other"
            raw = ""
        else:
            raw = str(response.content).strip()
            category = _parse_category(raw)

        logger.info("customer_service_classified", category=category)
        existing = dict(state.get("metadata") or {}).get("customer_service") or {}
        updated_cs = {**existing, "category": category, "classifier_raw": raw}
        return {"metadata": {**(state.get("metadata") or {}), "customer_service": updated_cs}}

    return classifier_node


def _parse_category(raw: str) -> str:
    lower = raw.lower()
    for category in CUSTOMER_SERVICE_CATEGORIES[:-1]:  # skip 'other'
        if category in lower:
            return category
    return "other"


__all__ = ["CUSTOMER_SERVICE_CATEGORIES", "make_classifier_node"]
