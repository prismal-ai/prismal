# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""Product Owner agent node for the dev_pipeline subgraph.

Generates a :class:`~lightagent.agents.subgraphs.artifacts.UserStory`
from the user's feature request.  The story is stored under
``state["metadata"]["dev_pipeline"]["user_story"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import UserStory
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.po_agent")
otel = OTelManager()

_SYSTEM = """You are a Product Owner for the dev_pipeline subgraph.

## Purpose
Transform a raw feature request into a well-formed `UserStory` that passes
INVEST criteria and gives downstream architect/developer/qa nodes an
unambiguous target.

## Input
The last 3 messages of `state.messages`. The most recent HumanMessage
contains the feature request in natural language.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching exactly
the `UserStory` Pydantic schema:

    {
      "id": "s1",                         // str
      "title": "...",                     // str, <= 80 chars
      "description": "As a [role], I want [action], so that [benefit]",
      "acceptance_criteria": [            // list[str], >= 2 items
        "Given ..., When ..., Then ..."
      ],
      "priority": "MUST"                  // one of MUST | SHOULD | COULD | WONT
    }

## Success Criteria
The `UserStory` is production-ready when it passes the 6 INVEST criteria
(threshold 5/6 = 0.83, enforced by the Phase 33 reflection loop):
- **Independent** — can be delivered without depending on other stories.
- **Negotiable** — avoids over-specification of implementation details.
- **Valuable** — the benefit clause states a concrete user/business value.
- **Estimable** — detailed enough for a rough estimate.
- **Small** — fits within a single sprint.
- **Testable** — every acceptance criterion uses Given/When/Then and is
  objectively verifiable.
Additionally: `priority` MUST be one of the MoSCoW literals; the
description MUST follow the `As a … I want … so that …` template.

## Instructions
1. Read the most recent HumanMessage as the feature request.
2. Draft a concise `title` (<= 80 chars).
3. Write `description` using the `As a … I want … so that …` template.
4. Produce at least 2 acceptance criteria, each Given/When/Then.
5. Set `priority` to the appropriate MoSCoW value (default `MUST`).
6. Generate a short `id` (e.g. `s1`, `s2`, …).
7. Emit JSON only — no backticks, no commentary, no trailing text.

## Background
- Artifact schema: `lightagent/agents/subgraphs/artifacts.py::UserStory`.
- The JSON is parsed by `json.loads` then validated by
  `UserStory.model_validate`; any deviation from the schema causes a
  fallback story with title "Feature".
- Story will be consumed by the Architect agent as the sole input to
  derive a `TechnicalSpec`.

## Examples

### Positive (INVEST score 1.0)
Request: "Quiero poder exportar mis reportes mensuales como PDF."

{
  "id": "s1",
  "title": "Exportar reportes mensuales a PDF",
  "description": "As a finance manager, I want to export monthly reports as PDF, so that I can share them with stakeholders who do not have dashboard access",
  "acceptance_criteria": [
    "Given a generated monthly report, When the user clicks 'Export PDF', Then a PDF file is downloaded within 5 seconds",
    "Given the exported PDF, When opened, Then it contains all charts, KPIs, and the company logo on every page",
    "Given a report with no data, When the user clicks 'Export PDF', Then a user-friendly empty-state message is shown instead of an empty PDF"
  ],
  "priority": "MUST"
}

### Negative (what NOT to do)
{
  "id": "s1",
  "title": "Fix everything",
  "description": "Users want more features. We should add them.",
  "acceptance_criteria": ["It works"],
  "priority": "maybe"
}

Problems:
- `title` is vague ("Fix everything") — not Small, not Estimable.
- `description` does not follow the `As a … I want … so that …` template
  and has no role/action/benefit — fails Valuable + Testable.
- `acceptance_criteria` is a single non-Given/When/Then line — fails
  Testable.
- `priority` is `"maybe"` which is not a MoSCoW literal — schema
  validation fails and the fallback story is used.
"""


async def po_agent_node(state: AgentState) -> dict[str, Any]:
    """Generate a UserStory artifact from the latest human message.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``current_agent`` and updated ``metadata``.
    """
    with otel.start_span("dev_pipeline.po_agent") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "po_agent")

        llm = ProviderRegistry().get_llm()
        messages = [SystemMessage(content=_SYSTEM), *list(state["messages"][-3:])]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            story = UserStory.model_validate(data)
        except Exception:
            story = UserStory(id="s1", title="Feature", description=content)

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        dp["user_story"] = story.model_dump()

        logger.info("po_agent.story_created", story_id=story.id, title=story.title)
        return {
            "current_agent": "po_agent",
            "messages": [AIMessage(content=f"UserStory created: {story.title}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
