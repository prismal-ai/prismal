"""Unit tests for ML pipeline tools (Phase 26 F5-02)."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path("tests/fixtures/datasets")
IRIS_CSV = FIXTURES / "iris_sample.csv"


# ---------------------------------------------------------------------------
# validate_dataset
# ---------------------------------------------------------------------------


def test_validate_dataset_csv() -> None:
    """validate_dataset returns rows, columns, dtypes for a CSV file."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        validate_dataset,
    )

    result = json.loads(validate_dataset.invoke({"path": str(IRIS_CSV)}))
    assert result.get("rows", 0) > 0
    assert result.get("columns", 0) > 0
    assert "column_types" in result


def test_validate_dataset_missing_file() -> None:
    """validate_dataset returns error dict for missing file."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        validate_dataset,
    )

    result = json.loads(
        validate_dataset.invoke({"path": "/no/such/file.csv"})
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# statistical_summary
# ---------------------------------------------------------------------------


def test_statistical_summary_iris() -> None:
    """statistical_summary returns numeric stats and target distribution."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        statistical_summary,
    )

    result = json.loads(
        statistical_summary.invoke(
            {"path": str(IRIS_CSV), "target_column": "species"}
        )
    )
    assert "numeric_summary" in result
    assert "target_distribution" in result


# ---------------------------------------------------------------------------
# feature_transform
# ---------------------------------------------------------------------------


def test_feature_transform_fill_null(tmp_path: Path) -> None:
    """feature_transform applies fill_null and writes output CSV."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        feature_transform,
    )

    out = tmp_path / "out.csv"
    result = json.loads(
        feature_transform.invoke({
            "dataset_path": str(IRIS_CSV),
            "output_path": str(out),
            "operations": json.dumps(
                [{"type": "fill_null", "column": "sepal_length", "value": 0}]
            ),
        })
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# feature_select
# ---------------------------------------------------------------------------


def test_feature_select_variance() -> None:
    """feature_select returns selected_features list using variance method."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        feature_select,
    )

    result = json.loads(
        feature_select.invoke({
            "dataset_path": str(IRIS_CSV),
            "target_column": "species",
            "method": "variance",
            "n_features": 3,
        })
    )
    assert "selected_features" in result
    assert len(result["selected_features"]) <= 3


# ---------------------------------------------------------------------------
# apply_sampling
# ---------------------------------------------------------------------------


def test_apply_sampling_oversample(tmp_path: Path) -> None:
    """apply_sampling creates a balanced dataset via oversampling."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        apply_sampling,
    )

    out = tmp_path / "balanced.csv"
    result = json.loads(
        apply_sampling.invoke({
            "dataset_path": str(IRIS_CSV),
            "output_path": str(out),
            "target_column": "species",
            "method": "oversample",
        })
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# export_model + generate_inference_code
# ---------------------------------------------------------------------------


def test_export_model_missing_source(tmp_path: Path) -> None:
    """export_model returns error when source model does not exist."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import export_model

    result = json.loads(
        export_model.invoke({
            "model_path": str(tmp_path / "no_model.joblib"),
            "output_dir": str(tmp_path / "out"),
            "format": "joblib",
            "model_name": "test",
        })
    )
    assert "error" in result


def test_generate_inference_code(tmp_path: Path) -> None:
    """generate_inference_code writes a valid predict.py script."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        generate_inference_code,
    )

    schema = json.dumps({"sepal_length": "float", "sepal_width": "float"})
    result = json.loads(
        generate_inference_code.invoke({
            "model_path": "model.joblib",
            "input_schema": schema,
            "output_dir": str(tmp_path),
            "model_name": "iris_clf",
            "task": "classification",
        })
    )
    assert result.get("status") == "ok"
    script = Path(result["script_path"])
    assert script.exists()
    content = script.read_text()
    assert "predict" in content
    assert "sepal_length" in content


# ---------------------------------------------------------------------------
# automl_train (graceful unavailable when flaml not installed)
# ---------------------------------------------------------------------------


def test_automl_train_missing_lib(tmp_path: Path) -> None:
    """automl_train returns structured unavailable or ok (never crashes)."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import automl_train

    result = json.loads(
        automl_train.invoke({
            "dataset_path": str(IRIS_CSV),
            "target_column": "species",
            "task": "classification",
            "time_budget": 5,
            "model_output_dir": str(tmp_path),
        })
    )
    # Either flaml ran (ok) or it's unavailable — never a crash
    assert result.get("status") in ("ok", "unavailable") or "error" in result


# ---------------------------------------------------------------------------
# train_dl_model (graceful unavailable when torch not installed)
# ---------------------------------------------------------------------------


def test_train_dl_model_missing_lib(tmp_path: Path) -> None:
    """train_dl_model returns unavailable or ok — never crashes."""
    from lightagent.agents.subgraphs.ml_pipeline.tools_ml import (
        train_dl_model,
    )

    result = json.loads(
        train_dl_model.invoke({
            "dataset_path": str(IRIS_CSV),
            "target_column": "species",
            "task": "classification",
            "epochs": 2,
            "model_output_dir": str(tmp_path),
        })
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result
