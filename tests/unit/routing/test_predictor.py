"""TableSuccessPredictor, load/export of the versioned table (SPEC-AMR-PRD-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismal.core.exceptions import RoutingConfigError
from prismal.decisions.types import RiskTier
from prismal.eval.types import CaseResult, EvalCase, Scorecard, Trajectory
from prismal.routing.ports import SuccessPredictorPort
from prismal.routing.predictor import (
    PredictorCell,
    TableSuccessPredictor,
    export_predictor_table,
    load_predictor_table,
)
from prismal.routing.types import ModelTier, TaskFeatures

_TIERS = {
    "nano-m": ModelTier.NANO,
    "small-m": ModelTier.SMALL,
    "medium-m": ModelTier.MEDIUM,
    "frontier-m": ModelTier.FRONTIER,
    "small-alt": ModelTier.SMALL,
}


def _tier_of(ref: str) -> ModelTier:
    return _TIERS[ref]


def _f(intent: str | None = "code") -> TaskFeatures:
    return TaskFeatures(
        agent="coder",
        intent=intent,
        est_input_tokens=100,
        needs_tools=False,
        needs_structured_output=False,
        risk_tier=RiskTier.LOW,
    )


def _pred(**kw: object) -> TableSuccessPredictor:
    table = {
        ("code", ModelTier.SMALL): PredictorCell(successes=8, trials=10, median_output_tokens=300),
        ("chat", ModelTier.SMALL): PredictorCell(successes=2, trials=10, median_output_tokens=100),
        ("_", ModelTier.MEDIUM): PredictorCell(successes=5, trials=8, median_output_tokens=0),
    }
    return TableSuccessPredictor(table, tier_of=_tier_of, **kw)  # type: ignore[arg-type]


class TestPSuccess:
    def test_conforms_to_port(self) -> None:
        assert isinstance(_pred(), SuccessPredictorPort)

    def test_beta_posterior_mean_for_known_cell(self) -> None:
        # (s + a) / (n + a + b) = (8 + 1) / (10 + 2)
        assert _pred().p_success(_f("code"), "small-m") == pytest.approx(9 / 12)

    def test_prior_parameters_are_honoured(self) -> None:
        p = _pred(prior_a=2.0, prior_b=3.0)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(10 / 15)

    def test_unknown_intent_uses_underscore_cell(self) -> None:
        assert _pred().p_success(_f(None), "medium-m") == pytest.approx(6 / 10)

    def test_missing_cell_falls_back_to_tier_row_prior_mean(self) -> None:
        # tier row SMALL aggregates (8+2)/(10+10) -> (10 + 1) / (20 + 2)
        assert _pred().p_success(_f("other"), "small-m") == pytest.approx(11 / 22)

    def test_empty_tier_row_falls_back_to_prior(self) -> None:
        assert _pred().p_success(_f("code"), "frontier-m") == pytest.approx(0.5)

    def test_unknown_model_never_raises_and_returns_prior(self) -> None:
        assert _pred().p_success(_f("code"), "mystery") == pytest.approx(0.5)

    def test_score_blend(self) -> None:
        p = _pred(score_fn=lambda f, m: 1.0, score_weight=0.5)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(0.5 * 0.75 + 0.5)

    def test_score_none_or_error_keeps_table_value(self) -> None:
        def boom(f: TaskFeatures, m: str) -> float:
            raise RuntimeError("down")

        assert _pred(score_fn=lambda f, m: None, score_weight=0.5).p_success(
            _f("code"), "small-m"
        ) == pytest.approx(0.75)
        assert _pred(score_fn=boom, score_weight=0.5).p_success(
            _f("code"), "small-m"
        ) == pytest.approx(0.75)

    def test_score_is_clamped(self) -> None:
        p = _pred(score_fn=lambda f, m: 7.0, score_weight=1.0)
        assert p.p_success(_f("code"), "small-m") == 1.0

    def test_invalid_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            _pred(score_weight=1.5)

    def test_invalid_prior_rejected(self) -> None:
        with pytest.raises(ValueError):
            _pred(prior_a=0.0)


class TestUpdate:
    def test_update_moves_posterior(self) -> None:
        p = _pred()
        p.update(_f("code"), "small-m", True)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(10 / 13)
        p.update(_f("code"), "small-alt", False)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(10 / 14)

    def test_update_creates_missing_cell(self) -> None:
        p = _pred()
        p.update(_f("new"), "frontier-m", True)
        assert p.p_success(_f("new"), "frontier-m") == pytest.approx(2 / 3)

    def test_served_model_attribution(self) -> None:
        p = _pred()
        p.update(_f("code"), "small-m", True, served_model="frontier-m")
        assert p.p_success(_f("code"), "frontier-m") == pytest.approx(2 / 3)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(9 / 12)

    def test_update_unknown_model_is_ignored(self) -> None:
        p = _pred()
        p.update(_f("code"), "mystery", True)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(9 / 12)


class TestOutputTokens:
    def test_cell_median(self) -> None:
        assert _pred().expected_output_tokens(_f("code"), "small-m") == 300

    def test_tier_row_median_when_cell_missing(self) -> None:
        # medians of SMALL row: 300, 100 -> 200
        assert _pred().expected_output_tokens(_f("other"), "small-m") == 200

    def test_default_when_nothing_known(self) -> None:
        p = _pred(default_output_tokens=42)
        assert p.expected_output_tokens(_f("code"), "medium-m") == 42
        assert p.expected_output_tokens(_f("code"), "mystery") == 42


def _write(tmp_path: Path, payload: object) -> str:
    path = tmp_path / "v9.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestLoad:
    def test_load_roundtrip(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "version": "v9",
                "prior": {"a": 1.0, "b": 1.0},
                "model_tiers": {"m-small": "small"},
                "cells": [
                    {
                        "intent": "code",
                        "tier": "small",
                        "successes": 3,
                        "trials": 4,
                        "median_output_tokens": 77,
                    }
                ],
            },
        )
        p = load_predictor_table(path)
        assert p.version == "v9"
        assert p.p_success(_f("code"), "m-small") == pytest.approx(4 / 6)
        assert p.expected_output_tokens(_f("code"), "m-small") == 77

    def test_injected_tier_of_used_for_unlisted_models(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "version": "v9",
                "cells": [{"intent": "_", "tier": "medium", "successes": 9, "trials": 9}],
            },
        )
        p = load_predictor_table(path, tier_of=_tier_of)
        assert p.p_success(_f(None), "medium-m") == pytest.approx(10 / 11)

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"cells": []},
            {"version": "v1", "cells": [{"intent": "x", "tier": "huge", "trials": 1}]},
            {"version": "v1", "cells": [{"intent": "x", "tier": "auto", "trials": 1}]},
            {"version": "v1", "cells": [{"intent": "x", "tier": "small", "successes": 3}]},
            {"version": "v1", "cells": [{"tier": "small", "successes": 3, "trials": 2}]},
            {"version": "v1", "cells": [], "model_tiers": {"m": "huge"}},
        ],
    )
    def test_invalid_tables_rejected(self, tmp_path: Path, payload: object) -> None:
        with pytest.raises(RoutingConfigError):
            load_predictor_table(_write(tmp_path, payload))

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(RoutingConfigError):
            load_predictor_table(str(tmp_path / "nope.json"))

    def test_shipped_default_table_loads(self) -> None:
        root = Path(__file__).resolve().parents[3]
        p = load_predictor_table(str(root / "config" / "routing_predictor" / "v1.json"))
        assert p.version == "v1"
        assert 0.0 <= p.p_success(_f("code"), "unknown-model") <= 1.0


def _traj(case_id: str, tokens: int, prompt: int) -> Trajectory:
    return Trajectory(
        case_id=case_id,
        final_answer="",
        steps=[],
        visited_nodes=[],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=tokens,
        latency_ms=1.0,
        terminated=True,
        prompt_tokens=prompt,
    )


def _scorecard() -> Scorecard:
    results = [
        CaseResult("c1", True, _traj("c1", 150, 100), []),
        CaseResult("c2", False, _traj("c2", 400, 100), []),
        CaseResult("c3", True, _traj("c3", 120, 100), []),
        CaseResult("c4", True, _traj("c4", 50, 0), []),
    ]
    return Scorecard(
        suite="core",
        version="2026.10",
        pass_rate=0.75,
        avg_steps=1.0,
        tool_error_rate=0.0,
        avg_cost_usd=0.0,
        avg_latency_ms=1.0,
        cases=results,
    )


class TestExport:
    def test_export_then_load(self, tmp_path: Path) -> None:
        cases = [
            EvalCase(id="c1", input="", assertions=[], tags=["intent:code"]),
            EvalCase(id="c2", input="", assertions=[], tags=["smoke", "intent:code"]),
            EvalCase(id="c3", input="", assertions=[], tags=[]),
        ]
        path = export_predictor_table(
            _scorecard(),
            out_dir=str(tmp_path),
            tier_of=_tier_of,
            model_ref="small-m",
            cases=cases,
            version="v2",
        )
        assert Path(path) == tmp_path / "v2.json"
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert payload["version"] == "v2"
        assert payload["model_tiers"] == {"small-m": "small"}
        cells = {(c["intent"], c["tier"]): c for c in payload["cells"]}
        assert cells[("code", "small")]["successes"] == 1
        assert cells[("code", "small")]["trials"] == 2
        assert cells[("code", "small")]["median_output_tokens"] == 175  # median(50, 300)
        assert cells[("_", "small")]["trials"] == 2  # c3 untagged + c4 unknown case
        assert cells[("_", "small")]["median_output_tokens"] == 20  # c4 has no prompt split
        p = load_predictor_table(path)
        assert p.p_success(_f("code"), "small-m") == pytest.approx(2 / 4)

    def test_export_default_version_and_auto_rejected(self, tmp_path: Path) -> None:
        path = export_predictor_table(
            _scorecard(), out_dir=str(tmp_path), tier_of=_tier_of, model_ref="small-m"
        )
        assert Path(path).name == "core-2026.10.json"
        with pytest.raises(RoutingConfigError):
            export_predictor_table(
                _scorecard(),
                out_dir=str(tmp_path),
                tier_of=lambda r: ModelTier.AUTO,
                model_ref="x",
            )

    def test_export_rejects_path_traversal_version(self, tmp_path: Path) -> None:
        with pytest.raises(RoutingConfigError):
            export_predictor_table(
                _scorecard(),
                out_dir=str(tmp_path),
                tier_of=_tier_of,
                model_ref="small-m",
                version="../evil",
            )
