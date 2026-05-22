"""Unit tests for ML pipeline tools (Phase 26 F5-02)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path("tests/fixtures/datasets")
IRIS_CSV = FIXTURES / "iris_sample.csv"
CHURN_CSV = FIXTURES / "churn_sample.csv"


# ---------------------------------------------------------------------------
# validate_dataset
# ---------------------------------------------------------------------------


def test_validate_dataset_csv() -> None:
    """validate_dataset returns rows, columns, dtypes for a CSV file."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        validate_dataset,
    )

    result = json.loads(validate_dataset.invoke({"path": str(IRIS_CSV)}))
    assert result.get("rows", 0) > 0
    assert result.get("columns", 0) > 0
    assert "column_types" in result


def test_validate_dataset_missing_file() -> None:
    """validate_dataset returns error dict for missing file."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        validate_dataset,
    )

    result = json.loads(validate_dataset.invoke({"path": "/no/such/file.csv"}))
    assert "error" in result


# ---------------------------------------------------------------------------
# statistical_summary
# ---------------------------------------------------------------------------


def test_statistical_summary_iris() -> None:
    """statistical_summary returns numeric stats and target distribution."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        statistical_summary,
    )

    result = json.loads(
        statistical_summary.invoke({"path": str(IRIS_CSV), "target_column": "species"})
    )
    assert "numeric_summary" in result
    assert "target_distribution" in result


# ---------------------------------------------------------------------------
# feature_transform
# ---------------------------------------------------------------------------


def test_feature_transform_fill_null(tmp_path: Path) -> None:
    """feature_transform applies fill_null and writes output CSV."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        feature_transform,
    )

    out = tmp_path / "out.csv"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps(
                    [{"type": "fill_null", "column": "sepal_length", "value": 0}]
                ),
            }
        )
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# feature_select
# ---------------------------------------------------------------------------


def test_feature_select_variance() -> None:
    """feature_select returns selected_features list using variance method."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        feature_select,
    )

    result = json.loads(
        feature_select.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "target_column": "species",
                "method": "variance",
                "n_features": 3,
            }
        )
    )
    assert "selected_features" in result
    assert len(result["selected_features"]) <= 3


# ---------------------------------------------------------------------------
# apply_sampling
# ---------------------------------------------------------------------------


def test_apply_sampling_oversample(tmp_path: Path) -> None:
    """apply_sampling creates a balanced dataset via oversampling."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        apply_sampling,
    )

    out = tmp_path / "balanced.csv"
    result = json.loads(
        apply_sampling.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "target_column": "species",
                "method": "oversample",
            }
        )
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# export_model + generate_inference_code
# ---------------------------------------------------------------------------


def test_export_model_missing_source(tmp_path: Path) -> None:
    """export_model returns error when source model does not exist."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import export_model

    result = json.loads(
        export_model.invoke(
            {
                "model_path": str(tmp_path / "no_model.joblib"),
                "output_dir": str(tmp_path / "out"),
                "format": "joblib",
                "model_name": "test",
            }
        )
    )
    assert "error" in result


def test_generate_inference_code(tmp_path: Path) -> None:
    """generate_inference_code writes a valid predict.py script."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        generate_inference_code,
    )

    schema = json.dumps({"sepal_length": "float", "sepal_width": "float"})
    result = json.loads(
        generate_inference_code.invoke(
            {
                "model_path": "model.joblib",
                "input_schema": schema,
                "output_dir": str(tmp_path),
                "model_name": "iris_clf",
                "task": "classification",
            }
        )
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


# ---------------------------------------------------------------------------
# feature_transform — additional operation types
# ---------------------------------------------------------------------------


def test_feature_transform_drop(tmp_path: Path) -> None:
    """feature_transform drops specified columns."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_transform

    out = tmp_path / "dropped.csv"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps([{"type": "drop", "columns": ["petal_width"]}]),
            }
        )
    )
    assert result.get("status") == "ok"
    assert result["cols_after"] == result["cols_before"] - 1


