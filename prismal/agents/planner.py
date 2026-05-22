"""Planner sub-agent node.

Specialist agent responsible for decomposing complex, multi-step tasks into an
ordered plan that subsequent specialist agents can execute sequentially, and for
producing software specification documents following the Spec-Driven Design (SDD)
methodology.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from prismal.agents.patterns.reflection import reflection_loop
from prismal.agents.tool_registry import get_tools_for_agent, react_loop
from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.planner")

_SYSTEM_PROMPT = """You are a planning and software design specialist with deep \
expertise in Spec-Driven Design (SDD).

## Purpose
Decompose complex multi-step user requests into ordered, executable plans for
downstream specialist agents, OR produce formal software specification documents
(PRD, API Spec, Tech Design, Data Model, Implementation Plan) when the user asks
to plan, design, or document a feature before it is built. You are the single
entry point for every "think before you code" workflow in LightAgent.

## Input
- `state.messages`: full conversation history; the last HumanMessage contains
  the user's planning request.
- Optional context already collected by prior agents (research notes, file
  contents) present in earlier AIMessage entries.
- Available tools: `spec_driven_design__guide`, `spec_driven_design__read_reference`,
  `write_file`, `read_file`, plus any MCP/skill tools bound at runtime.

## Output
One AIMessage whose text content is EITHER:
1. **Task decomposition mode** — a numbered list where every non-empty line
   begins with a digit and follows EXACTLY this format:
       N. [agent: <agent_name>] <Task description>
   where `<agent_name>` ∈ {researcher, coder, rag_agent, critic, data_analyst,
   file_manager}. The caller parses this list into `task_plan` / `pending_tasks`.
2. **SDD mode** — a complete specification document that matches exactly the
   template structure loaded from `spec_driven_design__read_reference`. When
   persisted, the file path MUST be inside `data/workspace/`.

Never mix the two modes in the same response.

## Success Criteria
The plan/spec is production-ready when ALL of the following hold:
- **Completeness** ≥ 0.85: every explicit sub-goal in the user request is
  addressed by at least one step / section.
- **Actionability**: every step is atomic (one agent, one verb, one artifact)
  OR every spec section is filled with concrete content (no TODO placeholders).
- **Consistency**: step ordering respects data dependencies (research before
  analysis, analysis before coding, coding before review).
- **Agent coverage**: each step targets a valid agent name from the allowlist.
- **SDD depth match**: the number of SDD documents produced matches the risk
  table below (e.g. a complex feature MUST include all 5 documents).

The downstream `reflection_loop()` applies threshold `0.85`; plans scoring
below this are rejected and regenerated with critique feedback.

## Instructions
### Task Decomposition workflow
1. Parse the user request and enumerate all sub-goals.
2. Map each sub-goal to exactly one specialist agent.
3. Order the sub-goals by data dependency (earlier steps feed later ones).
4. Emit the numbered list in the exact format above — no prose before or after.
5. Keep each step atomic: if a step needs two agents, split it.

### Spec-Driven Design workflow
1. If scope is unclear, ask the user a single clarifying question and stop.
2. Classify the work using the risk table below and decide which documents
   are required.
3. Call `spec_driven_design__guide` once to load the SDD methodology.
4. For EACH required document, call `spec_driven_design__read_reference`
   with the matching template filename, then fill it in:
   - PRD → `01-PLANTILLA-PRD.md`
   - API Spec → `02-PLANTILLA-API-SPEC.md`
   - Technical Design → `03-PLANTILLA-TECHNICAL-DESIGN.md`
   - Data Model → `04-PLANTILLA-DATA-MODEL.md`
   - Implementation Plan → `05-PLANTILLA-IMPLEMENTATION-PLAN.md`
   - Filling guide (optional) → `06-GUIA-LLENADO.md`
5. Persist each document via `write_file` ONLY when the user asks to save it.
6. Self-check against the quality checklist before returning.

### Routing heuristic
Decomposition triggers: "plan", "breakdown", "steps", "how would you do X",
"divide in tasks".
SDD triggers: PRD, spec, especificación, diseño técnico, arquitectura,
plan de implementación, modelo de datos, diseño de API, "planificar antes de
codificar", spec-driven, SDD, "planifica el feature", "documenta antes de
construir", "define la arquitectura".

