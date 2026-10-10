"""Routing cost/quality report + CLI gate (Phase AMR — SPEC-AMR-INT-002, AMR6-04)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from prismal.eval.__main__ import main
from prismal.eval.metrics_routing import (
    RoutingReport,
    build_routing_report,
    check_routing_gate,
)
from prismal.eval.report import to_json
from prismal.eval.types import Scorecard


def _card(pass_rate: float, cost: float) -> Scorecard:
    return Scorecard(
        suite="s",
        version="3.16.0",
        pass_rate=pass_rate,
        avg_steps=1.0,
        tool_error_rate=0.0,
        avg_cost_usd=cost,
        avg_latency_ms=0.0,
        cases=[],
    )


def test_report_computes_savings_and_quality_drop() -> None:
    report = build_routing_report(_card(0.90, 0.010), _card(0.895, 0.005))
    assert report.static_cost_usd == pytest.approx(0.010)
    assert report.routed_cost_usd == pytest.approx(0.005)
    assert report.savings_usd == pytest.approx(0.005)
    assert report.savings_ratio == pytest.approx(0.5)
    assert report.quality_drop_pts == pytest.approx(0.5)


def test_report_zero_static_cost_has_zero_ratio() -> None:
    assert build_routing_report(_card(1.0, 0.0), _card(1.0, 0.0)).savings_ratio == 0.0


def test_gate_passes_at_target() -> None:
    report = build_routing_report(_card(0.90, 0.010), _card(0.895, 0.006))
    assert check_routing_gate(report) == []


def test_gate_flags_insufficient_savings() -> None:
    report = build_routing_report(_card(0.90, 0.010), _card(0.90, 0.009))
    problems = check_routing_gate(report)
    assert len(problems) == 1 and "savings" in problems[0]


def test_gate_flags_quality_loss() -> None:
    report = build_routing_report(_card(0.90, 0.010), _card(0.87, 0.002))
    problems = check_routing_gate(report)
    assert len(problems) == 1 and "quality" in problems[0]


def test_gate_thresholds_configurable() -> None:
    report = RoutingReport(0.01, 0.009, 0.001, 0.1, 0.9, 0.85, 5.0)
    assert check_routing_gate(report, min_savings_ratio=0.05, max_quality_drop_pts=6.0) == []


def _write(tmp_path: Path, name: str, card: Scorecard) -> str:
    path = tmp_path / name
    path.write_text(to_json(card), encoding="utf-8")
    return str(path)


def test_cli_routing_gate_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    static = _write(tmp_path, "static.json", _card(0.9, 0.01))
    routed = _write(tmp_path, "routed.json", _card(0.9, 0.005))
    assert main(["routing-gate", "--static", static, "--routed", routed]) == 0
    out = capsys.readouterr().out
    assert "ROUTING GATE PASS" in out and "routing_savings_usd=0.005" in out


def test_cli_routing_gate_fail_and_report_only(tmp_path: Path) -> None:
    static = _write(tmp_path, "static.json", _card(0.9, 0.01))
    routed = _write(tmp_path, "routed.json", _card(0.8, 0.01))
    assert main(["routing-gate", "--static", static, "--routed", routed]) == 1
    assert main(["routing-gate", "--static", static, "--routed", routed, "--report-only"]) == 0


def test_cli_run_with_routing_injects_policy(tmp_path: Path) -> None:
    suite = tmp_path / "suite.yaml"
    suite.write_text("suite: s\ncases: []\n", encoding="utf-8")
    seen: dict[str, Any] = {}
    from prismal.core.config_source import get_config_source

    before = get_config_source()

    async def fake_run_set(self: Any, eval_set: Any) -> Scorecard:
        from prismal.core.config import get_settings

        s = get_settings()
        seen.update(
            enabled=s.adaptive_routing_enabled, policy=s.routing_policy, mode=s.routing_mode
        )
        return _card(1.0, 0.0)

    with patch("prismal.eval.runner.EvalRunner.run_set", fake_run_set):
        assert main(["run", "--suite", str(suite), "--routing", "cascade"]) == 0
    assert seen == {"enabled": True, "policy": "cascade", "mode": "enforce"}

    from prismal.core.config import get_settings

    assert get_config_source() is before  # restored after the run
    assert get_settings().adaptive_routing_enabled is False
