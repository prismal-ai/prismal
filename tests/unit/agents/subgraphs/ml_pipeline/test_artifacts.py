"""Unit tests for ML pipeline typed artifacts."""

import pytest
from pydantic import ValidationError

from prismal.agents.subgraphs.ml_pipeline.artifacts import (
    DatasetProfile,
    EDAReport,
    EvaluationReport,
    FeatureSet,
    ModelPackage,
    TrainedModel,
)


def test_dataset_profile_requires_fields() -> None:
    """DatasetProfile validates required fields."""
    profile = DatasetProfile(
        name="iris",
        path="data/iris.csv",
        rows=150,
        columns=5,
        column_types={"sepal_length": "float64", "species": "object"},
        null_counts={"sepal_length": 0},
        task_type="classification",
    )
    assert profile.rows == 150
    assert profile.target_column is None


def test_dataset_profile_invalid_task_type() -> None:
    """DatasetProfile rejects invalid task_type."""
    with pytest.raises(ValidationError):
        DatasetProfile(
            name="x",
            path="x.csv",
            rows=10,
            columns=2,
            column_types={},
            null_counts={},
            task_type="invalid_task",  # type: ignore[arg-type]
        )


def test_eda_report_defaults() -> None:
    """EDAReport has sensible defaults."""
    report = EDAReport(
        correlations={"age": 0.45},
        outlier_columns=[],
        missing_pattern="MCAR",
        class_balance="balanced",
        recommended_transforms=["StandardScaler"],
        chart_paths=["data/workspace/ml_models/test/eda/dist.png"],
    )
    assert report.missing_pattern == "MCAR"


def test_feature_set_shape_validation() -> None:
    """FeatureSet stores correct train/test shapes."""
    fs = FeatureSet(
        original_features=["a", "b"],
        engineered_features=["a", "b", "a_b"],
        selected_features=["a", "b"],
        encoding_map={"b": "one-hot"},
        scaling_method="StandardScaler",
        train_shape=(120, 2),
        test_shape=(30, 2),
    )
    assert fs.train_shape[0] == 120


def test_trained_model_random_seed_default() -> None:
    """TrainedModel defaults random_seed to 42."""
    model = TrainedModel(
        model_type="LightGBM",
        hyperparameters={"n_estimators": 100},
        training_time_seconds=12.5,
        framework="flaml",
        task="classification",
        model_path="data/workspace/ml_models/test/model.joblib",
    )
    assert model.random_seed == 42


def test_evaluation_report_primary_score() -> None:
    """EvaluationReport exposes primary_score as float."""
    report = EvaluationReport(
        metrics={"f1": 0.85, "auc": 0.91},
        primary_metric="f1",
        primary_score=0.85,
        feature_importance={"age": 0.3},
        chart_paths=[],
        recommendation="deploy",
    )
    assert report.primary_score == 0.85


def test_model_package_fields() -> None:
    """ModelPackage captures all export metadata."""
    pkg = ModelPackage(
        model_path="data/workspace/ml_models/test/model.joblib",
        format="joblib",
        inference_code_path="data/workspace/ml_models/test/predict.py",
        model_card="# Model Card\nTest model.",
        dependencies=["scikit-learn>=1.5.0"],
        input_schema={"age": "float"},
        output_schema={"prediction": "int"},
    )
    assert pkg.format == "joblib"
