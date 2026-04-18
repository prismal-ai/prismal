# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""Product Owner agent node for the dev_pipeline subgraph.

Generates a :class:`~lightagent.agents.subgraphs.artifacts.UserStory`
from the user's feature request.  The story is stored under
``state["metadata"]["dev_pipeline"]["user_story"]``.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from lightagent.agents.patterns.reflection import reflection_loop
from lightagent.agents.subgraphs.artifacts import UserStory
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.po_agent")
otel = OTelManager()

# 5 of 6 INVEST criteria must pass — matches AC-035-5.
_INVEST_THRESHOLD = 0.83

_INVEST_CRITIQUE_PROMPT = """You are a strict Agile coach reviewing a UserStory.

Evaluate the candidate UserStory JSON against the 6 INVEST criteria. Each
criterion is scored 0 or 1 (binary):

- Independent: can be developed without dependency on other stories?
- Negotiable: avoids over-specification of implementation details?
- Valuable: delivers clear user/business value?
- Estimable: has enough detail for rough estimation?
- Small: fits within a single sprint?
- Testable: every acceptance criterion uses Given/When/Then and is
  objectively verifiable?

Compute `overall_score = sum(scores) / 6`.

Respond with ONLY a single JSON object — no prose, no markdown fences:
{
  "scores": {"I": 0|1, "N": 0|1, "V": 0|1, "E": 0|1, "S": 0|1, "T": 0|1},
  "feedback": "<one paragraph listing failed criteria and how to fix them>",
  "overall_score": <float in [0,1]>
}
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_invest_response(content: str) -> tuple[str, float]:
    """Extract ``(feedback, overall_score)`` from an INVEST critique response.

    Defensively parses the first JSON object in ``content``.  When parsing
    fails the function returns a sentinel score of ``1.0`` so the reflection
    loop does not reject otherwise-valid stories because of critique
    formatting glitches (the failure is logged at WARNING level).

    Args:
        content: Raw text returned by the critique LLM.

    Returns:
        ``(feedback, overall_score)`` extracted from the JSON payload.
    """
    match = _JSON_OBJECT_RE.search(content)
    if match is None:
        logger.warning("po_invest_no_json", content_preview=content[:200])
        return ("INVEST critique returned no JSON object", 1.0)
    try:
        data = json.loads(match.group(0))
        score = float(data.get("overall_score", 0.0))
        feedback = str(data.get("feedback", ""))
        return feedback, max(0.0, min(1.0, score))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("po_invest_parse_failed", error=str(exc))
        return (f"INVEST critique failed to parse: {exc}", 1.0)


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
    """Generate a UserStory artifact and refine it via INVEST reflection.

    The story is generated by an LLM, then passed through
    :func:`reflection_loop` with an INVEST-based critique function.  Stories
    that fail to satisfy at least 5 of the 6 INVEST criteria (overall score
    ``>= 0.83``) are refined with explicit critique feedback, up to ``2``
    iterations.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``current_agent`` and updated ``metadata``
        (containing both the validated ``user_story`` and the reflection
        score / iteration count under ``dev_pipeline``).
    """
    with otel.start_span("dev_pipeline.po_agent") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "po_agent")

        llm = ProviderRegistry().get_llm()
        critique_llm = ProviderRegistry().get_llm()

        iteration_count: int = 0

        async def _generate_story(
            s: AgentState,
            previous_draft: str | None = None,
            critique: str | None = None,
        ) -> str:
            nonlocal iteration_count
            iteration_count += 1
            messages: list[BaseMessage] = [
                SystemMessage(content=_SYSTEM),
                *list(s["messages"][-3:]),
            ]
            if previous_draft is not None:
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous UserStory failed the INVEST review.\n\n"
                            f"=== Previous JSON ===\n{previous_draft}\n\n"
                            f"=== INVEST critique ===\n{critique or '(no feedback)'}\n\n"
                            "Produce a revised UserStory JSON that addresses every "
                            "failing INVEST criterion. Return JSON only — no prose."
                        )
                    )
                )
            response = await llm.ainvoke(messages)
            return str(response.content)

        async def _critique_story(draft: str, _s: AgentState) -> tuple[str, float]:
            critique_response = await critique_llm.ainvoke(
                [
                    SystemMessage(content=_INVEST_CRITIQUE_PROMPT),
                    HumanMessage(content=f"UserStory to evaluate:\n{draft}"),
                ]
            )
            return _parse_invest_response(str(critique_response.content))

        final_content, score = await reflection_loop(
            generate_fn=_generate_story,
            critique_fn=_critique_story,
            state=state,
            threshold=_INVEST_THRESHOLD,
            max_iterations=2,
        )

        try:
            data = json.loads(final_content)
            story = UserStory.model_validate(data)
        except Exception:
            story = UserStory(id="s1", title="Feature", description=final_content)

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        dp["user_story"] = story.model_dump()
        dp["po_agent"] = {
            "reflection_score": score,
            "reflection_iterations": iteration_count,
        }

        logger.info(
            "po_agent.story_created",
            story_id=story.id,
            title=story.title,
            reflection_score=score,
            reflection_iterations=iteration_count,
        )
        return {
            "current_agent": "po_agent",
            "messages": [AIMessage(content=f"UserStory created: {story.title}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