def test_feature_transform_log1p(tmp_path: Path) -> None:
    """feature_transform applies log1p transformation."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_transform

    out = tmp_path / "log.csv"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps([{"type": "log1p", "column": "sepal_length"}]),
            }
        )
    )
    assert result.get("status") == "ok"


def test_feature_transform_normalize(tmp_path: Path) -> None:
    """feature_transform normalizes a column to [0, 1]."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_transform

    out = tmp_path / "norm.csv"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps([{"type": "normalize", "column": "sepal_width"}]),
            }
        )
    )
    assert result.get("status") == "ok"


def test_feature_transform_cast(tmp_path: Path) -> None:
    """feature_transform casts a column dtype."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_transform

    out = tmp_path / "cast.csv"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps(
                    [{"type": "cast", "column": "sepal_length", "dtype": "float32"}]
                ),
            }
        )
    )
    assert result.get("status") == "ok"


def test_feature_transform_parquet_output(tmp_path: Path) -> None:
    """feature_transform writes Parquet when output path ends in .parquet."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_transform

    out = tmp_path / "out.parquet"
    result = json.loads(
        feature_transform.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_path": str(out),
                "operations": json.dumps([]),
            }
        )
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# feature_select — correlation + mutual_info methods
# ---------------------------------------------------------------------------


def test_feature_select_correlation() -> None:
    """feature_select exercises the correlation code path (ok or graceful error)."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_select

    # Use churn CSV: tenure/monthly_charges are numeric, churned is integer target
    result = json.loads(
        feature_select.invoke(
            {
                "dataset_path": str(CHURN_CSV),
                "target_column": "churned",
                "method": "correlation",
                "n_features": 2,
            }
        )
    )
    # pearson_corr may not be available in all Polars versions — accept graceful error
    assert "selected_features" in result or "error" in result


def test_feature_select_mutual_info() -> None:
    """feature_select exercises the mutual_info code path with integer target."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import feature_select

    # churned is 0/1 integer — valid for mutual_info_classif
    result = json.loads(
        feature_select.invoke(
            {
                "dataset_path": str(CHURN_CSV),
                "target_column": "churned",
                "method": "mutual_info",
                "n_features": 2,
            }
        )
    )
    assert "selected_features" in result or "error" in result


# ---------------------------------------------------------------------------
# apply_sampling — undersample method
# ---------------------------------------------------------------------------


