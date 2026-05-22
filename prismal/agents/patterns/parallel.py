"""Map-Reduce parallel execution pattern (SPEC-036 / Phase 34).

This module implements a reusable fan-out / fan-in pattern built on top of
LangGraph's :class:`~langgraph.types.Send` primitive.  The
:func:`make_parallel_dispatcher` factory returns a node-shaped callable that
reads a list of independent tasks from ``state`` and emits one ``Send`` per
task targeting the same worker node.  LangGraph then executes the worker
invocations concurrently and merges their partial state updates via the
``operator.add`` reducers declared on
:class:`~lightagent.agents.state.AgentState` (see Phase 34 / T-309).

Example::

    from langgraph.graph import StateGraph
    from prismal.agents.patterns.parallel import make_parallel_dispatcher
    from prismal.agents.state import AgentState

    research_dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        max_workers=10,
        on_empty="aggregator",
    )

    graph = StateGraph(AgentState)
    graph.add_node("researcher", researcher_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_conditional_edges("research_dispatcher", research_dispatcher)

The dispatcher honours two safety controls:

* ``max_workers`` is hard-capped by ``settings.parallel_max_workers`` so a
  caller cannot exceed the operator-configured ceiling even by passing a
  larger value.
* When ``settings.parallel_enabled`` is ``False`` the dispatcher routes every
  invocation to ``on_empty`` regardless of the task list, which lets operators
  disable parallelism globally for debugging without modifying agent code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from langgraph.types import Send

from prismal.core.config import get_settings
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.patterns.parallel")

DispatcherFn = Callable[["AgentState"], "list[Send] | str"]
"""Signature returned by :func:`make_parallel_dispatcher`.

The function accepts a LangGraph state and returns either a list of
``Send`` objects (one per parallel worker invocation) or a string node name
when there is nothing to dispatch — both forms are valid return types for
``StateGraph.add_conditional_edges``.
"""


def make_parallel_dispatcher(
    tasks_field: str,
    worker_node: str,
    max_workers: int = 10,
    on_empty: str = "__end__",
    task_key: str = "_task",
) -> DispatcherFn:
    """Build a LangGraph dispatcher that fans out tasks via :class:`Send`.

    The returned callable is a synchronous function suitable for use with
    ``StateGraph.add_conditional_edges``.  When invoked it reads
    ``state[tasks_field]`` and produces one ``Send`` per task, copying the
    parent state into each child invocation and inserting the per-task value
    under ``state[task_key]`` so the worker node can access it.

    Args:
        tasks_field: Name of the state key holding the list of tasks to fan
            out.  When the key is missing or empty the dispatcher routes to
            ``on_empty`` instead of raising.
        worker_node: Name of the registered LangGraph node that will be
            invoked once per task.
        max_workers: Per-call cap on the number of parallel ``Send`` objects
            emitted.  Hard-capped by ``settings.parallel_max_workers``;
            tasks beyond the cap are silently dropped (the operator-visible
            ceiling always wins).
        on_empty: Name of the node to route to when the task list is empty
            *or* when ``settings.parallel_enabled`` is ``False``.  Defaults to
            ``"__end__"`` so an unconfigured graph terminates cleanly.
        task_key: Name of the state key under which each individual task
            value is exposed to the worker.  Defaults to ``"_task"``.

    Returns:
        A dispatcher callable conforming to :data:`DispatcherFn`.

    Raises:
        ValueError: If ``max_workers`` is less than ``1`` or ``worker_node``
            / ``tasks_field`` are empty strings.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers}")
    if not worker_node:
        raise ValueError("worker_node must be a non-empty node name")
    if not tasks_field:
        raise ValueError("tasks_field must be a non-empty state key")

    # NOTE: the inner annotation uses ``dict[str, Any]`` instead of
    # ``AgentState`` so that LangGraph's ``typing.get_type_hints()`` call in
    # ``add_conditional_edges`` resolves cleanly — ``AgentState`` lives behind
    # ``TYPE_CHECKING`` in this module so the forward reference would fail at
    # runtime.  At dispatch time the dict-shape is identical to the TypedDict.
    def dispatcher(state: dict[str, Any]) -> list[Send] | str:
        settings = get_settings()
        if not settings.parallel_enabled:
            logger.debug(
                "parallel_dispatcher_disabled",
                worker_node=worker_node,
                on_empty=on_empty,
            )
            return on_empty

        tasks_raw: Any = state.get(tasks_field, [])
        if not isinstance(tasks_raw, list):
            logger.warning(
                "parallel_dispatcher_invalid_tasks_type",
                tasks_field=tasks_field,
                actual_type=type(tasks_raw).__name__,
            )
            return on_empty

        tasks: list[Any] = tasks_raw
        if not tasks:
            logger.debug(
                "parallel_dispatcher_empty",
                tasks_field=tasks_field,
                on_empty=on_empty,
            )
            return on_empty

        effective_max = min(max_workers, settings.parallel_max_workers)
        limited = tasks[:effective_max]

        if len(tasks) > effective_max:
            logger.warning(
                "parallel_dispatcher_truncated",
                tasks_field=tasks_field,
                requested=len(tasks),
                cap=effective_max,
            )

        sends = [Send(worker_node, {**state, task_key: task}) for task in limited]
        logger.debug(
            "parallel_dispatcher_fanout",
            worker_node=worker_node,
            count=len(sends),
        )
        return sends

    dispatcher.__name__ = f"parallel_dispatcher_{worker_node}"
    return cast("DispatcherFn", dispatcher)


__all__ = ["DispatcherFn", "make_parallel_dispatcher"]
