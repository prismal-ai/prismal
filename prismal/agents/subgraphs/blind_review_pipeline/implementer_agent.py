"""Implementer agent node for the Blind Review Pipeline (Phase BRP2, SPEC-BRP-IMPL-001).

``implementer_agent_node`` reads **only**
``state["metadata"]["blind_review"]["spec_artifact"]`` and, on a correction
pass, the structured issue list from
``state["metadata"]["blind_review"]["synthesis"]["report"]["issues"]`` — never
``state["messages"]`` (RF-BRP-02). It gates any file/code action through the
``ActionInterceptor`` before delegating to ``implementer_fn``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.agents.subgraphs.code_review.types import CodeIssue
    from prismal.core.config import Settings
    from prismal.security.action_interceptor import ActionInterceptor

logger = structlog.get_logger("prismal.subgraphs.blind_review_pipeline.implementer")
otel = OTelManager()

ImplementerFn = Callable[[str, "list[CodeIssue] | None"], Awaitable[str]]
# (spec_artifact, prior_issues_or_None) -> implementation_artifact

_SYSTEM = """You are the Implementer Agent of the Blind Review Pipeline.

Implement the specification exactly. On a correction pass you also receive a
structured list of issues raised by the reviewers — address every one. Work
only from the specification (and those issues); you do not see the original
conversation. Emit the implementation artifact only."""

__all__ = ["ImplementerFn", "make_implementer_agent_node"]


def _extract_prior_issues(br: dict[str, Any]) -> list[CodeIssue] | None:
    """Return the structured correction issues from a prior synthesis, or None.

    Reads ``synthesis.report.issues`` — the deterministic, structured list, never
    raw reviewer prose (``PLAN.md §6.2``).
    """
    issues = br.get("synthesis", {}).get("report", {}).get("issues")
    if issues:
        return list(issues)
    return None


def _default_implementer_fn(settings: Settings) -> ImplementerFn:
    """Build the default implementer_fn: role-scoped tools + configured model."""

    async def implementer_fn(spec_artifact: str, prior_issues: list[CodeIssue] | None) -> str:
        from prismal.agents.tool_registry import get_tools_for_agent
        from prismal.providers.registry import ProviderRegistry
        from prismal.security.prompt_builder import SecurePromptBuilder

        tools = get_tools_for_agent(
            "implementer_agent", settings.blind_review_implementer_capabilities
        )
        llm: Any = ProviderRegistry(settings=settings).get_llm(
            model=settings.blind_review_implementer_model or None
        )
        if tools and hasattr(llm, "bind_tools"):
            llm = llm.bind_tools(tools)

        user = spec_artifact
        if prior_issues:
            rendered = "\n".join(f"- [{i.severity}] {i.description}" for i in prior_issues)
            user = f"{spec_artifact}\n\n## Reviewer issues to address\n{rendered}"

        prompt = SecurePromptBuilder().build(system=_SYSTEM, user=user)
        response = await llm.ainvoke(
            [
                SystemMessage(content=prompt[0]["content"]),
                HumanMessage(content=prompt[1]["content"]),
            ]
        )
        return str(response.content)

    return implementer_fn


def _build_default_interceptor() -> ActionInterceptor:
    """Lazily build the default ActionInterceptor (mirrors SwarmWorker)."""
    from prismal.security.action_interceptor import ActionInterceptor
    from prismal.security.audit import AuditLogger
    from prismal.security.permissions import PermissionManager

    return ActionInterceptor(
        permission_manager=PermissionManager(),
        audit_logger=AuditLogger(),
    )


def make_implementer_agent_node(
    implementer_fn: ImplementerFn | None = None,
    *,
    settings: Settings | None = None,
    interceptor: ActionInterceptor | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node: spec_artifact (+ prior issues) -> implementation_artifact.

    Args:
        implementer_fn: Injected ``(spec, issues) -> artifact`` coroutine.
            ``None`` wires the default (role-scoped tools + configured model).
        settings: Optional settings override; ``None`` resolves ``get_settings()``.
        interceptor: Optional ``ActionInterceptor`` (dependency injection for
            tests / multi-tenant hosts); ``None`` builds the default lazily.

    Returns:
        An async node writing
        ``state["metadata"]["blind_review"]["implementation_artifact"]``.
    """

    async def implementer_agent_node(state: dict[str, Any]) -> dict[str, Any]:
        with otel.start_span("blind_review.implement") as span:
            span.set_attribute("prismal.subgraph", "blind_review_pipeline")
            span.set_attribute("prismal.agent", "implementer_agent")

            resolved_settings = settings
            if resolved_settings is None:
                from prismal.core.config import get_settings

                resolved_settings = get_settings()
            fn = implementer_fn or _default_implementer_fn(resolved_settings)
            gate = interceptor or _build_default_interceptor()

            br = dict(state.get("metadata", {}).get("blind_review", {}))
            spec_artifact = str(br.get("spec_artifact", ""))
            prior_issues = _extract_prior_issues(br)

            # Gate the implement action before any file write / code execution.
            await gate.on_tool_start({"name": "blind_review.implement"}, spec_artifact)

            implementation_artifact = await fn(spec_artifact, prior_issues)

            br["implementation_artifact"] = implementation_artifact
            logger.info(
                "blind_review.implementation_written",
                chars=len(implementation_artifact),
                retry=prior_issues is not None,
            )

            return {
                "current_agent": "implementer_agent",
                "messages": [AIMessage(content="Implementation written.")],
                "metadata": {**state.get("metadata", {}), "blind_review": br},
                # Bounds the correction loop: score_gate force-passes once
                # iteration_count >= blind_review_max_iterations.
                "iteration_count": state.get("iteration_count", 0) + 1,
            }

    return implementer_agent_node