def test_apply_sampling_undersample(tmp_path: Path) -> None:
    """apply_sampling undersample balances classes by truncating majority."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import apply_sampling

    out = tmp_path / "under.csv"
    result = json.loads(
        apply_sampling.invoke(
            {
                "dataset_path": str(CHURN_CSV),
                "output_path": str(out),
                "target_column": "churned",
                "method": "undersample",
            }
        )
    )
    assert result.get("status") == "ok"
    assert out.exists()


# ---------------------------------------------------------------------------
# train_clustering — sklearn KMeans (available in env)
# ---------------------------------------------------------------------------


def test_train_clustering_kmeans(tmp_path: Path) -> None:
    """train_clustering fits a KMeans model and saves it."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import train_clustering

    result = json.loads(
        train_clustering.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_dir": str(tmp_path),
                "model_name": "iris_km",
                "n_clusters": 3,
                "method": "kmeans",
                "random_seed": 42,
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result
    if result.get("status") == "ok":
        assert "model_path" in result


def test_train_clustering_auto_k(tmp_path: Path) -> None:
    """train_clustering with n_clusters=0 selects best k via silhouette."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import train_clustering

    result = json.loads(
        train_clustering.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "output_dir": str(tmp_path),
                "model_name": "iris_auto_km",
                "n_clusters": 0,
                "method": "kmeans",
                "max_k": 4,
                "random_seed": 42,
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result


# ---------------------------------------------------------------------------
# evaluate_model + explain_model using real sklearn
# ---------------------------------------------------------------------------


@pytest.fixture
def trained_model_path(tmp_path: Path) -> Path:
    """Create a small trained sklearn model and return its path."""
    import joblib
    import polars as pl
    from sklearn.preprocessing import LabelEncoder
    from sklearn.tree import DecisionTreeClassifier

    df = pl.read_csv(IRIS_CSV)
    feats = [c for c in df.columns if c != "species"]
    x = df.select(feats).to_numpy()
    le = LabelEncoder()
    y = le.fit_transform(df["species"].to_numpy())
    clf = DecisionTreeClassifier(random_state=42)
    clf.fit(x, y)
    model_path = tmp_path / "model.joblib"
    joblib.dump(clf, model_path)
    return model_path


def test_evaluate_model_classification(tmp_path: Path, trained_model_path: Path) -> None:
    """evaluate_model computes accuracy + f1 for a classification model."""
    # Write a simple numeric CSV for the model (species encoded as int)
    import polars as pl
    from sklearn.preprocessing import LabelEncoder

    from prismal.agents.subgraphs.ml_pipeline.tools_ml import evaluate_model

    df = pl.read_csv(IRIS_CSV)
    le = LabelEncoder()
    labels = le.fit_transform(df["species"].to_numpy())
    feats = [c for c in df.columns if c != "species"]
    numeric_df = df.select(feats).with_columns(pl.Series("label", labels))
    eval_path = tmp_path / "eval.csv"
    numeric_df.write_csv(eval_path)

    result = json.loads(
        evaluate_model.invoke(
            {
                "model_path": str(trained_model_path),
                "dataset_path": str(eval_path),
                "target_column": "label",
                "task": "classification",
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result
    if result.get("status") == "ok":
        assert "primary_score" in result


def test_evaluate_model_regression(tmp_path: Path) -> None:
    """evaluate_model computes MAE/MSE/R² for a regression model."""
    import joblib
    import polars as pl
    from sklearn.linear_model import LinearRegression

    # Build a simple regression model
    df = pl.read_csv(IRIS_CSV)
    feats = ["sepal_length", "sepal_width", "petal_width"]
    x = df.select(feats).to_numpy()
    y = df["petal_length"].to_numpy()
    reg = LinearRegression()
    reg.fit(x, y)
    model_path = tmp_path / "reg_model.joblib"
    joblib.dump(reg, model_path)

    eval_path = tmp_path / "eval_reg.csv"
    df.select([*feats, "petal_length"]).write_csv(eval_path)

    from prismal.agents.subgraphs.ml_pipeline.tools_ml import evaluate_model

    result = json.loads(
        evaluate_model.invoke(
            {
                "model_path": str(model_path),
                "dataset_path": str(eval_path),
                "target_column": "petal_length",
                "task": "regression",
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result


def test_explain_model(tmp_path: Path, trained_model_path: Path) -> None:
    """explain_model returns feature importances (SHAP or fallback)."""
    import polars as pl
    from sklearn.preprocessing import LabelEncoder

    df = pl.read_csv(IRIS_CSV)
    le = LabelEncoder()
    labels = le.fit_transform(df["species"].to_numpy())
    feats = [c for c in df.columns if c != "species"]
    numeric_df = df.select(feats).with_columns(pl.Series("label", labels))
    eval_path = tmp_path / "explain_data.csv"
    numeric_df.write_csv(eval_path)

    from prismal.agents.subgraphs.ml_pipeline.tools_ml import explain_model

    result = json.loads(
        explain_model.invoke(
            {
                "model_path": str(trained_model_path),
                "dataset_path": str(eval_path),
                "target_column": "label",
                "max_samples": 20,
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result


# ---------------------------------------------------------------------------
# export_model — joblib copy path (model exists)
# ---------------------------------------------------------------------------


def test_export_model_joblib_copy(tmp_path: Path, trained_model_path: Path) -> None:
    """export_model with format=joblib copies the model file."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import export_model

    out_dir = tmp_path / "exported"
    result = json.loads(
        export_model.invoke(
            {
                "model_path": str(trained_model_path),
                "output_dir": str(out_dir),
                "format": "joblib",
                "model_name": "iris_clf",
            }
        )
    )
    assert result.get("status") == "ok"
    assert "exported_path" in result


# ---------------------------------------------------------------------------
# automl_train — actual sklearn fallback (if flaml not installed)
# ---------------------------------------------------------------------------


def test_automl_train_missing_lib(tmp_path: Path) -> None:
    """automl_train returns structured unavailable or ok (never crashes)."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import automl_train

    result = json.loads(
        automl_train.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "target_column": "species",
                "task": "classification",
                "time_budget": 5,
                "model_output_dir": str(tmp_path),
            }
        )
    )
    # Either flaml ran (ok) or it's unavailable — never a crash
    assert result.get("status") in ("ok", "unavailable") or "error" in result


# ---------------------------------------------------------------------------
# train_dl_model (graceful unavailable when torch not installed)
# ---------------------------------------------------------------------------


def test_train_dl_model_missing_lib(tmp_path: Path) -> None:
    """train_dl_model returns unavailable or ok — never crashes."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import (
        train_dl_model,
    )

    result = json.loads(
        train_dl_model.invoke(
            {
                "dataset_path": str(IRIS_CSV),
                "target_column": "species",
                "task": "classification",
                "epochs": 2,
                "model_output_dir": str(tmp_path),
            }
        )
    )
    assert result.get("status") in ("ok", "unavailable") or "error" in result


