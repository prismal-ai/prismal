"""Parallel research fan-out / fan-in pattern (Phase 34).

Implements the map-reduce execution path for research tasks, using
:func:`~lightagent.agents.patterns.parallel.make_parallel_dispatcher` to fan
out one ``Send`` per pending task to a worker node, then aggregate the
partial results in :func:`research_aggregator_node`.

Graph topology added by Phase 34::

    supervisor
        │  (next_agent == "parallel_researcher")
        ▼
    parallel_researcher  ──[dispatcher conditional edges]──┐
                                                            │
                            ┌───────────────────────────────┘
                            │  one Send per pending_tasks item
                            ▼
                  parallel_researcher_worker  (runs in parallel)
                            │  (every parallel invocation appends to
                            │   state.parallel_results via reducer)
                            ▼
                  research_aggregator
                            │
                            ▼
                       supervisor

The worker reuses the same research tool registry as the sequential
``researcher`` node so behaviour stays consistent across both code paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from lightagent.agents.patterns.parallel import make_parallel_dispatcher
from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.parallel_research")

_PARALLEL_RESEARCH_SYSTEM = """You are a focused research worker.

You have been dispatched as one of several parallel researchers, each
investigating a single sub-task in isolation. Your job:

1. Read the task description in the most recent HumanMessage.
2. Use the available web search and document tools to gather relevant facts.
3. Return a concise, self-contained summary of what you found.

Do NOT attempt to coordinate with other workers — your output will be
merged with theirs by an aggregator node downstream. Stay strictly within
the bounds of your assigned sub-task.
"""

# Per-worker iteration cap — kept small to bound the cost of fan-out.
_MAX_WORKER_ITERATIONS: int = 3


async def parallel_researcher_node(state: AgentState) -> dict[str, Any]:
    """Pass-through node that anchors the conditional dispatcher edges.

    LangGraph requires conditional edges to originate from a registered
    node.  This function is that anchor: it sets ``current_agent`` so the
    UI / traces can show that the parallel branch is active and returns
    no other state mutations.  The actual fan-out happens in the dispatcher
    function attached via ``add_conditional_edges``.

    Args:
        state: Current agent state from LangGraph (unused).

    Returns:
        Partial state update with ``current_agent`` set to
        ``"parallel_researcher"``.
    """
    pending = state.get("pending_tasks", [])
    logger.debug(
        "parallel_researcher_node_called",
        session_id=state.get("session_id"),
        pending_count=len(pending),
    )
    return {"current_agent": "parallel_researcher"}


async def parallel_researcher_worker(state: AgentState) -> dict[str, Any]:
    """Execute a single research sub-task and append the result.

    Reads the task assigned to this worker from ``state["_task"]`` (set by
    :func:`make_parallel_dispatcher`), runs a bounded ReAct loop with the
    researcher tool registry, and appends a dict to
    ``state["parallel_results"]``.  Because ``parallel_results`` uses an
    ``operator.add`` reducer, all parallel invocations of this worker
    contribute their findings to the same merged list.

    Args:
        state: Current agent state.  Must contain ``_task`` populated by
            the dispatcher.

    Returns:
        Partial state update appending one dict to ``parallel_results``.
    """
    raw_task: Any = state.get("_task")
    task: str = str(raw_task) if raw_task is not None else ""
    session_id = state.get("session_id")
    logger.debug(
        "parallel_researcher_worker_called",
        session_id=session_id,
        task_preview=task[:80],
    )

    if not task:
        # Defensive: dispatcher should always populate ``_task`` but skip
        # gracefully if it did not.
        return {
            "parallel_results": [
                {
                    "agent": "parallel_researcher_worker",
                    "task": "",
                    "result": "(no task assigned)",
                }
            ]
        }

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("researcher")
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    messages: list[BaseMessage] = [
        SystemMessage(content=_PARALLEL_RESEARCH_SYSTEM),
        HumanMessage(content=task),
    ]
    response = cast(
        "BaseMessage",
        await react_loop(
            llm_with_tools,
            tools,
            list(messages),
            agent_name="parallel_researcher_worker",
            max_iterations=_MAX_WORKER_ITERATIONS,
            session_id=str(session_id) if session_id else None,
        ),
    )

    return {
        "parallel_results": [
            {
                "agent": "parallel_researcher_worker",
                "task": task,
                "result": str(response.content),
            }
        ]
    }


async def research_aggregator_node(state: AgentState) -> dict[str, Any]:
    """Consolidate ``parallel_results`` into a single AIMessage.

    Runs once after every parallel worker has finished (LangGraph
    synchronises fan-in automatically when edges from a Send-fanned-out
    node converge on a single downstream node).  Builds a markdown
    summary that lists each task and its findings, then routes control
    back to the supervisor.

    Args:
        state: Current agent state with populated ``parallel_results``.

    Returns:
        Partial state update with ``current_agent`` set to
        ``"research_aggregator"`` and a new AIMessage in ``messages``.
    """
    raw_results: Any = state.get("parallel_results", [])
    results: list[dict[str, Any]] = (
        raw_results if isinstance(raw_results, list) else []
    )
    session_id = state.get("session_id")
    logger.info(
        "research_aggregator_called",
        session_id=session_id,
        result_count=len(results),
    )

    if not results:
        summary = "Parallel research finished but produced no results."
    else:
        sections: list[str] = ["## Parallel research summary", ""]
        for idx, item in enumerate(results, start=1):
            task = str(item.get("task", "(unknown task)"))
            finding = str(item.get("result", "(no result)"))
            sections.append(f"### {idx}. {task}")
            sections.append(finding)
            sections.append("")
        summary = "\n".join(sections).rstrip()

    return {
        "current_agent": "research_aggregator",
        "messages": [AIMessage(content=summary)],
    }


# Pre-built dispatcher used by ``graph.py`` to wire the conditional edges from
# ``parallel_researcher`` to ``parallel_researcher_worker``.  The cap honours
# ``settings.parallel_max_workers`` at dispatch time (see
# ``make_parallel_dispatcher`` for the cap interaction).
research_dispatcher = make_parallel_dispatcher(
    tasks_field="pending_tasks",
    worker_node="parallel_researcher_worker",
    max_workers=10,
    on_empty="research_aggregator",
)


__all__ = [
    "parallel_researcher_node",
    "parallel_researcher_worker",
    "research_aggregator_node",
    "research_dispatcher",
]
