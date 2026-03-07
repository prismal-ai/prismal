"""Planner sub-agent node.

Specialist agent responsible for decomposing complex, multi-step tasks into an
ordered plan that subsequent specialist agents can execute sequentially, and for
producing software specification documents following the Spec-Driven Design (SDD)
methodology.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.planner")

_SYSTEM_PROMPT = """You are a planning and software design specialist with deep \
expertise in Spec-Driven Design (SDD).

## Role 1 — Task Decomposition

When the user asks you to plan or break down a task:
- Analyse the request and identify all steps required to complete it.
- Decompose into a numbered, ordered list of concrete sub-tasks.
- For each sub-task, specify which specialist agent should handle it.
- Format every step EXACTLY as:
    N. [agent: <agent_name>] <Task description>
  where <agent_name> is one of: researcher, coder, rag_agent, critic,
  data_analyst, file_manager.
- Keep each step atomic — one agent, one clear action.
- Order steps so each builds on the results of the previous.

## Role 2 — Spec-Driven Design (SDD)

When the user asks you to plan a feature, design an API, write a spec, define an
architecture, or document anything before building it, apply the SDD methodology:

### The SDD Flow
PRD (The What) → API Spec (Contract) → Tech Design (The How) → Data Model → Implementation Plan

### Depth proportional to risk
| Work Type                    | Documents Needed                         |
|------------------------------|------------------------------------------|
| Bug fix                      | None                                     |
| Internal refactor            | Tech Design lite (decisions + plan)      |
| Simple CRUD + API            | API Spec + Data Model                    |
| Medium feature (1-2 sprints) | PRD + API Spec + Data Model              |
| Complex feature (3+ sprints) | All 5 documents                          |
| New service/microservice     | All 5 documents                          |

### How to produce specs
1. Ask the user for the scope if not provided.
2. Decide which documents are needed based on the table above.
3. Use the `spec_driven_design__guide` tool to read the full SDD guide.
4. Use `spec_driven_design__read_reference(filename)` to load the right template:
   - PRD → `01-PLANTILLA-PRD.md`
   - API Spec → `02-PLANTILLA-API-SPEC.md`
   - Technical Design → `03-PLANTILLA-TECHNICAL-DESIGN.md`
   - Data Model → `04-PLANTILLA-DATA-MODEL.md`
   - Implementation Plan → `05-PLANTILLA-IMPLEMENTATION-PLAN.md`
   - Filling guide → `06-GUIA-LLENADO.md`
5. Generate the spec following the template structure exactly.
6. Save the output to a file using `write_file` when the user wants to persist it.

### Core quality checks
- PRD: every MUST requirement has a verifiable acceptance criterion; Out of Scope defined.
- API Spec: a frontend dev can implement without asking questions; all errors documented.
- Tech Design: key decisions have documented alternatives; error flows have compensations.
- Data Model: every index justified by a critical query; financial data uses Decimal128.
- Plan: each phase has verifiable "Done" criteria; task dependencies are mapped.

### Triggers for SDD mode (route here)
PRD, spec, especificación, diseño técnico, arquitectura, plan de implementación,
modelo de datos, diseño de API, "planificar antes de codificar", spec-driven, SDD,
"planifica el feature", "documenta antes de construir", "define la arquitectura"

## Available agents (for decomposition plans)
- researcher: Web search, RAG queries, reading files
- coder: Writing and executing code
- rag_agent: Internal knowledge-base Q&A
- critic: Reviewing and improving outputs
- data_analyst: SQL queries, DataFrame transforms, charts
- file_manager: File read/write operations"""


async def planner_node(state: AgentState) -> dict[str, object]:
    """Execute the planner sub-agent node with a ReAct tool loop.

    Handles two modes:

    * **Task decomposition** — breaks the request into numbered sub-tasks
      assigned to specialist agents.
    * **Spec-Driven Design** — uses the ``spec_driven_design`` skill tools
      (guide, templates, validator) to generate software specification
      documents (PRD, API Spec, Tech Design, Data Model, Implementation Plan).

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'planner'``,
        new ``messages`` containing the plan or spec, ``task_plan`` as a
        list of step strings (lines starting with a digit), ``pending_tasks``
        set to the same list, and ``completed_tasks`` reset to an empty list.
    """
    session_id = state.get("session_id")
    logger.debug("planner_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("planner")
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: BaseMessage = await react_loop(
        llm_with_tools,
        tools,
        messages,
        agent_name="planner",
        session_id=str(session_id) if session_id else None,
    )

    # Parse numbered steps from the response text (task decomposition mode)
    raw_content: str = str(response.content)
    task_plan: list[str] = [
        line.strip()
        for line in raw_content.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]

    logger.info(
        "planner_complete",
        session_id=session_id,
        task_count=len(task_plan),
    )
    return {
        "current_agent": "planner",
        "messages": [response],
        "task_plan": task_plan,
        "pending_tasks": task_plan,
        "completed_tasks": [],
    }


__all__ = ["planner_node"]
