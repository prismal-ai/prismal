"""Tests for the eval CLI (Phase V — SPEC-EVL-CLI-001 / RF-EVL-006).

``run``/``redteam`` are exercised with a monkeypatched ``EvalRunner`` so no real
graph or LLM is touched; ``gate`` is pure (compares two JSON scorecards).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismal.eval.__main__ import main
from prismal.eval.report import to_json
from prismal.eval.types import Scorecard


def _card(*, suite: str = "s", pass_rate: float = 1.0) -> Scorecard:
    return Scorecard(
        suite=suite,
        version="3.3.0",
        pass_rate=pass_rate,
        avg_steps=1.0,
        tool_error_rate=0.0,
        avg_cost_usd=0.0,
        avg_latency_ms=0.0,
        cases=[],
    )


def _suite_file(tmp_path: Path) -> str:
    p = tmp_path / "suite.yaml"
    p.write_text("suite: smoke\ncases:\n  - id: c1\n    input: hi\n    assertions: []\n")
    return str(p)


class _FakeRunner:
    """Stands in for EvalRunner; run_set echoes a canned scorecard."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def run_set(self, eval_set: Any) -> Scorecard:
        return _card(suite=eval_set.suite)


# ── gate (pure) ───────────────────────────────────────────────────────────────


def test_cli_gate_passes_when_no_regression(tmp_path: Path) -> None:
    cur = tmp_path / "cur.json"
    base = tmp_path / "base.json"
    cur.write_text(to_json(_card(pass_rate=1.0)))
    base.write_text(to_json(_card(pass_rate=1.0)))
    assert main(["gate", "--current", str(cur), "--baseline", str(base)]) == 0


def test_cli_gate_fails_on_regression(tmp_path: Path) -> None:
    cur = tmp_path / "cur.json"
    base = tmp_path / "base.json"
    cur.write_text(to_json(_card(pass_rate=0.5)))
    base.write_text(to_json(_card(pass_rate=1.0)))
    assert main(["gate", "--current", str(cur), "--baseline", str(base)]) == 1


# ── run ───────────────────────────────────────────────────────────────────────


def test_cli_run_writes_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prismal.eval.__main__.EvalRunner", _FakeRunner)
    out = tmp_path / "out.json"
    rc = main(["run", "--suite", _suite_file(tmp_path), "--json", str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["suite"] == "smoke"


def test_cli_run_writes_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prismal.eval.__main__.EvalRunner", _FakeRunner)
    out = tmp_path / "out.md"
    rc = main(["run", "--suite", _suite_file(tmp_path), "--markdown", str(out)])
    assert rc == 0
    assert "smoke" in out.read_text()


def test_cli_run_gate_fails_against_higher_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("prismal.eval.__main__.EvalRunner", _FakeRunner)
    # _FakeRunner returns pass_rate=1.0; a baseline that also demands 1.0 but the
    # run drops would regress — here we force a regression via a baseline that
    # records a metric the run cannot beat.
    base = tmp_path / "base.json"
    base.write_text(to_json(_card(suite="smoke", pass_rate=1.0)))
    # Make the run "worse" by patching the runner to return a lower pass_rate.

    class _WorseRunner(_FakeRunner):
        async def run_set(self, eval_set: Any) -> Scorecard:
            return _card(suite=eval_set.suite, pass_rate=0.4)

    monkeypatch.setattr("prismal.eval.__main__.EvalRunner", _WorseRunner)
    rc = main(["run", "--suite", _suite_file(tmp_path), "--baseline", str(base)])
    assert rc == 1


def test_cli_unknown_command_errors() -> None:
    with pytest.raises(SystemExit):
        main(["frobnicate"])
