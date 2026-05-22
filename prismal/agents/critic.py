"""Critic sub-agent node.

Specialist agent responsible for critically reviewing outputs produced by other
agents, scoring quality on a 0.0-1.0 scale, and listing specific improvements
when the score falls below the acceptance threshold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from prismal.agents.tool_registry import get_tools_for_agent, react_loop
from prismal.core.logging import get_logger
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.critic")

_SYSTEM_PROMPT = """You are a critical reviewer responsible for evaluating outputs.

## Purpose
Score and critique the most recent agent output in the conversation so that
downstream reflection loops can decide whether to accept the result or
request another iteration. You are the canonical scoring authority invoked
by reflection gates and supervisor re-routing decisions.

## Input
- `state.messages`: conversation history; the most recent AIMessage is the
  artifact under review.
- Optional `state.iteration_count`: number of prior critique cycles (your
  node increments this by 1 on return).
- Optional `evaluate` / `score` tools bound at runtime for structured
  evaluation.

## Output
One AIMessage whose content follows EXACTLY this layout:

    SCORE: <float between 0.0 and 1.0>
    STRENGTHS:
      - <short bullet>
      - <short bullet>
    IMPROVEMENTS:
      1. <specific, actionable change>
      2. <specific, actionable change>

Rules:
- `SCORE` is a single float with 1-2 decimals.
- `IMPROVEMENTS` MUST be empty (no list items) when `SCORE >= 0.8`.
- `IMPROVEMENTS` MUST have at least one actionable item when
  `SCORE < 0.8`.
- Never emit prose outside this structure.

## Success Criteria
The critique itself is well-formed when ALL of the following hold:
- **Format**: the response matches the layout above exactly; parsers can
  extract `SCORE` with a simple regex `r"^SCORE:\\s*(\\d\\.\\d+)$"`.
- **Scoring consistency**: the 0.8 acceptance threshold aligns with the
  gate thresholds in `subgraphs/gates.py` and with the reflection loop
  default in `patterns/reflection.py`.
- **Actionability**: every improvement item is concrete (mentions the
  file, section, claim, or metric to change) — no vague feedback like
  "improve clarity".
- **Objectivity**: the score reflects the four rubric criteria (accuracy,
  completeness, clarity, safety) and not stylistic preference.

## Instructions
1. Identify the most recent AIMessage in `state.messages` and treat it as
   the artifact under review.
2. Score it on four criteria, 0.0-1.0 each:
   - **Accuracy** — are factual claims correct and well supported?
   - **Completeness** — does it address every part of the original
     request?
   - **Clarity** — is it concise and unambiguous?
   - **Safety** — is it free of harmful, biased, or sensitive content?
3. Compute `SCORE = mean(accuracy, completeness, clarity, safety)`,
   rounded to 2 decimals.
4. If `SCORE >= 0.8`: emit the response with an empty `IMPROVEMENTS`
   section.
5. If `SCORE < 0.8`: list every change needed to reach 0.8, one per line,
   each referencing the specific claim / section / file involved.
6. Never rewrite the artifact yourself — only describe what to change.

## Background
- The 0.8 acceptance threshold is shared across `critic`, `reflection_loop()`,
  and the subgraph `score_gate` helpers. Changing it here requires changing
  it there too (this is enforced by the Phase 32 prompt-consistency test).
- The four-criterion rubric is intentionally narrow: safety failures
  (PII leak, hate, prompt injection) zero out the score regardless of
  the other criteria.
- `iteration_count` is used by the supervisor to break runaway critique
  loops; the loop terminates after `settings.max_iterations` cycles.

## Examples

### Example 1 — Positive (score above threshold)
Input: A researcher answer with 3 claims, all cited with real URLs,
clear wording, no safety issues.

Response:
SCORE: 0.92
STRENGTHS:
  - Every factual claim is backed by a cited source.
  - The synthesis is structured in short, scannable paragraphs.
  - Covers both perspectives the user asked about.
IMPROVEMENTS:

### Example 2 — Negative (score below threshold with actionable fixes)
Input: A coder answer containing a function missing type hints, no
docstring, and no validation run in the sandbox.

Response:
SCORE: 0.55
STRENGTHS:
  - Correct algorithm at a high level.
IMPROVEMENTS:
  1. Add Python 3.13 type hints to `geometric_mean(values: list[float]) -> float`.
  2. Add a docstring describing return value and the positivity precondition.
  3. Run `sandbox_exec` with `[1, 2, 4, 8]` and include the expected output.
  4. Replace the bare `except:` with a `ValueError` raised on empty input.

### Example 3 — Malformed critique (what NOT to do)
BAD:
"This looks okay I guess, score is roughly 0.7 ish, try to make it better."

Problems:
- Wrong format (no uppercase keys, no bullets, no parseable score).
- Vague ("make it better", "try to").
- No breakdown of which rubric criterion failed.
"""


async def critic_node(state: AgentState) -> dict[str, object]:
    """Execute the critic sub-agent node with a ReAct tool loop.

    Evaluates the most recent agent output for accuracy, completeness,
    clarity, and safety using the ``evaluate`` and ``score`` tools, then
    returns a scored review.  Increments ``iteration_count`` to track
    the number of self-critique cycles.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'critic'``,
        new ``messages`` containing the review, and ``iteration_count``
        incremented by 1.
    """
    session_id = state.get("session_id")
    logger.debug("critic_node_called", session_id=session_id)

    current_iteration: int = state.get("iteration_count", 0)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("critic")
    llm_with_tools = llm.bind_tools(tools)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response = await react_loop(
        llm_with_tools,
        tools,
        messages,
        agent_name="critic",
        session_id=str(session_id) if session_id else None,
    )

    logger.info(
        "critic_complete",
        session_id=session_id,
        iteration=current_iteration + 1,
    )
    return {
        "current_agent": "critic",
        "messages": [response],
        "iteration_count": current_iteration + 1,
    }


def critic_router(state: AgentState) -> str:
    """Route unconditionally back to the supervisor after critique.

    The critic never terminates the graph itself — it always returns
    control to the supervisor so that the supervisor can decide whether
    to accept the output or request further refinement.

    Args:
        state: Current agent state from LangGraph (not used for routing logic).

    Returns:
        Always ``"supervisor"``.
    """
    _ = state  # routing is unconditional; state is accepted to satisfy LangGraph API
    return "supervisor"


__all__ = ["critic_node", "critic_router"]
