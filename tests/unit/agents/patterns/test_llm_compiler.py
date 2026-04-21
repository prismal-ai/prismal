"""Unit tests for LLMCompiler (lightagent.agents.patterns.llm_compiler).

LLM-Compiler: plan a goal into a DAG of tool-executing tasks, validate the
DAG (topological sort), run independent tasks in parallel, re-plan on
failure.

plan_fn, tool_executor, and joiner_fn are injected so tests run without any
LLM backend.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lightagent.agents.patterns.llm_compiler import (
    CompilerError,
    CompilerPlan,
    CompilerResult,
    LLMCompiler,
    TaskNode,
)
from lightagent.core.exceptions import LightAgentError


def _task(
    id_: str, tool: str = "echo", args: dict[str, Any] | None = None, deps: list[str] | None = None
) -> TaskNode:
    return TaskNode(
        id=id_,
        description=f"task {id_}",
        tool=tool,
        args=args or {},
        depends_on=deps or [],
    )


# ── Dataclasses / exception ──────────────────────────────────────────────────


def test_task_node_is_instantiable() -> None:
    t = _task("T1")
    assert t.id == "T1"
    assert t.status == "pending"
    assert t.output is None
    assert t.depends_on == []


def test_compiler_plan_is_instantiable() -> None:
    tasks = [_task("T1"), _task("T2", deps=["T1"])]
    plan = CompilerPlan(tasks=tasks, execution_waves=[["T1"], ["T2"]], goal="g")
    assert plan.goal == "g"
    assert len(plan.tasks) == 2


def test_compiler_plan_to_json_round_trips() -> None:
    plan = CompilerPlan(
        tasks=[_task("T1", tool="echo", args={"msg": "hi"})],
        execution_waves=[["T1"]],
        goal="greet",
    )
    blob = plan.to_json()
    data = json.loads(blob)
    assert data["goal"] == "greet"
    assert data["execution_waves"] == [["T1"]]
    assert data["tasks"][0]["id"] == "T1"


def test_compiler_result_is_instantiable() -> None:
    plan = CompilerPlan(tasks=[], execution_waves=[], goal="g")
    r = CompilerResult(
        final_answer="42",
        plan=plan,
        replanning_count=1,
        tasks_succeeded=2,
        tasks_failed=0,
    )
    assert r.final_answer == "42"


def test_compiler_error_inherits_from_lightagent_error() -> None:
    assert issubclass(CompilerError, LightAgentError)


# ── validate_dag ─────────────────────────────────────────────────────────────


def test_validate_dag_passes_on_linear_chain() -> None:
    plan = CompilerPlan(
        tasks=[_task("T1"), _task("T2", deps=["T1"]), _task("T3", deps=["T2"])],
        execution_waves=[["T1"], ["T2"], ["T3"]],
        goal="g",
    )
    compiler = _compiler()
    assert compiler.validate_dag(plan) is True


def test_validate_dag_passes_on_parallel_fan_out() -> None:
    plan = CompilerPlan(
        tasks=[_task("T1"), _task("T2"), _task("T3", deps=["T1", "T2"])],
        execution_waves=[["T1", "T2"], ["T3"]],
        goal="g",
    )
    compiler = _compiler()
    assert compiler.validate_dag(plan) is True


def test_validate_dag_rejects_cycle() -> None:
    plan = CompilerPlan(
        tasks=[
            _task("T1", deps=["T2"]),
            _task("T2", deps=["T1"]),
        ],
        execution_waves=[],
        goal="g",
    )
    compiler = _compiler()
    with pytest.raises(CompilerError, match="cycle"):
        compiler.validate_dag(plan)


def test_validate_dag_rejects_dangling_dependency() -> None:
    plan = CompilerPlan(
        tasks=[_task("T1", deps=["T_UNKNOWN"])],
        execution_waves=[],
        goal="g",
    )
    compiler = _compiler()
    with pytest.raises(CompilerError, match="unknown"):
        compiler.validate_dag(plan)


def test_validate_dag_rejects_duplicate_task_ids() -> None:
    plan = CompilerPlan(
        tasks=[_task("T1"), _task("T1")],
        execution_waves=[],
        goal="g",
    )
    compiler = _compiler()
    with pytest.raises(CompilerError, match="duplicate"):
        compiler.validate_dag(plan)


# ── Wave computation ─────────────────────────────────────────────────────────


def test_compute_waves_groups_parallel_tasks() -> None:
    """T1, T2 have no deps → wave 0; T3 depends on both → wave 1."""
    compiler = _compiler()
    tasks = [_task("T1"), _task("T2"), _task("T3", deps=["T1", "T2"])]
    waves = compiler.compute_execution_waves(tasks)
    assert waves[0] == ["T1", "T2"] or waves[0] == ["T2", "T1"]
    assert waves[1] == ["T3"]


def test_compute_waves_for_linear_chain() -> None:
    compiler = _compiler()
    tasks = [_task("A"), _task("B", deps=["A"]), _task("C", deps=["B"])]
    waves = compiler.compute_execution_waves(tasks)
    assert waves == [["A"], ["B"], ["C"]]


# ── compile_and_run: execution ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compile_and_run_executes_linear_dag() -> None:
    tasks = [_task("T1", tool="fetch"), _task("T2", tool="parse", deps=["T1"])]

    async def plan_fn(goal, state, previous_results):
        return tasks

    async def tool_executor(task, prior_outputs):
        return f"output-of-{task.id}"

    async def joiner(goal, completed_tasks):
        return "final answer from " + ",".join(t.id for t in completed_tasks)

    compiler = _compiler(plan_fn=plan_fn, tool_executor=tool_executor, joiner=joiner)
    result = await compiler.compile_and_run(goal="do it", state={})

    assert result.tasks_succeeded == 2
    assert result.tasks_failed == 0
    assert result.replanning_count == 0
    assert "T1" in result.final_answer and "T2" in result.final_answer


@pytest.mark.asyncio
async def test_parallel_tasks_run_concurrently() -> None:
    """Three independent tasks that each sleep 0.1s should finish in ~0.1s, not 0.3s."""
    tasks = [_task("T1"), _task("T2"), _task("T3")]

    async def plan_fn(goal, state, previous_results):
        return tasks

    async def slow_executor(task, prior_outputs):
        await asyncio.sleep(0.1)
        return f"out-{task.id}"

    async def joiner(goal, completed_tasks):
        return "joined"

    compiler = _compiler(plan_fn=plan_fn, tool_executor=slow_executor, joiner=joiner)

    start = time.monotonic()
    result = await compiler.compile_and_run(goal="g", state={})
    elapsed = time.monotonic() - start

    assert result.tasks_succeeded == 3
    # Parallel: ~0.1s. Sequential would be ~0.3s. Assert < 0.25s for margin.
    assert elapsed < 0.25, f"tasks did not run in parallel (elapsed={elapsed:.3f}s)"


@pytest.mark.asyncio
async def test_args_interpolate_prior_task_outputs() -> None:
    """'$T1.output' in args must be replaced by T1's actual output."""
    tasks = [
        _task("T1", tool="produce"),
        _task("T2", tool="consume", args={"input": "$T1.output"}, deps=["T1"]),
    ]

    captured_args: dict[str, dict[str, Any]] = {}

    async def plan_fn(goal, state, previous_results):
        return tasks

    async def tool_executor(task, prior_outputs):
        captured_args[task.id] = dict(task.args)
        if task.id == "T1":
            return "T1_VALUE"
        return "done"

    async def joiner(goal, completed_tasks):
        return "x"

    compiler = _compiler(plan_fn=plan_fn, tool_executor=tool_executor, joiner=joiner)
    await compiler.compile_and_run(goal="g", state={})

    # By the time T2 ran, "$T1.output" must have been substituted.
    assert captured_args["T2"]["input"] == "T1_VALUE"