## Background
### Depth proportional to risk (SDD)
| Work Type                    | Documents Needed                         |
|------------------------------|------------------------------------------|
| Bug fix                      | None                                     |
| Internal refactor            | Tech Design lite (decisions + plan)      |
| Simple CRUD + API            | API Spec + Data Model                    |
| Medium feature (1-2 sprints) | PRD + API Spec + Data Model              |
| Complex feature (3+ sprints) | All 5 documents                          |
| New service/microservice     | All 5 documents                          |

### Core quality checks per document
- PRD: every MUST requirement has a verifiable acceptance criterion;
  Out of Scope defined.
- API Spec: a frontend dev can implement without asking questions; all
  errors documented.
- Tech Design: key decisions have documented alternatives; error flows have
  compensations.
- Data Model: every index justified by a critical query; financial data
  uses Decimal128.
- Plan: each phase has verifiable "Done" criteria; task dependencies mapped.

### Available specialist agents (for decomposition)
- `researcher`: Web search, RAG queries, reading files
- `coder`: Writing and executing code
- `rag_agent`: Internal knowledge-base Q&A
- `critic`: Reviewing and improving outputs
- `data_analyst`: SQL queries, DataFrame transforms, charts
- `file_manager`: File read/write operations

## Examples

### Example 1 — Task decomposition (positive)
User: "Investiga qué librerías hay para parsear PDFs en Python y compáralas
en una tabla, luego implementa un ejemplo con la mejor."

Response:
1. [agent: researcher] Busca librerías Python para parseo de PDFs
   (pypdf, pdfplumber, pymupdf, tika) y recopila features y licencia.
2. [agent: data_analyst] Construye una tabla comparativa y recomienda
   la mejor opción con justificación.
3. [agent: coder] Implementa un script que extraiga texto de un PDF
   usando la librería recomendada en data/workspace/pdf_demo.txt.
4. [agent: critic] Revisa el script y la tabla; sugiere mejoras si
   el score < 0.8.

### Example 2 — Task decomposition (negative — what NOT to do)
BAD:
- "First I'll research, then analyze, then code."  ← prose, no numbered steps
- "1. Do everything"                                 ← not atomic, no agent tag
- "1. [agent: magician] Cast a spell"                ← invalid agent name
- "2. [agent: researcher] Research then code it"     ← two verbs in one step

### Example 3 — SDD mode (positive)
User: "Diseña la arquitectura del nuevo módulo de notificaciones por email
(complejidad media, 1-2 sprints)."

Response: produces PRD + API Spec + Data Model, each following the template
loaded via `spec_driven_design__read_reference`, with every MUST requirement
mapped to an acceptance criterion and every DB column typed explicitly.
"""

_PLAN_CRITIQUE_PROMPT = """You are a strict quality reviewer for planner outputs.

You will receive a candidate plan produced by an AI planner agent. Evaluate it
against three criteria, each scored in [0.0, 1.0]:

1. **Completeness** — every explicit sub-goal in the user's request is addressed
   by at least one step / section.
2. **Consistency** — step ordering respects data dependencies (research before
   analysis, analysis before coding, coding before review).
3. **Actionability** — every step is atomic (one agent, one verb, one artifact),
   uses a valid agent name from {researcher, coder, rag_agent, critic,
   data_analyst, file_manager}, and follows the format
   `N. [agent: <agent_name>] <Task description>`. SDD documents must have all
   sections filled with concrete content (no TODO placeholders).

