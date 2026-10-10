"""Token-regression CLI gate (Phase TKE — TKE9-03; SPEC-TKE-ACC-005)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismal.eval.__main__ import main
from prismal.eval.metrics_tokens import TokenReport, load_baseline, save_baseline
from prismal.eval.report import to_json
from prismal.eval.types import Scorecard


def _card(report: TokenReport | None) -> Scorecard:
    return Scorecard(
        suite="s",
        version="3.13.0",
        pass_rate=1.0,
        avg_steps=1.0,
        tool_error_rate=0.0,
        avg_cost_usd=0.0,
        avg_latency_ms=0.0,
        cases=[],
        token_report=report,
    )


def _write(tmp_path: Path, report: TokenReport) -> str:
    path = tmp_path / "current.json"
    path.write_text(to_json(_card(report)))
    return str(path)


def _baseline(tmp_path: Path, report: TokenReport) -> str:
    path = tmp_path / "tokens.json"
    save_baseline(report, str(path))
    return str(path)


_BASE = TokenReport(1000.0, 0.6, 1.0, 3.0, 0.01)


def test_token_gate_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cur = _write(tmp_path, TokenReport(1050.0, 0.6, 1.0, 3.0, 0.01))
    assert main(["token-gate", "--current", cur, "--baseline", _baseline(tmp_path, _BASE)]) == 0
    assert "TOKEN GATE PASS" in capsys.readouterr().out


def test_token_gate_fails_on_regression(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cur = _write(tmp_path, TokenReport(1200.0, 0.4, 1.0, 3.0, 0.01))
    assert main(["token-gate", "--current", cur, "--baseline", _baseline(tmp_path, _BASE)]) == 1
    out = capsys.readouterr().out
    assert out.count("TOKEN REGRESSION:") == 2


def test_token_gate_report_only_never_fails(tmp_path: Path) -> None:
    cur = _write(tmp_path, TokenReport(5000.0, 0.0, 1.0, 3.0, 0.01))
    args = ["token-gate", "--current", cur, "--baseline", _baseline(tmp_path, _BASE)]
    assert main([*args, "--report-only"]) == 0


def test_token_gate_missing_report_fails(tmp_path: Path) -> None:
    cur = tmp_path / "current.json"
    cur.write_text(to_json(_card(None)))
    assert (
        main(["token-gate", "--current", str(cur), "--baseline", _baseline(tmp_path, _BASE)]) == 1
    )


class _FakeRunner:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def run_set(self, eval_set: Any) -> Scorecard:
        return _card(_BASE)


def test_run_writes_token_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prismal.eval.__main__.EvalRunner", _FakeRunner)
    suite = tmp_path / "suite.yaml"
    suite.write_text("suite: smoke\ncases:\n  - id: c1\n    input: hi\n    assertions: []\n")
    out = tmp_path / "baseline.json"
    assert main(["run", "--suite", str(suite), "--write-token-baseline", str(out)]) == 0
    assert load_baseline(str(out)) == _BASE
    assert json.loads(out.read_text())["solve_rate"] == 1.0
