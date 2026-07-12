"""Spec agent node for the Blind Review Pipeline (Phase BRP2, SPEC-BRP-SPEC-001).

``spec_agent_node`` is the pipeline's entry point and the **only** node allowed
to read ``state["messages"]`` in full — it turns free-form user intent into a
bounded ``spec_artifact`` written to
``state["metadata"]["blind_review"]["spec_artifact"]``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = structlog.get_logger("prismal.subgraphs.blind_review_pipeline.spec")
otel = OTelManager()

SpecFn = Callable[[str], Awaitable[str]]  # (goal) -> spec_artifact text

_SYSTEM = """You are the Spec Agent of the Blind Review Pipeline.

Turn the user's goal into a clear, bounded specification that a separate
implementer agent — and two independent reviewers — can work from without ever
seeing this conversation. Capture the intent, the acceptance criteria, and any
explicit constraints. Emit the specification text only, no preamble."""

__all__ = ["SpecFn", "make_spec_agent_node"]


def _extract_goal(state: dict[str, Any]) -> str:
    """Return the goal text from the most recent human/user message.

    This is the single point where the pipeline reads ``state["messages"]``;
    downstream nodes work only from the derived ``spec_artifact``.
    """
    messages = state.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if content:
            return str(content)
    return ""


def _default_spec_fn(settings: Settings) -> SpecFn:
    """Build the default spec_fn: role-scoped tools + configured model via ports."""

    async def spec_fn(goal: str) -> str:
        from prismal.agents.tool_registry import get_tools_for_agent
        from prismal.providers.registry import ProviderRegistry
        from prismal.security.prompt_builder import SecurePromptBuilder

        tools = get_tools_for_agent("spec_agent", settings.blind_review_spec_capabilities)
        llm: Any = ProviderRegistry(settings=settings).get_llm(
            model=settings.blind_review_spec_model or None
        )
        if tools and hasattr(llm, "bind_tools"):
            llm = llm.bind_tools(tools)

        prompt = SecurePromptBuilder().build(system=_SYSTEM, user=goal)
        response = await llm.ainvoke(
            [
                SystemMessage(content=prompt[0]["content"]),
                HumanMessage(content=prompt[1]["content"]),
            ]
        )
        return str(response.content)

    return spec_fn


def make_spec_agent_node(
    spec_fn: SpecFn | None = None,
    *,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node: goal (``state["messages"]``) -> spec_artifact.

    Args:
        spec_fn: Injected ``(goal) -> spec_artifact`` coroutine. ``None`` wires
            the default, which resolves ``ProviderRegistry(settings).get_llm(
            settings.blind_review_spec_model)`` and role-scoped tools via the
            injected ``ToolProviderPort`` (``agent_name="spec_agent"``).
        settings: Optional settings override; ``None`` resolves ``get_settings()``
            lazily at call time.

    Returns:
        An async node writing ``state["metadata"]["blind_review"]["spec_artifact"]``.
    """

    async def spec_agent_node(state: dict[str, Any]) -> dict[str, Any]:
        with otel.start_span("blind_review.spec") as span:
            span.set_attribute("prismal.subgraph", "blind_review_pipeline")
            span.set_attribute("prismal.agent", "spec_agent")

            resolved_settings = settings
            if resolved_settings is None:
                from prismal.core.config import get_settings

                resolved_settings = get_settings()
            fn = spec_fn or _default_spec_fn(resolved_settings)

            goal = _extract_goal(state)
            spec_artifact = await fn(goal)

            br = dict(state.get("metadata", {}).get("blind_review", {}))
            br["spec_artifact"] = spec_artifact
            logger.info("blind_review.spec_written", chars=len(spec_artifact))

            return {
                "current_agent": "spec_agent",
                "messages": [AIMessage(content="Specification written.")],
                "metadata": {**state.get("metadata", {}), "blind_review": br},
            }

    return spec_agent_node
