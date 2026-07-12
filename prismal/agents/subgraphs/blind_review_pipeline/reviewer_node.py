"""Blind reviewer node for the Blind Review Pipeline (Phase BRP3, SPEC-BRP-REV-001).

This module is the core of the pipeline's one new invariant: **blindness**. Each
reviewer node reads exactly two fields —
``state["metadata"]["blind_review"]["spec_artifact"]`` and
``["implementation_artifact"]`` — via the private ``_extract_blind_context``
helper, and NEVER indexes ``state["messages"]`` nor the other reviewer's
verdict (RF-BRP-03/04).

Blindness is enforced three ways (ARCHITECTURE.md §4):
  1. Structural — the reviewer_fn signature is ``(spec, artifact) -> report``.
  2. Static — an AST guard test fails CI if this module ever reads a ``messages``
     field off ``state`` (``test_reviewer_blindness_guard.py``).
  3. Runtime — ``BlindnessGuard.assert_no_message_leak`` is called before every
     prompt is built.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.subgraphs.code_review.types import CodeIssue, CodeReviewReport
from prismal.core.exceptions import BlindReviewBlindnessViolationError
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = structlog.get_logger("prismal.subgraphs.blind_review_pipeline.reviewer")
otel = OTelManager()

ReviewerFn = Callable[[str, str], Awaitable[CodeReviewReport]]
# (spec_artifact, implementation_artifact) -> CodeReviewReport

# Substrings that betray a serialized ``AgentState.messages`` list leaking into a
# reviewer prompt (best-effort heuristic — defense in depth, not the primary
# control, which is the narrow input contract).
_LEAK_MARKERS: tuple[str, ...] = (
    "HumanMessage(",
    "AIMessage(",
    "SystemMessage(",
    "ToolMessage(",
    "tool_call_id",
)

_SYSTEM = """You are a Blind Reviewer of the Blind Review Pipeline.

You see ONLY a specification and an implementation artifact — never the original
conversation, never another reviewer's opinion. Independently assess whether the
implementation satisfies the specification. Return ONLY a JSON object matching:

    {
      "summary": "one-line verdict",
      "score": 0.0,                 // 0.0 (unacceptable) .. 1.0 (clean)
      "approved": true,
      "issues": [
        {"severity": "high", "category": "logic", "description": "...",
         "file": "path", "line": 1, "suggestion": "..."}
      ]
    }
