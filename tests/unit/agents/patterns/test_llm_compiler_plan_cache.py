"""LLM-Compiler planner ↔ plan cache wiring (AMR4-06, SPEC-AMR-PLC-001)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from prismal.agents.patterns.llm_compiler import LLMCompiler, TaskNode
from prismal.core.config import Settings
from prismal.routing.plan_cache import PlanCache, PlanCacheKey

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _settings(*, enabled: bool = True) -> Settings:
    return Settings(
        adaptive_routing_enabled=enabled,
        plan_cache_enabled=enabled,
        plan_cache_intents=["research"],
    )


def _key(tools: Sequence[str] = ("search",)) -> PlanCacheKey:
    return PlanCacheKey.build(
        "research", {"topic": "python"}, "acme", tools=tools, prompt_version="v1"
    )


class Planner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any] | None]] = []

    async def __call__(
        self, goal: str, state: Any, previous_results: dict[str, Any] | None
    ) -> list[TaskNode]:
        self.calls.append((goal, state, previous_results))
        return [
            TaskNode(id="T1", description="search", tool="search", args={"q": goal}),
            TaskNode(
                id="T2",
                description="sum",
                tool="search",
                args={"q": "$T1.output"},
                depends_on=["T1"],
            ),
        ]


async def _executor(task: TaskNode, prior: dict[str, Any]) -> str:
    return f"{task.id}:{task.args['q']}"


async def _joiner(goal: str, tasks: list[TaskNode]) -> str:
    return " | ".join(str(t.output) for t in tasks)


async def _identity(plan: Mapping[str, object], summary: str) -> Mapping[str, object]:
    return plan


def _compiler(planner: Planner, settings: Settings, cache: Any) -> LLMCompiler:
    return LLMCompiler(
        tools=[],
        plan_fn=planner,
        tool_executor=_executor,
        joiner=_joiner,
        settings=settings,
        plan_cache=cache,
    )


async def test_flags_off_planner_called_exactly_as_before() -> None:
    planner = Planner()
    cache = MagicMock(spec=PlanCache)
    compiler = _compiler(planner, _settings(enabled=False), cache)
    state = {"s": 1}
    r1 = await compiler.compile_and_run("g", state, plan_cache_key=_key(), request_summary="g")
    r2 = await compiler.compile_and_run("g", state, plan_cache_key=_key(), request_summary="g")
    assert planner.calls == [("g", state, None), ("g", state, None)]
    assert cache.method_calls == []
    assert r1.final_answer == r2.final_answer == "T1:g | T2:T1:g"


async def test_no_cache_or_key_keeps_legacy_path() -> None:
    planner = Planner()
    compiler = LLMCompiler(
        tools=[], plan_fn=planner, tool_executor=_executor, joiner=_joiner, settings=_settings()
    )
    await compiler.compile_and_run("g", None)
    await compiler.compile_and_run("g", None)
    assert len(planner.calls) == 2


async def test_repeated_read_only_intent_skips_planner() -> None:
    planner = Planner()
    cache = PlanCache(settings=_settings(), adapt_fn=_identity)
    compiler = _compiler(planner, _settings(), cache)
    first = await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    second = await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert len(planner.calls) == 1
    assert second.final_answer == first.final_answer
    assert [t.status for t in second.plan.tasks] == ["completed", "completed"]


async def test_tool_catalog_change_forces_planner_call() -> None:
    planner = Planner()
    cache = PlanCache(settings=_settings(), adapt_fn=_identity)
    compiler = _compiler(planner, _settings(), cache)
    await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    await compiler.compile_and_run(
        "g", None, plan_cache_key=_key(("search", "web_fetch")), request_summary="g"
    )
    assert len(planner.calls) == 2


async def test_replan_bypasses_cache() -> None:
    planner = Planner()
    cache = PlanCache(settings=_settings(), adapt_fn=_identity)
    attempts = {"n": 0}

    async def flaky(task: TaskNode, prior: dict[str, Any]) -> str:
        attempts["n"] += 1
        if attempts["n"] == 3:  # first task of the second (cached) run fails once
            raise RuntimeError("boom")
        return await _executor(task, prior)

    compiler = LLMCompiler(
        tools=[],
        plan_fn=planner,
        tool_executor=flaky,
        joiner=_joiner,
        settings=_settings(),
        plan_cache=cache,
    )
    await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    result = await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert result.replanning_count == 1
    assert len(planner.calls) == 2
    assert planner.calls[1][2] is not None  # replan carried previous_results


async def test_unusable_cached_plan_falls_back_to_planner() -> None:
    planner = Planner()
    cache = MagicMock(spec=PlanCache)

    async def lookup(key: PlanCacheKey, *, request_summary: str) -> Mapping[str, object]:
        return {"tasks": [{"id": "T1"}]}  # no tool → unusable

    cache.lookup.side_effect = lookup
    compiler = _compiler(planner, _settings(), cache)
    result = await compiler.compile_and_run("g", None, plan_cache_key=_key())
    assert len(planner.calls) == 1
    assert result.final_answer == "T1:g | T2:T1:g"
    cache.store.assert_called_once()


# ── review fixes: store only successful plans, evict failing cached plans ────


class FailingFirstRun:
    """Tool executor whose first *n* calls raise."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    async def __call__(self, task: TaskNode, prior: dict[str, Any]) -> str:
        self.calls += 1
        if self.calls <= self.n:
            raise RuntimeError("boom")
        return await _executor(task, prior)


async def test_failed_fresh_plan_is_never_stored() -> None:
    planner = Planner()
    cache = PlanCache(settings=_settings(), adapt_fn=_identity)
    compiler = LLMCompiler(
        tools=[],
        plan_fn=planner,
        tool_executor=FailingFirstRun(1),
        joiner=_joiner,
        settings=_settings(),
        plan_cache=cache,
    )
    result = await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert result.replanning_count == 1
    assert cache.backend.get(_key()) is None  # neither the failing plan nor the replan


async def test_plan_stored_only_after_successful_execution() -> None:
    planner = Planner()
    cache = MagicMock(spec=PlanCache)

    async def lookup(key: PlanCacheKey, *, request_summary: str) -> None:
        return None

    cache.lookup.side_effect = lookup
    order: list[str] = []
    cache.store.side_effect = lambda *a, **k: order.append("store") or True

    async def executor(task: TaskNode, prior: dict[str, Any]) -> str:
        order.append(f"run:{task.id}")
        return await _executor(task, prior)

    compiler = LLMCompiler(
        tools=[],
        plan_fn=planner,
        tool_executor=executor,
        joiner=_joiner,
        settings=_settings(),
        plan_cache=cache,
    )
    await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert order == ["run:T1", "run:T2", "store"]


async def test_failing_cached_plan_is_evicted() -> None:
    planner = Planner()
    cache = PlanCache(settings=_settings(), adapt_fn=_identity)
    executor = FailingFirstRun(0)
    compiler = LLMCompiler(
        tools=[],
        plan_fn=planner,
        tool_executor=executor,
        joiner=_joiner,
        settings=_settings(),
        plan_cache=cache,
    )
    await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert cache.backend.get(_key()) is not None
    # The cached plan now fails once on the next run → replan + eviction.
    executor.n, executor.calls = 1, 0
    result = await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert result.replanning_count == 1
    assert cache.backend.get(_key()) is None
    # A third run must call the planner again (no stale failing plan served).
    calls_before = len(planner.calls)
    await compiler.compile_and_run("g", None, plan_cache_key=_key(), request_summary="g")
    assert len(planner.calls) == calls_before + 1
