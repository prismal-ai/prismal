"""``python -m prismal.decisions`` CLI (Phase DEC — DEC3-06 calibrate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Imported at collection: litellm (pulled in by the meter) loads ``.env`` into
# os.environ on first import; the autouse conftest fixture then scrubs it per test.
import prismal.budget.meter  # noqa: F401
from prismal.decisions.calibration import CalibrationStore
from prismal.decisions.cli import main


def _write(tmp_path: Path, n: int = 400) -> tuple[Path, Path]:
    labeled = tmp_path / "labeled.jsonl"
    preds = tmp_path / "preds.jsonl"
    with labeled.open("w") as lf, preds.open("w") as pf:
        for i in range(n):
            lang = "es" if i % 2 else "en"
            label = "a" if i % 10 else "b"  # 90 % 'a'
            lf.write(
                json.dumps(
                    {"state": {"u": str(i)}, "question_id": "q", "label": label, "language": lang}
                )
                + "\n"
            )
            pf.write(
                json.dumps(
                    {"question_id": "q", "kind": "choice", "probabilities": {"a": 0.99, "b": 0.01}}
                )
                + "\n"
            )
    return labeled, preds


def test_calibrate_writes_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    labeled, preds = _write(tmp_path)
    out_dir = tmp_path / "cal"
    rc = main(
        [
            "calibrate",
            "--labeled",
            str(labeled),
            "--predictions",
            str(preds),
            "--decision-id",
            "q",
            "--model",
            "laya-multilingual",
            "--method",
            "temperature",
            "--target-precision",
            "0.85",
            "--floor",
            "0.85",
            "--version",
            "v1",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    record = CalibrationStore(out_dir).get("q", "laya-multilingual")
    assert record is not None
    assert record.version == "v1"
    assert record.ece_after < record.ece_before
    assert record.n_by_language == {"en": 200, "es": 200}
    assert "ece_after" in capsys.readouterr().out


def test_calibrate_rejects_mismatched_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labeled, preds = _write(tmp_path, 10)
    preds.write_text(preds.read_text().splitlines()[0] + "\n")
    rc = main(
        [
            "calibrate",
            "--labeled",
            str(labeled),
            "--predictions",
            str(preds),
            "--decision-id",
            "q",
            "--model",
            "m",
            "--out-dir",
            str(tmp_path / "x"),
        ]
    )
    assert rc == 2
    assert "predictions" in capsys.readouterr().err


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2


async def test_run_bench_with_fake_backend() -> None:
    from prismal.decisions.adapters.fake import FakeDecisionAdapter
    from prismal.decisions.cli import run_bench

    report = await run_bench(FakeDecisionAdapter({}), n=2)
    assert report["requests"] == 8
    assert report["abstain_ratio"] == 1.0  # unscripted fake abstains
    assert report["backend"] == "fake"


def test_bench_unconfigured_backend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from prismal.core import config
    from prismal.core.config import Settings

    monkeypatch.setattr(config, "get_settings", Settings)
    assert main(["bench", "--backend", "jev"]) == 2
    assert "not configured" in capsys.readouterr().err


def test_bench_command_prints_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from prismal.core import config
    from prismal.core.config import Settings
    from prismal.decisions.adapters import factory
    from prismal.decisions.adapters.fake import FakeDecisionAdapter

    monkeypatch.setattr(config, "get_settings", Settings)
    monkeypatch.setattr(factory, "build_backend", lambda *_a, **_k: FakeDecisionAdapter({}))
    assert main(["bench", "--backend", "laya", "--n", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["requests"] == 4
    assert out["cost_usd"] == 0.0