Respond with ONLY a single JSON object — no prose, no markdown fences:
{
  "completeness": <float in [0,1]>,
  "consistency": <float in [0,1]>,
  "actionability": <float in [0,1]>,
  "score": <float in [0,1] = average of the three>,
  "feedback": "<one paragraph explaining the lowest-scoring criterion and what to fix>"
}
"""


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_critique_response(content: str) -> tuple[str, float]:
    """Extract ``(feedback, score)`` from a critique LLM response.

    The critique LLM is instructed to return raw JSON, but real models
    sometimes wrap it in markdown fences or add prose.  This helper finds the
    first JSON object in the content and parses it defensively.  When parsing
    fails the function returns a sentinel score of ``1.0`` so the reflection
    loop does not reject otherwise-valid plans because of a critique
    formatting glitch (the failure is logged at WARNING level).

    Args:
        content: Raw text returned by the critique LLM.

    Returns:
        ``(feedback, score)`` extracted from the JSON payload.
    """
    match = _JSON_OBJECT_RE.search(content)
    if match is None:
        logger.warning("planner_critique_no_json", content_preview=content[:200])
        return ("critique response contained no JSON object", 1.0)
    try:
        data = json.loads(match.group(0))
        score = float(data.get("score", 0.0))
        feedback = str(data.get("feedback", ""))
        return feedback, max(0.0, min(1.0, score))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("planner_critique_parse_failed", error=str(exc))
        return (f"critique response failed to parse: {exc}", 1.0)


async def planner_node(state: AgentState) -> dict[str, object]:
    """Execute the planner sub-agent node with a reflection-wrapped ReAct loop.

    Handles two modes:

    * **Task decomposition** — breaks the request into numbered sub-tasks
      assigned to specialist agents.
    * **Spec-Driven Design** — uses the ``spec_driven_design`` skill tools
      (guide, templates, validator) to generate software specification
      documents (PRD, API Spec, Tech Design, Data Model, Implementation Plan).

    The generated draft is passed through :func:`reflection_loop` so that the
    plan is critiqued for completeness, consistency, and actionability before
    being routed back to the supervisor.  Reflection is bounded by
    ``settings.reflection_default_threshold`` (default ``0.85``) and a hard
    cap of ``2`` iterations (one initial draft + one optional refinement).

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'planner'``,
        new ``messages`` containing the plan or spec, ``task_plan`` as a
        list of step strings (lines starting with a digit), ``pending_tasks``
        set to the same list, ``completed_tasks`` reset to an empty list, and
        ``metadata['planner']`` populated with reflection score and iteration
        count.
    """
    session_id = state.get("session_id")
    logger.debug("planner_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    critique_llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("planner")
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    # Counter and last-response holder mutated by ``_generate_plan`` so we can
    # persist iteration count and recover the original BaseMessage afterwards.
    iteration_count: int = 0
    last_response: BaseMessage | None = None

    async def _generate_plan(
        s: AgentState,
        previous_draft: str | None = None,
        critique: str | None = None,
    ) -> str:
        nonlocal iteration_count, last_response
        iteration_count += 1
        messages: list[BaseMessage] = [
            SystemMessage(content=_SYSTEM_PROMPT),
            *s["messages"],
        ]
        if previous_draft is not None:
            messages.append(
                HumanMessage(
                    content=(
                        "Your previous plan did not pass quality review.\n\n"
                        f"=== Previous draft ===\n{previous_draft}\n\n"
                        f"=== Critique ===\n{critique or '(no feedback provided)'}\n\n"
                        "Produce a revised plan that addresses the critique. "
                        "Keep the same output format."
                    )
                )
            )
        response = cast(
            "BaseMessage",
            await react_loop(
                llm_with_tools,
                tools,
                list(messages),
                agent_name="planner",
                session_id=str(session_id) if session_id else None,
            ),
        )
        last_response = response
        return str(response.content)

    async def _critique_plan(draft: str, _s: AgentState) -> tuple[str, float]:
        critique_response = await critique_llm.ainvoke(
            [
                SystemMessage(content=_PLAN_CRITIQUE_PROMPT),
                HumanMessage(content=f"Plan to evaluate:\n{draft}"),
            ]
        )
        return _parse_critique_response(str(critique_response.content))

    settings = get_settings()
    final_plan, score = await reflection_loop(
        generate_fn=_generate_plan,
        critique_fn=_critique_plan,
        state=state,
        threshold=settings.reflection_default_threshold,
        max_iterations=2,
    )

    # Recover the BaseMessage produced during the winning iteration so we keep
    # tool-call metadata intact when appending to the conversation history.
    response_msg: BaseMessage
    if last_response is not None and str(last_response.content) == final_plan:
        response_msg = last_response
    else:
        # Fallback: synthesise a plain AIMessage when the best draft does not
        # match the most recent response (rare — only when iteration 1 scored
        # higher than iteration 2 and the loop returned the older draft).
        from langchain_core.messages import AIMessage  # local import to avoid cycle

        response_msg = AIMessage(content=final_plan)

    raw_content: str = final_plan
    task_plan: list[str] = [
        line.strip()
        for line in raw_content.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]

    logger.info(
        "planner_complete",
        session_id=session_id,
        task_count=len(task_plan),
        reflection_score=score,
        reflection_iterations=iteration_count,
    )

    planner_meta = {
        "reflection_score": score,
        "reflection_iterations": iteration_count,
    }
    return {
        "current_agent": "planner",
        "messages": [response_msg],
        "task_plan": task_plan,
        "pending_tasks": task_plan,
        "completed_tasks": [],
        "metadata": {**state.get("metadata", {}), "planner": planner_meta},
    }


__all__ = ["planner_node"]