# ── Replanning ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replan_triggered_on_task_failure() -> None:
    call_count = {"plan": 0}
    tasks_v1 = [_task("T1", tool="failing")]
    tasks_v2 = [_task("T1", tool="succeeding")]

    async def plan_fn(goal, state, previous_results):
        call_count["plan"] += 1
        return tasks_v1 if call_count["plan"] == 1 else tasks_v2

    async def tool_executor(task, prior_outputs):
        if task.tool == "failing":
            raise RuntimeError("tool failed")
        return "ok"

    async def joiner(goal, completed_tasks):
        return "final"

    compiler = _compiler(
        plan_fn=plan_fn,
        tool_executor=tool_executor,
        joiner=joiner,
        max_replanning=2,
    )
    result = await compiler.compile_and_run(goal="g", state={})

    assert call_count["plan"] == 2  # initial + 1 replan
    assert result.replanning_count == 1
    assert result.tasks_succeeded >= 1


@pytest.mark.asyncio
async def test_max_replanning_cap_aborts_with_compiler_error() -> None:
    async def plan_fn(goal, state, previous_results):
        return [_task("T1", tool="failing")]

    async def tool_executor(task, prior_outputs):
        raise RuntimeError("always fails")

    async def joiner(goal, completed_tasks):
        return "x"

    compiler = _compiler(
        plan_fn=plan_fn,
        tool_executor=tool_executor,
        joiner=joiner,
        max_replanning=2,
    )
    with pytest.raises(CompilerError, match="replanning"):
        await compiler.compile_and_run(goal="g", state={})


# ── Constructor ──────────────────────────────────────────────────────────────


def test_init_rejects_negative_max_replanning() -> None:
    with pytest.raises(ValueError):
        _compiler(max_replanning=-1)


# ── factory ──────────────────────────────────────────────────────────────────


def _compiler(
    plan_fn: Any = None,
    tool_executor: Any = None,
    joiner: Any = None,
    max_replanning: int = 2,
) -> LLMCompiler:
    return LLMCompiler(
        tools=[],
        plan_fn=plan_fn or AsyncMock(return_value=[]),
        tool_executor=tool_executor or AsyncMock(return_value=None),
        joiner=joiner or AsyncMock(return_value=""),
        max_replanning=max_replanning,
        settings=MagicMock(),
    )
