"""Parallel unit testing for the dev_pipeline subgraph (Phase 34, T-313).

When the developer produces a multi-module ``CodeArtifact`` (i.e. the LLM
returns ``{"modules": [...]}``), the dev_pipeline fans out one ``Send`` per
module to :func:`dev_unit_tester_node`, then merges the resulting partial
``TestReport`` instances in :func:`dev_test_aggregator_node`.

Topology added inside the dev_pipeline subgraph::

    developer → module_router  ──[module_dispatcher]──┐
                       │ (on_empty)                    │ (one Send per module)
                       ▼                                ▼
                  unit_tester                  dev_unit_tester (parallel)
                       │                                │
                       ▼                                ▼
                _TEST_GATE                   dev_test_aggregator
                                                       │
                                                       ▼
                                                _TEST_GATE
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.patterns.parallel import make_parallel_dispatcher
from prismal.agents.subgraphs.artifacts import TestReport
from prismal.agents.subgraphs.dev_pipeline.unit_test_agent import (
    _SYSTEM as _UNIT_TEST_SYSTEM,
)
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger(
    "prismal.subgraphs.dev_pipeline.parallel_unit_test",
)

# Per-worker artifact tag — written into ``parallel_results`` so the
# aggregator can filter its own contributions away from any other parallel
# producer (e.g. the parallel research path).
_DEV_UNIT_TESTER_AGENT_TAG = "dev_unit_tester"


async def module_router_node(state: AgentState) -> dict[str, Any]:
    """Pass-through anchor for the module dispatcher conditional edges.

    LangGraph requires conditional edges (including ``Send``-emitting ones)
    to originate from a registered node.  This function is that anchor.

    Args:
        state: Current agent state from LangGraph (unused).

    Returns:
        Partial state update with ``current_agent`` set to
        ``"module_router"``.
    """
    raw = state.get("dev_pipeline_modules", [])
    modules: list[dict[str, Any]] = raw if isinstance(raw, list) else []
    logger.debug(
        "dev_pipeline.module_router_called",
        module_count=len(modules),
    )
    return {"current_agent": "module_router"}


async def dev_unit_tester_node(state: AgentState) -> dict[str, Any]:
    """Generate a TestReport for a single module dispatched in parallel.

    Reads the per-worker module dict from ``state["_task"]`` (populated by
    :func:`make_parallel_dispatcher`), invokes the unit-test LLM with the
    same system prompt as the sequential ``unit_tester`` node, parses the
    response into a :class:`TestReport`, and appends a tagged dict to
    ``state["parallel_results"]``.

    Because ``parallel_results`` uses an ``operator.add`` reducer (see
    Phase 34 / T-309), every parallel invocation of this worker contributes
    one entry to the merged list visible to the aggregator.

    Args:
        state: Current agent state.  Must contain ``_task`` populated by
            the dispatcher.

    Returns:
        Partial state update appending a single dict to
        ``parallel_results``.
    """
    raw_task: Any = state.get("_task")
    module_data: dict[str, Any] = raw_task if isinstance(raw_task, dict) else {}
    file_path = str(module_data.get("file_path", "unknown"))
    logger.debug("dev_unit_tester.called", module=file_path)

    if not module_data:
        return {
            "parallel_results": [
                {
                    "agent": _DEV_UNIT_TESTER_AGENT_TAG,
                    "file_path": file_path,
                    "report": TestReport(
                        tests_written=0,
                        tests_passed=0,
                        failing_tests=["empty_module"],
                    ).model_dump(),
                }
            ]
        }

    llm = ProviderRegistry().get_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content=_UNIT_TEST_SYSTEM),
            HumanMessage(
                content=f"Code artifact:\n{json.dumps(module_data, indent=2)}",
            ),
        ]
    )
    content = str(response.content)

    try:
        data = json.loads(content)
        report = TestReport.model_validate(data)
    except Exception:
        report = TestReport(
            tests_written=0,
            tests_passed=0,
            failing_tests=["parse_error"],
        )

    logger.info(
        "dev_unit_tester.report_created",
        module=file_path,
        passed=report.tests_passed,
        failed=len(report.failing_tests),
    )
    return {
        "parallel_results": [
            {
                "agent": _DEV_UNIT_TESTER_AGENT_TAG,
                "file_path": file_path,
                "report": report.model_dump(),
            }
        ]
    }


async def dev_test_aggregator_node(state: AgentState) -> dict[str, Any]:
    """Merge per-module ``TestReport`` instances into a single report.

    Filters ``state["parallel_results"]`` for entries tagged by
    :func:`dev_unit_tester_node`, sums the per-module counters, averages the
    coverage percentages, and concatenates the failing test names + the
    recommendations.  The merged report is written into
    ``state["metadata"]["dev_pipeline"]["test_report"]`` so the existing
    ``_TEST_GATE`` (which reads ``dev_pipeline.test_report.failing_tests``)
    can route as usual.

    Args:
        state: Current agent state with populated ``parallel_results``.

    Returns:
        Partial state update with ``current_agent`` set to
        ``"dev_test_aggregator"``, a summary AIMessage, and the merged
        ``test_report`` under ``metadata.dev_pipeline``.
    """
    raw_results: Any = state.get("parallel_results", [])
    results: list[dict[str, Any]] = raw_results if isinstance(raw_results, list) else []
    own_results = [
        item
        for item in results
        if isinstance(item, dict) and item.get("agent") == _DEV_UNIT_TESTER_AGENT_TAG
    ]

    total_written = 0
    total_passed = 0
    coverage_sum = 0.0
    failing: list[str] = []
    recommendations: list[str] = []

    for item in own_results:
        report_dict = item.get("report", {})
        if not isinstance(report_dict, dict):
            continue
        try:
            r = TestReport.model_validate(report_dict)
        except Exception as exc:
            logger.warning(
                "dev_test_aggregator.invalid_report_skipped",
                error=str(exc),
            )
            continue
        total_written += r.tests_written
        total_passed += r.tests_passed
        coverage_sum += r.coverage_percent
        failing.extend(r.failing_tests)
        recommendations.extend(r.recommendations)

    coverage_avg = coverage_sum / len(own_results) if own_results else 0.0
    merged = TestReport(
        tests_written=total_written,
        tests_passed=total_passed,
        coverage_percent=round(coverage_avg, 2),
        failing_tests=failing,
        recommendations=recommendations,
    )

    dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
    dp["test_report"] = merged.model_dump()
    dp["module_test_count"] = len(own_results)

    logger.info(
        "dev_test_aggregator.merged",
        module_count=len(own_results),
        total_passed=merged.tests_passed,
        total_written=merged.tests_written,
        failing=len(failing),
    )

    return {
        "current_agent": "dev_test_aggregator",
        "messages": [
            AIMessage(
                content=(
                    f"Parallel tests across {len(own_results)} modules: "
                    f"{merged.tests_passed}/{merged.tests_written} passed"
                )
            )
        ],
        "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
    }


# Pre-built dispatcher: when ``state["dev_pipeline_modules"]`` has any
# entries it fans out one Send per module to ``dev_unit_tester``; otherwise
# routes to the sequential ``unit_tester`` (the existing single-artifact
# path).  ``max_workers=5`` matches the spec.
module_dispatcher = make_parallel_dispatcher(
    tasks_field="dev_pipeline_modules",
    worker_node="dev_unit_tester",
    max_workers=5,
    on_empty="unit_tester",
)


__all__ = [
    "dev_test_aggregator_node",
    "dev_unit_tester_node",
    "module_dispatcher",
    "module_router_node",
]
