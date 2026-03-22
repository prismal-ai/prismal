"""
Typed Pydantic v2 artifact models for the ML/DL pipeline subgraph.

Each artifact represents structured data produced by a ml_pipeline agent node
and stored in ``AgentState.metadata["ml_pipeline"]``.  Agents must never
pass raw dicts between nodes — use these models and call ``.model_dump()``
when persisting to metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DatasetProfile(BaseModel):
    """Data ingester artifact: profile of an ingested dataset."""

    name: str = Field(..., description="Dataset name / identifier")
    path: str = Field(..., description="Absolute or workspace-relative file path")
    rows: int = Field(..., ge=0, description="Number of rows in the dataset")
    columns: int = Field(..., ge=0, description="Number of columns")
    column_types: dict[str, str] = Field(
        ..., description="Column name to dtype string (e.g. 'float64', 'object')"
    )
    null_counts: dict[str, int] = Field(
        default_factory=dict, description="Column name to null count"
    )
    target_column: str | None = Field(
        default=None, description="Detected or user-specified target column"
    )
    task_type: Literal[
        "classification", "regression", "clustering", "time_series"
    ] = Field(..., description="ML task type inferred or specified by user")
    class_distribution: dict[str, int] | None = Field(
        default=None, description="Class label to count (classification only)"
    )


class EDAReport(BaseModel):
    """EDA analyst artifact: exploratory data analysis results."""

    correlations: dict[str, float] = Field(
        default_factory=dict, description="Top feature-to-target correlations"
    )
    outlier_columns: list[str] = Field(
        default_factory=list, description="Columns with detected outliers"
    )
    missing_pattern: Literal["MCAR", "MAR", "MNAR", "none"] = Field(
        default="none", description="Missing-at-random pattern"
    )
    class_balance: Literal[
        "balanced", "imbalanced", "severely_imbalanced", "n/a"
    ] = Field(default="n/a", description="Class balance assessment")
    recommended_transforms: list[str] = Field(
        default_factory=list, description="Suggested preprocessing steps"
    )
    chart_paths: list[str] = Field(
        default_factory=list, description="Paths to generated EDA charts"
    )


class FeatureSet(BaseModel):
    """Feature engineer artifact: feature engineering results."""

    original_features: list[str] = Field(
        default_factory=list, description="Column names before engineering"
    )
    engineered_features: list[str] = Field(
        default_factory=list, description="Column names after engineering"
    )
    selected_features: list[str] = Field(
        default_factory=list, description="Final features selected for training"
    )
    encoding_map: dict[str, str] = Field(
        default_factory=dict, description="Column name to encoding type applied"
    )
    scaling_method: str = Field(
        default="StandardScaler", description="Scaling method applied"
    )
    train_shape: tuple[int, int] = Field(
        default=(0, 0), description="(rows, cols) of training set"
    )
    test_shape: tuple[int, int] = Field(
        default=(0, 0), description="(rows, cols) of test set"
    )
    feature_pipeline_path: str | None = Field(
        default=None, description="Path to serialized feature pipeline (joblib)"
    )


class TrainedModel(BaseModel):
    """Model trainer artifact: trained ML model metadata."""

    model_type: str = Field(
        ..., description="Algorithm name (e.g. 'LightGBM', 'RandomForest')"
    )
    hyperparameters: dict[str, object] = Field(
        default_factory=dict, description="Hyperparameters used"
    )
    training_time_seconds: float = Field(
        default=0.0, ge=0.0, description="Wall-clock training time"
    )
    framework: Literal["flaml", "sklearn", "pytorch", "optuna"] = Field(
        default="flaml", description="Training framework used"
    )
    task: str = Field(..., description="ML task type")
    model_path: str = Field(..., description="Path to serialized model file")
    random_seed: int = Field(default=42, description="Random seed for reproducibility")
    validation_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Best cross-validation score"
    )


class EvaluationReport(BaseModel):
    """Model evaluator artifact: evaluation results with metrics and charts."""

    metrics: dict[str, float] = Field(
        default_factory=dict, description="Metric name to value"
    )
    primary_metric: str = Field(
        default="f1", description="Primary metric used for gate comparison"
    )
    primary_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Score on primary_metric (used by gate)"
    )
    confusion_matrix: list[list[int]] | None = Field(
        default=None, description="Confusion matrix (classification only)"
    )
    feature_importance: dict[str, float] = Field(
        default_factory=dict,
        description="Feature name to importance score (SHAP or built-in)",
    )
    chart_paths: list[str] = Field(
        default_factory=list, description="Paths to generated evaluation charts"
    )
    recommendation: Literal["deploy", "retrain", "recollect_data"] = Field(
        default="retrain", description="Agent recommendation based on metrics"
    )


class ModelPackage(BaseModel):
    """Model exporter artifact: final exportable model package."""

    model_path: str = Field(..., description="Path to primary serialized model file")
    format: Literal["joblib", "onnx", "torchscript"] = Field(
        default="joblib", description="Serialization format"
    )
    inference_code_path: str = Field(
        ..., description="Path to generated predict.py inference script"
    )
    model_card: str = Field(
        default="", description="Auto-generated Markdown model card"
    )
    dependencies: list[str] = Field(
        default_factory=list, description="Required packages for inference"
    )
    input_schema: dict[str, str] = Field(
        default_factory=dict, description="Feature name to dtype string"
    )
    output_schema: dict[str, str] = Field(
        default_factory=dict, description="Output field to dtype string"
    )


__all__ = [
    "DatasetProfile",
    "EDAReport",
    "EvaluationReport",
    "FeatureSet",
    "ModelPackage",
    "TrainedModel",
]
