"""Token report, baseline and regression gate (Phase TKE — SPEC-TKE-ACC-005)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from prismal.eval.metrics_tokens import (
    TokenReport,
    build_token_report,
    check_token_regression,
    load_baseline,
    save_baseline,
)
from prismal.eval.trajectory import capture_trajectory
from prismal.eval.types import Assertion, AssertionType, CaseResult, EvalCase, Trajectory


def _traj(
    *,
    tokens: int,
    prompt: int,
    cache_read: int,
    llm_calls: int,
    input_cost: float,
    case_id: str = "c",
) -> Trajectory:
    return Trajectory(
        case_id=case_id,
        final_answer="",
        steps=[],
        visited_nodes=[],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=tokens,
        latency_ms=0.0,
        terminated=True,
        prompt_tokens=prompt,
        cache_read_tokens=cache_read,
        llm_calls=llm_calls,
        input_cost_usd=input_cost,
    )


def _result(passed: bool, **kw: Any) -> CaseResult:
    return CaseResult(case_id="c", passed=passed, trajectory=_traj(**kw), assertion_results=[])


def test_build_token_report() -> None:
    results = [
        _result(True, tokens=1000, prompt=900, cache_read=600, llm_calls=2, input_cost=0.01),
        _result(True, tokens=3000, prompt=2700, cache_read=1200, llm_calls=4, input_cost=0.03),
        _result(False, tokens=5000, prompt=4400, cache_read=0, llm_calls=6, input_cost=0.05),
    ]
    report = build_token_report(results)
    assert report.solve_rate == pytest.approx(2 / 3)
    assert report.tokens_per_successful_task == pytest.approx(9000 / 2)
    assert report.cache_hit_ratio == pytest.approx(1800 / 8000)
    assert report.mean_trajectory_length == pytest.approx(4.0)
    assert report.input_cost_usd_per_success == pytest.approx(0.09 / 2)


def test_build_token_report_empty_and_no_success() -> None:
    assert build_token_report([]) == TokenReport(0.0, 0.0, 0.0, 0.0, 0.0)
    report = build_token_report(
        [_result(False, tokens=10, prompt=10, cache_read=0, llm_calls=1, input_cost=0.1)]
    )
    assert report.tokens_per_successful_task == 0.0
    assert report.input_cost_usd_per_success == 0.0


def test_baseline_round_trip(tmp_path: Path) -> None:
    report = TokenReport(100.0, 0.5, 1.0, 3.0, 0.002)
    path = tmp_path / "tokens.json"
    save_baseline(report, str(path))
    assert json.loads(path.read_text())["tokens_per_successful_task"] == 100.0
    assert load_baseline(str(path)) == report


def test_regression_passes_within_thresholds() -> None:
    base = TokenReport(1000.0, 0.6, 1.0, 3.0, 0.01)
    assert check_token_regression(TokenReport(1099.0, 0.51, 1.0, 3.0, 0.01), base) == []


def test_token_regression_detected() -> None:
    base = TokenReport(1000.0, 0.6, 1.0, 3.0, 0.01)
    problems = check_token_regression(TokenReport(1101.0, 0.6, 1.0, 3.0, 0.01), base)
    assert len(problems) == 1
    assert "tokens_per_successful_task" in problems[0]


def test_hit_ratio_drop_detected() -> None:
    base = TokenReport(1000.0, 0.6, 1.0, 3.0, 0.01)
    problems = check_token_regression(TokenReport(1000.0, 0.49, 1.0, 3.0, 0.01), base)
    assert ["cache_hit_ratio" in p for p in problems] == [True]


class _FakeGraph:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            yield chunk


async def test_trajectory_captures_prompt_cache_and_llm_calls() -> None:
    def ai(text: str, prompt: int, cached: int) -> AIMessage:
        return AIMessage(
            content=text,
            usage_metadata={
                "input_tokens": prompt,
                "output_tokens": 10,
                "total_tokens": prompt + 10,
                "input_token_details": {"cache_read": cached},
            },
            response_metadata={"model_name": "claude-sonnet-4-5"},
        )

    graph = _FakeGraph(
        [
            {"supervisor": {"messages": [ai("route", 1000, 800)]}},
            {"coder": {"messages": [ai("done", 2000, 1500)]}},
        ]
    )
    case = EvalCase(id="t", input="x", assertions=[Assertion(type=AssertionType.EXACT)])
    traj = await capture_trajectory(graph, case, clock=lambda: 0.0)
    assert traj.prompt_tokens == 3000
    assert traj.cache_read_tokens == 2300
    assert traj.llm_calls == 2
    assert traj.input_cost_usd > 0.0


def _scorecard_with(results: list[CaseResult]) -> Any:
    from prismal.eval.runner import _aggregate

    return _aggregate("smoke", results, version="3.13.0")


def test_scorecard_carries_token_report() -> None:
    results = [_result(True, tokens=100, prompt=80, cache_read=40, llm_calls=1, input_cost=0.0)]
    card = _scorecard_with(results)
    assert card.token_report == build_token_report(results)


def test_report_renders_token_metrics() -> None:
    from prismal.eval.report import to_json, to_markdown

    results = [_result(True, tokens=100, prompt=80, cache_read=40, llm_calls=1, input_cost=0.0)]
    card = _scorecard_with(results)
    assert "Tokens per successful task" in to_markdown(card)
    assert json.loads(to_json(card))["token_report"]["cache_hit_ratio"] == 0.5