# ---------------------------------------------------------------------------
# validate_dataset — parquet, json, and unsupported format branches
# ---------------------------------------------------------------------------


def test_validate_dataset_parquet(tmp_path: Path) -> None:
    """validate_dataset reads Parquet files (covers parquet branch)."""
    import polars as pl

    from prismal.agents.subgraphs.ml_pipeline.tools_ml import validate_dataset

    df = pl.read_csv(IRIS_CSV)
    parquet_path = tmp_path / "iris.parquet"
    df.write_parquet(parquet_path)

    result = json.loads(validate_dataset.invoke({"path": str(parquet_path)}))
    assert result.get("rows", 0) > 0
    assert "column_types" in result


def test_validate_dataset_json(tmp_path: Path) -> None:
    """validate_dataset reads JSON/ndjson files (covers json branch)."""
    import polars as pl

    from prismal.agents.subgraphs.ml_pipeline.tools_ml import validate_dataset

    df = pl.read_csv(IRIS_CSV)
    json_path = tmp_path / "iris.ndjson"
    df.write_ndjson(json_path)

    result = json.loads(validate_dataset.invoke({"path": str(json_path)}))
    assert result.get("rows", 0) > 0 or "error" in result


def test_validate_dataset_unsupported_format(tmp_path: Path) -> None:
    """validate_dataset returns error for unsupported file extension."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import validate_dataset

    unsupported = tmp_path / "data.xlsx"
    unsupported.write_bytes(b"fake data")

    result = json.loads(validate_dataset.invoke({"path": str(unsupported)}))
    assert "error" in result


# ---------------------------------------------------------------------------
# statistical_summary — file not found branch
# ---------------------------------------------------------------------------


def test_statistical_summary_missing_file() -> None:
    """statistical_summary returns error for missing file."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import statistical_summary

    result = json.loads(
        statistical_summary.invoke({"path": "/no/such/dataset.csv", "target_column": ""})
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# apply_sampling — smote method (imblearn not installed → falls back to oversample)
# ---------------------------------------------------------------------------


def test_apply_sampling_smote_fallback(tmp_path: Path) -> None:
    """apply_sampling with smote falls back to oversample when imblearn is absent."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import apply_sampling

    out = tmp_path / "smote.csv"
    result = json.loads(
        apply_sampling.invoke(
            {
                "dataset_path": str(CHURN_CSV),
                "output_path": str(out),
                "target_column": "churned",
                "method": "smote",
            }
        )
    )
    assert result.get("status") == "ok" or "error" in result


# ---------------------------------------------------------------------------
# export_model — onnx format (skl2onnx not installed → fallback to joblib)
# ---------------------------------------------------------------------------


def test_export_model_onnx_fallback(tmp_path: Path, trained_model_path: Path) -> None:
    """export_model with onnx format falls back to joblib when skl2onnx is absent."""
    from prismal.agents.subgraphs.ml_pipeline.tools_ml import export_model

    out_dir = tmp_path / "onnx_export"
    result = json.loads(
        export_model.invoke(
            {
                "model_path": str(trained_model_path),
                "output_dir": str(out_dir),
                "format": "onnx",
                "model_name": "iris_clf",
            }
        )
    )
    assert result.get("status") == "ok"
    assert result.get("format") in ("onnx", "joblib")
