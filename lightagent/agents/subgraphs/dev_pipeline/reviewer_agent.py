"""Reviewer agent node for the dev_pipeline subgraph.

Produces a :class:`~lightagent.agents.subgraphs.artifacts.ReviewResult` with
a score in [0.0, 1.0].  When score < 0.8 the approval gate routes back to
the developer node.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import ReviewResult
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.reviewer")

_SYSTEM = (
    "You are a Senior Code Reviewer. Review all pipeline artifacts.\n"
    "Score the implementation from 0.0 (poor) to 1.0 (excellent).\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "score": 0.85,\n  "approved": true,\n  "strengths": ["clean code"],\n'
    '  "improvements": ["add more comments"],\n  "blocking_issues": []\n}'
)


async def reviewer_agent_node(state: AgentState) -> dict[str, Any]:
    """Review all pipeline artifacts and produce a ReviewResult.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ReviewResult in metadata.
    """
    dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))

    llm = ProviderRegistry().get_llm()
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=f"Pipeline artifacts:\n{json.dumps(dp, indent=2)}"),
    ]
    response = await llm.ainvoke(messages)
    content = str(response.content)

    try:
        data = json.loads(content)
        result = ReviewResult.model_validate(data)
    except Exception:
        result = ReviewResult(score=0.0, approved=False, strengths=[], improvements=[])

    dp["review_result"] = result.model_dump()
    logger.info("reviewer.result", score=result.score, approved=result.approved)

    verdict = "APPROVED" if result.approved else "NEEDS REVISION"
    return {
        "current_agent": "reviewer",
        "messages": [
            AIMessage(content=f"Review score: {result.score:.2f} | {verdict}")
        ],
        "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
    }
