"""LLM-Compiler — plan a goal as a DAG of tool tasks and run in parallel.

Implements SPEC-PAT-005. Converts a natural-language goal into a directed
acyclic graph (DAG) of tool-executing tasks. Independent tasks are grouped
into waves and run concurrently via :func:`asyncio.gather`; downstream tasks
reference upstream outputs via the ``$TaskId.output`` placeholder. On task
failure the compiler re-plans (up to ``max_replanning`` times) using the
accumulated results as context, then joins all completed outputs into a
final answer.

DAG validation uses Kahn's algorithm: any cycle, unknown dependency, or
duplicate task id is rejected with :class:`CompilerError` before execution.

Decoupling: the compiler takes three callables — ``plan_fn`` (goal → task
list), ``tool_executor`` (task + prior outputs → output), ``joiner`` (goal +
completed tasks → final answer) — so tests and alternative runtimes can
swap in fakes without touching the scheduler.

Example::

    async def plan_fn(goal, state, previous_results):
        return await llm_planner(goal, previous_results)


    async def tool_executor(task, prior_outputs):
        return await run_tool(task.tool, task.args)


    async def joiner(goal, completed):
        return await llm_joiner(goal, completed)


    compiler = LLMCompiler(
        tools=my_tools,
        plan_fn=plan_fn,
        tool_executor=tool_executor,
        joiner=joiner,
        max_replanning=2,
    )
    result = await compiler.compile_and_run(goal="do it", state=state)
    print(result.final_answer)
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from lightagent.core.config import get_settings
from lightagent.core.exceptions import CompilerError
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from lightagent.core.config import Settings

logger = get_logger("lightagent.agents.patterns.llm_compiler")

_REFERENCE_RE = re.compile(r"\$(\w+)\.output")


TaskStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class TaskNode:
    """One node of the compiler DAG.

    Attributes:
        id: Unique id within the plan (e.g. ``"T1"``).
        description: Human-readable description of what the task does.
        tool: Name of the tool to invoke.
        args: Tool arguments. Values of the form ``"$T1.output"`` are
            substituted with the actual output of task ``T1`` just before
            execution.
        depends_on: IDs of tasks that must complete before this one.
        output: Output of the tool (``None`` until the task runs).
        status: ``pending`` | ``running`` | ``completed`` | ``failed``.
    """

    id: str
    description: str
    tool: str
    args: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    output: Any = None
    status: TaskStatus = "pending"


@dataclass
class CompilerPlan:
    """Validated DAG of :class:`TaskNode` with waves already computed.

    Attributes:
        tasks: All tasks in the plan.
        execution_waves: Lists of task ids grouped by topological wave; each
            inner list is safe to run concurrently.
        goal: The original user goal.
    """

    tasks: list[TaskNode]
    execution_waves: list[list[str]]
    goal: str

    def to_json(self) -> str:
        """Serialise the plan as JSON (human-readable, debug-friendly)."""
        payload = {
            "goal": self.goal,
            "execution_waves": self.execution_waves,
            "tasks": [asdict(t) for t in self.tasks],
        }
        return json.dumps(payload, sort_keys=True, default=str)


@dataclass
class CompilerResult:
    """Outcome of :meth:`LLMCompiler.compile_and_run`.

    Attributes:
        final_answer: Joiner output.
        plan: The final executed plan (includes per-task outputs).
        replanning_count: Number of replans performed (0 on first-try success).
        tasks_succeeded: Count of tasks with status ``completed``.
        tasks_failed: Count of tasks with status ``failed``.
    """

    final_answer: str
    plan: CompilerPlan
    replanning_count: int
    tasks_succeeded: int
    tasks_failed: int


PlanFn = Callable[[str, Any, dict[str, Any] | None], Awaitable[Sequence[TaskNode]]]
"""``(goal, state, previous_results) -> list[TaskNode]``."""

ToolExecutorFn = Callable[[TaskNode, dict[str, Any]], Awaitable[Any]]
"""``(task, prior_outputs_by_id) -> tool output``."""

JoinerFn = Callable[[str, list[TaskNode]], Awaitable[str]]
"""``(goal, completed_tasks) -> final answer string``."""


class LLMCompiler:
    """Plan a goal into a DAG of tool tasks and execute it concurrently.

    Args:
        tools: Catalogue of tools (opaque; forwarded to ``plan_fn``).
        plan_fn: Async callable producing a task list from the goal.
        tool_executor: Async callable running one task's tool.
        joiner: Async callable synthesising the final answer.
        max_replanning: Maximum additional planning attempts on failure
            (default 2; must be ≥ 0).
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        tools: Sequence[Any],
        plan_fn: PlanFn,
        tool_executor: ToolExecutorFn,
        joiner: JoinerFn,
        max_replanning: int = 2,
        settings: Settings | None = None,
    ) -> None:
        if max_replanning < 0:
            raise ValueError(f"max_replanning must be >= 0; got {max_replanning}")
        self._tools = tools
        self._plan_fn = plan_fn
        self._tool_executor = tool_executor
        self._joiner = joiner
        self._max_replanning = max_replanning
        self._settings = settings if settings is not None else get_settings()

    # ── Planning ──────────────────────────────────────────────────────────────

    async def plan(
        self,
        goal: str,
        state: Any,
        previous_results: dict[str, Any] | None = None,
    ) -> CompilerPlan:
        """Ask the planner for a task list and validate it into a :class:`CompilerPlan`."""
        tasks = list(await self._plan_fn(goal, state, previous_results))
        waves = self.compute_execution_waves(tasks)
        plan = CompilerPlan(tasks=tasks, execution_waves=waves, goal=goal)
        self.validate_dag(plan)
        return plan

    def validate_dag(self, plan: CompilerPlan) -> bool:
        """Validate that *plan* is an acyclic DAG with well-formed deps.

        Uses Kahn's algorithm: nodes with in-degree 0 are consumed iteratively;
        if any remain at the end, the graph has a cycle.

        Raises:
            CompilerError: On duplicate ids, unknown dependencies, or cycles.

        Returns:
            ``True`` if the plan is valid (never ``False``; on failure raises).
        """
        ids = [t.id for t in plan.tasks]
        if len(ids) != len(set(ids)):
            dupes = [i for i in ids if ids.count(i) > 1]
            raise CompilerError(f"Plan contains duplicate task ids: {sorted(set(dupes))}")

        id_set = set(ids)
        for task in plan.tasks:
            for dep in task.depends_on:
                if dep not in id_set:
                    raise CompilerError(f"Task {task.id!r} depends on unknown task {dep!r}")

        # Kahn's algorithm
        in_degree = {t.id: len(t.depends_on) for t in plan.tasks}
        succ: dict[str, list[str]] = {t.id: [] for t in plan.tasks}
        for t in plan.tasks:
            for dep in t.depends_on:
                succ[dep].append(t.id)
        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        processed = 0
        while queue:
            tid = queue.popleft()
            processed += 1
            for s in succ[tid]:
                in_degree[s] -= 1
                if in_degree[s] == 0:
                    queue.append(s)
        if processed != len(plan.tasks):
            remaining = [tid for tid, deg in in_degree.items() if deg > 0]
            raise CompilerError(f"Plan contains a cycle involving tasks: {sorted(remaining)}")
        return True

    def compute_execution_waves(self, tasks: Sequence[TaskNode]) -> list[list[str]]:
        """Group tasks into topological waves.

        Each wave contains task ids whose dependencies are all satisfied by
        earlier waves; tasks in the same wave can run concurrently.
        """
        remaining = {t.id: set(t.depends_on) for t in tasks}
        order_index = {t.id: i for i, t in enumerate(tasks)}
        waves: list[list[str]] = []
        while remaining:
            ready = sorted(
                (tid for tid, deps in remaining.items() if not deps),
                key=lambda tid: order_index[tid],
            )
            if not ready:
                # Cycle — validate_dag will surface a descriptive error later;
                # bail so the caller knows something is wrong.
                break
            waves.append(ready)
            for tid in ready:
                del remaining[tid]
                for left_deps in remaining.values():
                    left_deps.discard(tid)
        return waves

    # ── Execution ─────────────────────────────────────────────────────────────

    async def compile_and_run(
        self,
        goal: str,
        state: Any,
    ) -> CompilerResult:
        """Plan → validate → execute → join (with retries on task failure)."""
        otel = OTelManager()
        with otel.start_span("llm_compiler.run") as span:
            span.set_attribute("lightagent.compiler.goal_len", len(goal))

            replanning_count = 0
            previous_results: dict[str, Any] | None = None

            while True:
                plan = await self.plan(goal, state, previous_results)
                outputs, succeeded, failed = await self._run_plan(plan)

                if failed == 0:
                    final = await self._joiner(goal, plan.tasks)
                    span.set_attribute("lightagent.compiler.replanning_count", replanning_count)
                    span.set_attribute("lightagent.compiler.tasks_succeeded", succeeded)
                    logger.info(
                        "llm_compiler_done",
                        goal_len=len(goal),
                        replanning_count=replanning_count,
                        tasks_succeeded=succeeded,
                        tasks_failed=failed,
                    )
                    return CompilerResult(
                        final_answer=final,
                        plan=plan,
                        replanning_count=replanning_count,
                        tasks_succeeded=succeeded,
                        tasks_failed=failed,
                    )

                # Prepare replanning context and retry.
                previous_results = dict(outputs)
                if replanning_count >= self._max_replanning:
                    raise CompilerError(
                        f"Exceeded max_replanning={self._max_replanning}; "
                        f"{failed} task(s) kept failing"
                    )
                replanning_count += 1
                logger.warning(
                    "llm_compiler_replan",
                    replanning_count=replanning_count,
                    tasks_failed=failed,
                )

    async def _run_plan(self, plan: CompilerPlan) -> tuple[dict[str, Any], int, int]:
        """Run all waves; return ``(outputs_by_id, succeeded, failed)``."""
        outputs: dict[str, Any] = {}
        by_id = {t.id: t for t in plan.tasks}
        succeeded = 0
        failed = 0
        for wave in plan.execution_waves:
            wave_tasks = [by_id[tid] for tid in wave]
            results = await asyncio.gather(
                *(self._run_one_task(t, outputs) for t in wave_tasks),
                return_exceptions=True,
            )
            for task, outcome in zip(wave_tasks, results, strict=True):
                if isinstance(outcome, BaseException):
                    task.status = "failed"
                    failed += 1
                    logger.warning(
                        "llm_compiler_task_failed",
                        task_id=task.id,
                        error=str(outcome),
                    )
                else:
                    task.status = "completed"
                    task.output = outcome
                    outputs[task.id] = outcome
                    succeeded += 1
        return outputs, succeeded, failed

    async def _run_one_task(self, task: TaskNode, prior_outputs: dict[str, Any]) -> Any:
        task.status = "running"
        interpolated = _interpolate_args(task.args, prior_outputs)
        # We don't mutate the original args dict; expose the interpolated one
        # by temporarily swapping, then restoring so the plan object remains
        # a faithful record of the original arguments.
        original_args = task.args
        task.args = interpolated
        try:
            return await self._tool_executor(task, prior_outputs)
        finally:
            task.args = original_args


# ── helpers ───────────────────────────────────────────────────────────────────


def _interpolate_args(args: dict[str, Any], prior_outputs: dict[str, Any]) -> dict[str, Any]:
    """Replace ``$Tid.output`` placeholders in *args* with real outputs.

    Pure function: returns a new dict; leaves *args* untouched. Placeholders
    whose referenced task has no output yet are left as-is (the tool executor
    will receive the raw placeholder).
    """
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            match = _REFERENCE_RE.fullmatch(value)
            if match:
                ref_id = match.group(1)
                if ref_id in prior_outputs:
                    resolved[key] = prior_outputs[ref_id]
                    continue

            # Inline substitution when placeholder appears as a substring.
            def _sub(m: re.Match[str]) -> str:
                ref_id = m.group(1)
                return str(prior_outputs.get(ref_id, m.group(0)))

            resolved[key] = _REFERENCE_RE.sub(_sub, value)
        else:
            resolved[key] = value
    return resolved


__all__ = [
    "CompilerError",
    "CompilerPlan",
    "CompilerResult",
    "JoinerFn",
    "LLMCompiler",
    "PlanFn",
    "TaskNode",
    "TaskStatus",
    "ToolExecutorFn",
]