"""

__all__ = ["BlindnessGuard", "ReviewerFn", "make_reviewer_node"]


class BlindnessGuard:
    """Runtime backstop for RF-BRP-04 (defense in depth).

    The primary control is the narrow ``(spec, artifact)`` input contract, not
    this heuristic — but a defensive assertion before every LLM call catches a
    future edit that accidentally funnels serialized history into a prompt.
    """

    @staticmethod
    def assert_no_message_leak(*fields: str) -> None:
        """Raise if any of *fields* looks like a serialized message history.

        Raises:
            BlindReviewBlindnessViolationError: When a leak marker is found.
        """
        for field in fields:
            for marker in _LEAK_MARKERS:
                if marker in field:
                    raise BlindReviewBlindnessViolationError(
                        f"reviewer input appears to embed serialized message "
                        f"history (marker {marker!r}); refusing to build the prompt."
                    )


def _extract_blind_context(state: dict[str, Any]) -> tuple[str, str]:
    """Return exactly ``(spec_artifact, implementation_artifact)``.

    The ONLY state accessor a reviewer node body uses. It reads
    ``state["metadata"]["blind_review"]`` and nothing else — no message history,
    no sibling verdict.
    """
    br = state.get("metadata", {}).get("blind_review", {})
    spec_artifact = str(br.get("spec_artifact", ""))
    implementation_artifact = str(br.get("implementation_artifact", ""))
    return spec_artifact, implementation_artifact


def _parse_report(content: str) -> CodeReviewReport:
    """Parse an LLM response into a ``CodeReviewReport`` (lenient, never raises)."""
    try:
        data = json.loads(content)
        issues = [
            CodeIssue(
                severity=i.get("severity", "info"),
                category=i.get("category", "logic"),
                description=str(i.get("description", "")),
                file=str(i.get("file", "")),
                line=i.get("line"),
                suggestion=str(i.get("suggestion", "")),
            )
            for i in data.get("issues", [])
            if isinstance(i, dict)
        ]
        return CodeReviewReport(
            issues=issues,
            summary=str(data.get("summary", "")),
            score=float(data.get("score", 0.0)),
            approved=bool(data.get("approved", False)),
        )
    except (ValueError, TypeError):
        # Unparseable → conservative failing verdict (blocks approval).
        return CodeReviewReport(summary=content[:200], score=0.0, approved=False)


def _default_reviewer_fn(
    role: str, model_id: str | None, capabilities: list[str], settings: Settings
) -> ReviewerFn:
    """Build the default reviewer_fn: role-scoped tools + per-role model via ports."""

    async def reviewer_fn(spec_artifact: str, implementation_artifact: str) -> CodeReviewReport:
        from prismal.agents.tool_registry import get_tools_for_agent
        from prismal.providers.registry import ProviderRegistry
        from prismal.security.prompt_builder import SecurePromptBuilder

        tools = get_tools_for_agent(role, capabilities)
        llm: Any = ProviderRegistry(settings=settings).get_llm(model=model_id)
        if tools and hasattr(llm, "bind_tools"):
            llm = llm.bind_tools(tools)

        user = f"## Specification\n{spec_artifact}\n\n## Implementation\n{implementation_artifact}"
        prompt = SecurePromptBuilder().build(system=_SYSTEM, user=user)
        response = await llm.ainvoke(
            [
                SystemMessage(content=prompt[0]["content"]),
                HumanMessage(content=prompt[1]["content"]),
            ]
        )
        return _parse_report(str(response.content))

    return reviewer_fn


def make_reviewer_node(
    role: Literal["reviewer_a", "reviewer_b"],
    *,
    model_id: str | None,
    capabilities: list[str],
    reviewer_fn: ReviewerFn | None = None,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async, BLIND LangGraph node for one reviewer (SPEC-BRP-REV-001).

    Args:
        role: ``"reviewer_a"`` or ``"reviewer_b"`` — also the tool provider's
            ``agent_name`` and the verdict's metadata key (``{role}_verdict``).
        model_id: Per-role model override; ``None`` uses the registry default.
        capabilities: Role-scoped capability filter for tool resolution.
        reviewer_fn: Injected ``(spec, artifact) -> CodeReviewReport`` coroutine.
            ``None`` wires the default (role-scoped tools + per-role model).
        settings: Optional settings override; ``None`` resolves ``get_settings()``.

    Returns:
        An async node writing
        ``state["metadata"]["blind_review"][f"{role}_verdict"]``.
    """

    async def reviewer_node(state: dict[str, Any]) -> dict[str, Any]:
        with otel.start_span(f"blind_review.review_{role[-1]}") as span:
            span.set_attribute("prismal.subgraph", "blind_review_pipeline")
            span.set_attribute("prismal.agent", role)

            resolved_settings = settings
            if resolved_settings is None:
                from prismal.core.config import get_settings

                resolved_settings = get_settings()
            fn = reviewer_fn or _default_reviewer_fn(
                role, model_id, capabilities, resolved_settings
            )

            spec_artifact, implementation_artifact = _extract_blind_context(state)

            # Runtime backstop before anything reaches the model (RF-BRP-04).
            BlindnessGuard.assert_no_message_leak(spec_artifact, implementation_artifact)

            verdict = await fn(spec_artifact, implementation_artifact)

            br = dict(state.get("metadata", {}).get("blind_review", {}))
            br[f"{role}_verdict"] = verdict
            logger.info(
                "blind_review.verdict_written",
                role=role,
                score=verdict.score,
                approved=verdict.approved,
                issues=len(verdict.issues),
            )

            return {
                "current_agent": role,
                "messages": [AIMessage(content=f"{role} verdict recorded.")],
                "metadata": {**state.get("metadata", {}), "blind_review": br},
            }

    return reviewer_node
