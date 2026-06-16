"""The evaluation runner (SPEC-EVL-RUN-001).

``EvalRunner`` drives one or many cases against the compiled graph, capturing a
:class:`~prismal.eval.types.Trajectory` per case and evaluating its assertions.
It is deterministic by default (``build_test_runtime`` fakes + a per-case seed)
and never raises out of ``run_case`` — any failure becomes a failed
:class:`~prismal.eval.types.CaseResult` with a non-terminated trajectory.

The default graph/runtime factories are bound lazily so importing this module
stays cheap and the harness can be injected with fakes in tests.
"""

from __future__ import annotations

import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from prismal.eval.trajectory import capture_trajectory
from prismal.eval.types import (
    AssertionResult,
    CaseResult,
    EvalCase,
    EvalSet,
    Scorecard,
    Trajectory,
)

if TYPE_CHECKING:
    from prismal.core.config import Settings

GraphFactory = Callable[..., Awaitable[Any]]
RuntimeFactory = Callable[..., Any]
# An override evaluator receives (trajectory, case, judge, embeddings).
Evaluator = Callable[..., Awaitable[list[AssertionResult]]]


class EvalRunner:
    """Run eval-sets against the graph and produce scorecards."""

    def __init__(
        self,
        *,
        graph_factory: GraphFactory | None = None,
        runtime_factory: RuntimeFactory | None = None,
        evaluator: Evaluator | None = None,
        judge: Any = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialise the runner.

        Args:
            graph_factory: Async factory returning a compiled graph. Defaults to
                :func:`prismal.agents.graph.get_async_compiled_graph`.
            runtime_factory: Factory returning a ``RuntimeContext``. Defaults to
                :func:`prismal.composition.build_test_runtime` (deterministic fakes).
            evaluator: Optional override for assertion dispatch; defaults to
                :func:`prismal.eval.assertions.dispatch_assertions`.
            judge: Optional LLM-as-judge for ``llm_judge``/``groundedness``.
            settings: Optional settings (seed, mode).
        """
        self._graph_factory = graph_factory
        self._runtime_factory = runtime_factory
        self._evaluator = evaluator
        self._judge = judge
        self._settings = settings

    async def run_case(self, case: EvalCase) -> CaseResult:
        """Run one case and return its :class:`CaseResult` (never raises)."""
        self._seed_for(case)
        runtime = self._make_runtime(case)
        try:
            graph = await self._make_graph(runtime)
            config = {"configurable": {"thread_id": f"eval-{case.id}"}}
            trajectory = await capture_trajectory(graph, case, config=config)
            assertion_results = await self._evaluate(trajectory, case, runtime)
        except Exception as exc:  # a runner failure is a failed case, never a raise
            return CaseResult(
                case_id=case.id,
                passed=False,
                trajectory=_empty_trajectory(case, detail=str(exc)),
                assertion_results=[],
            )
        finally:
            await _aclose(runtime)

        # A run that did not terminate (graph error mid-stream) fails the case
        # regardless of assertions — it never produced a complete answer.
        passed = trajectory.terminated and all(ar.passed for ar in assertion_results)
        return CaseResult(
            case_id=case.id,
            passed=passed,
            trajectory=trajectory,
            assertion_results=assertion_results,
        )

    async def run_set(self, eval_set: EvalSet) -> Scorecard:
        """Run every case in *eval_set* and aggregate a :class:`Scorecard`."""
        results = [await self.run_case(case) for case in eval_set.cases]
        return _aggregate(eval_set.suite, results, version=self._version())

    # ── internals ────────────────────────────────────────────────────────────

    async def _evaluate(
        self, trajectory: Trajectory, case: EvalCase, runtime: Any
    ) -> list[AssertionResult]:
        """Evaluate the case's assertions against the captured trajectory."""
        embeddings = getattr(runtime, "embeddings", None)
        judge = self._judge_for()
        if self._evaluator is not None:
            return await self._evaluator(trajectory, case, judge=judge, embeddings=embeddings)
        from prismal.eval.assertions import dispatch_assertions

        return await dispatch_assertions(trajectory, case, judge=judge, embeddings=embeddings)

    def _judge_for(self) -> Any:
        """Return the judge, lazily building a default when assertions need one."""
        if self._judge is not None:
            return self._judge
        from prismal.eval.judges import Judge

        self._judge = Judge(settings=self._settings)
        return self._judge

    def _seed_for(self, case: EvalCase) -> None:
        """Seed the RNG for reproducibility from the case setup or settings."""
        seed = case.setup.get("seed")
        if seed is None and self._settings is not None:
            seed = self._settings.eval_seed
        if seed is not None:
            random.seed(int(seed))

    def _make_runtime(self, case: EvalCase) -> Any:
        """Build the per-case runtime (fakes by default)."""
        factory = self._runtime_factory
        if factory is None:
            from prismal.composition import build_test_runtime

            factory = build_test_runtime
        return factory(**_runtime_kwargs(case.setup))

    async def _make_graph(self, runtime: Any) -> Any:
        """Build the compiled graph bound to the runtime's tool provider."""
        factory = self._graph_factory
        if factory is None:
            from prismal.agents.graph import get_async_compiled_graph

            factory = get_async_compiled_graph
        tool_provider = getattr(runtime, "tool_provider", None)
        return await factory(tool_provider=tool_provider)

    def _version(self) -> str:
        """Resolve the prismal package version stamped on the scorecard."""
        return _package_version()


def _package_version() -> str:
    """Best-effort prismal distribution version (``"0.0.0"`` if undiscoverable)."""
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("prismal-ai", "prismal"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0.0.0"


def _runtime_kwargs(setup: dict[str, Any]) -> dict[str, Any]:
    """Translate a case ``setup`` block into ``build_test_runtime`` kwargs.

    For V2 only the ``org_id`` passthrough is honoured; ``tool_provider`` /
    ``vector_store`` ``"fake"`` markers map to the default fakes. Per-case
    setting ``flags`` are consumed by later phases (red-team).
    """
    kwargs: dict[str, Any] = {}
    if "org_id" in setup:
        kwargs["org_id"] = setup["org_id"]
    return kwargs


async def _aclose(runtime: Any) -> None:
    """Best-effort teardown of a runtime context."""
    aclose = getattr(runtime, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(Exception):  # teardown must not mask the result
        await aclose()


def _empty_trajectory(case: EvalCase, *, detail: str = "") -> Trajectory:
    """A non-terminated trajectory used when a run fails before capture."""
    del detail
    return Trajectory(
        case_id=case.id,
        final_answer="",
        steps=[],
        visited_nodes=[],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=0,
        latency_ms=0.0,
        terminated=False,
    )


def _aggregate(suite: str, results: list[CaseResult], *, version: str) -> Scorecard:
    """Roll per-case results up into suite-level metrics."""
    n = len(results)
    if n == 0:
        return Scorecard(
            suite=suite,
            version=version,
            pass_rate=0.0,
            avg_steps=0.0,
            tool_error_rate=0.0,
            avg_cost_usd=0.0,
            avg_latency_ms=0.0,
            cases=[],
        )
    trajectories = [r.trajectory for r in results]
    total_calls = sum(t.tool_calls for t in trajectories)
    total_errors = sum(t.tool_errors for t in trajectories)
    return Scorecard(
        suite=suite,
        version=version,
        pass_rate=sum(1 for r in results if r.passed) / n,
        avg_steps=sum(len(t.steps) for t in trajectories) / n,
        tool_error_rate=(total_errors / total_calls) if total_calls else 0.0,
        avg_cost_usd=sum(t.cost_usd for t in trajectories) / n,
        avg_latency_ms=sum(t.latency_ms for t in trajectories) / n,
        cases=results,
    )


__all__ = ["EvalRunner"]
