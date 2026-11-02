"""``python -m prismal.decisions cases-check | collect`` (Phase DEC — DEC3-07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Imported at collection: litellm (pulled in by the meter) loads ``.env`` into
# os.environ on first import; the autouse conftest fixture then scrubs it per test.
import prismal.budget.meter  # noqa: F401
from prismal.decisions import cli
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calset import resolve_question
from prismal.decisions.types import Decision


def _cases(tmp_path: Path, labels: list[str]) -> Path:
    path = tmp_path / "route.supervisor.jsonl"
    rows = [
        {
            "id": f"r-{i}",
            "decision_id": "route.supervisor",
            "input": {"user": f"Necesito ayuda con la tarea número {i} del proyecto"},
            "label": label,
            "language": "es",
        }
        for i, label in enumerate(labels)
    ]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return path


def test_cases_check_prints_the_balance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _cases(tmp_path, ["coder", "coder", "END"])
    rc = cli.main(["cases-check", "--cases", str(path), "--decision-id", "route.supervisor"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n"] == 3
    assert report["by_label"] == {"coder": 2, "END": 1}
    assert report["errors"] == []


def test_cases_check_fails_on_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _cases(tmp_path, ["coder", "chef"])
    rc = cli.main(["cases-check", "--cases", str(path), "--decision-id", "route.supervisor"])
    assert rc == 1
    assert "label 'chef'" in capsys.readouterr().out


class _PinnedFake(FakeDecisionAdapter):
    """A fake that, like the Ollama adapter, exposes ``model_name`` + a preflight digest."""

    model_name = "tev1:4b"

    class _Preflight:
        ok = True
        digest12 = "9b5bb969e46c"

    preflight = _Preflight()


def _fake_factory(calls: list[dict[str, Any]]) -> Any:
    q = resolve_question("route.supervisor")

    def factory(*, tier: str, settings: Any) -> FakeDecisionAdapter:
        calls.append({"tier": tier})
        script = {
            q.id: lambda _s: Decision.from_probabilities(
                q,
                {lab: (0.9 if lab == "coder" else 0.1 / 15) for lab in q.labels()},
                backend="ollama:fast",
                model="tev1:4b",
            )
        }
        return _PinnedFake(script, name=f"ollama:{tier}", model="tev1:4b")

    return factory


def test_collect_writes_files_and_prints_the_calibrate_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_collect_backend", _fake_factory(calls))
    path = _cases(tmp_path, ["coder", "END", "coder"])
    out_dir = tmp_path / "runs"
    rc = cli.main(
        [
            "collect",
            "--cases",
            str(path),
            "--decision-id",
            "route.supervisor",
            "--tier",
            "fast",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert calls == [{"tier": "fast"}]
    stem = "route.supervisor@tev1:4b@9b5bb969e46c"
    assert (out_dir / f"{stem}.labeled.jsonl").exists()
    assert (out_dir / f"{stem}.preds.jsonl").exists()
    summary = json.loads((out_dir / f"{stem}.summary.json").read_text("utf-8"))
    assert summary["accuracy"] == pytest.approx(2 / 3)
    out = capsys.readouterr().out
    assert "python -m prismal.decisions calibrate" in out
    assert "--model tev1:4b@9b5bb969e46c" in out


def test_collect_refuses_a_case_set_with_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_collect_backend", _fake_factory(calls))
    path = _cases(tmp_path, ["chef"])
    rc = cli.main(
        [
            "collect",
            "--cases",
            str(path),
            "--decision-id",
            "route.supervisor",
            "--tier",
            "fast",
            "--out-dir",
            str(tmp_path / "runs"),
        ]
    )
    assert rc == 1
    assert calls == []  # no model is asked before the set is valid


def test_collect_limit_takes_the_first_n_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_collect_backend", _fake_factory([]))
    path = _cases(tmp_path, ["coder"] * 5)
    out_dir = tmp_path / "runs"
    rc = cli.main(
        [
            "collect",
            "--cases",
            str(path),
            "--decision-id",
            "route.supervisor",
            "--tier",
            "fast",
            "--out-dir",
            str(out_dir),
            "--limit",
            "2",
        ]
    )
    assert rc == 0
    preds = (out_dir / "route.supervisor@tev1:4b@9b5bb969e46c.preds.jsonl").read_text("utf-8")
    assert len(preds.splitlines()) == 2


def test_collect_backend_is_a_single_question_calibration_adapter() -> None:
    """The real factory pins one question per request, no p95 skipping, no stored records."""
    from prismal.core.config import Settings

    adapter = cli._collect_backend(tier="accurate", settings=Settings())
    assert adapter.name == "ollama:accurate"
    assert adapter.model_name == "nimble:9b-q4_K_M"
    assert adapter._max_batch == 1
    assert adapter._calibration_dir is None
    assert adapter._max_p95_ms >= 10**6
