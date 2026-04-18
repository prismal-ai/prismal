"""Unit tests for ML pipeline agent nodes (mocked LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.state import create_initial_state


def _base_state(task: str = "Train a model") -> dict:
    """Create a base state for ML pipeline tests."""
    state = create_initial_state("sess-ml")
    state["messages"] = [HumanMessage(content=task)]
    state["metadata"] = {"ml_pipeline": {}}
    return state


@pytest.mark.asyncio
async def test_data_ingester_produces_dataset_profile() -> None:
    """data_ingester node populates ml_pipeline.dataset_profile in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.data_ingester import data_ingester_node

    mock_response = AIMessage(
        content=(
            '{"name": "iris_sample", "path": "tests/fixtures/datasets/iris_sample.csv", '
            '"rows": 6, "columns": 5, "column_types": {"sepal_length": "float64", '
            '"species": "object"}, "null_counts": {}, "task_type": "classification", '
            '"target_column": "species"}'
        )
    )
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        state = _base_state("Train a classifier on iris data")
        result = await data_ingester_node(state)

    assert result["current_agent"] == "data_ingester"
    ml = result["metadata"]["ml_pipeline"]
    assert "dataset_profile" in ml
    assert ml["dataset_profile"]["rows"] == 6


@pytest.mark.asyncio
async def test_data_ingester_handles_bad_llm_response() -> None:
    """data_ingester falls back gracefully on unparseable LLM response."""
    from lightagent.agents.subgraphs.ml_pipeline.data_ingester import data_ingester_node

    mock_response = AIMessage(content="I cannot parse this")
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await data_ingester_node(_base_state())

    assert result["current_agent"] == "data_ingester"
    assert "dataset_profile" in result["metadata"]["ml_pipeline"]


@pytest.mark.asyncio
async def test_eda_analyst_produces_eda_report() -> None:
    """eda_analyst node populates ml_pipeline.eda_report in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.eda_analyst import eda_analyst_node

    mock_response = AIMessage(
        content=(
            '{"correlations": {"tenure": -0.35, "monthly_charges": 0.19}, '
            '"outlier_columns": ["total_charges"], '
            '"missing_pattern": "MCAR", '
            '"class_balance": "imbalanced", '
            '"recommended_transforms": ["StandardScaler", "SMOTE"], '
            '"chart_paths": ["data/workspace/ml_models/test/eda/dist.png"]}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"]["dataset_profile"] = {
        "name": "churn",
        "path": "tests/fixtures/datasets/churn_sample.csv",
        "rows": 5,
        "columns": 5,
        "column_types": {"tenure": "int64"},
        "null_counts": {},
        "task_type": "classification",
        "target_column": "churned",
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await eda_analyst_node(state)

    assert result["current_agent"] == "eda_analyst"
    ml = result["metadata"]["ml_pipeline"]
    assert "eda_report" in ml
    assert ml["eda_report"]["class_balance"] == "imbalanced"


@pytest.mark.asyncio
async def test_feature_engineer_produces_feature_set() -> None:
    """feature_engineer node populates ml_pipeline.feature_set in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.feature_engineer import (
        feature_engineer_node,
    )

    mock_response = AIMessage(
        content=(
            '{"original_features": ["tenure", "monthly_charges", "contract_type"], '
            '"engineered_features": ["tenure", "monthly_charges", '
            '"contract_type_encoded"], '
            '"selected_features": ["tenure", "monthly_charges", '
            '"contract_type_encoded"], '
            '"encoding_map": {"contract_type": "one-hot"}, '
            '"scaling_method": "StandardScaler", '
            '"train_shape": [4, 3], '
            '"test_shape": [1, 3]}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "churn",
            "path": "tests/fixtures/datasets/churn_sample.csv",
            "rows": 5,
            "columns": 5,
            "column_types": {},
            "null_counts": {},
            "task_type": "classification",
            "target_column": "churned",
        },
        "eda_report": {
            "correlations": {},
            "outlier_columns": [],
            "missing_pattern": "MCAR",
            "class_balance": "imbalanced",
            "recommended_transforms": ["StandardScaler"],
            "chart_paths": [],
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await feature_engineer_node(state)

    assert result["current_agent"] == "feature_engineer"
    ml = result["metadata"]["ml_pipeline"]
    assert "feature_set" in ml
    assert list(ml["feature_set"]["train_shape"]) == [4, 3]


@pytest.mark.asyncio
async def test_model_trainer_produces_trained_model() -> None:
    """model_trainer node populates ml_pipeline.trained_model in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.model_trainer import model_trainer_node

    mock_response = AIMessage(
        content=(
            '{"model_type": "LightGBM", '
            '"hyperparameters": {"n_estimators": 100, "learning_rate": 0.05}, '
            '"training_time_seconds": 12.5, '
            '"framework": "flaml", '
            '"task": "classification", '
            '"model_path": "data/workspace/ml_models/churn/model.joblib", '
            '"random_seed": 42, '
            '"validation_score": 0.85}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "churn",
            "task_type": "classification",
            "target_column": "churned",
            "rows": 5,
            "columns": 5,
            "path": "",
            "column_types": {},
            "null_counts": {},
        },
        "feature_set": {
            "original_features": ["tenure"],
            "engineered_features": ["tenure"],
            "selected_features": ["tenure"],
            "encoding_map": {},
            "scaling_method": "StandardScaler",
            "train_shape": [4, 1],
            "test_shape": [1, 1],
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await model_trainer_node(state)

    assert result["current_agent"] == "model_trainer"
    ml = result["metadata"]["ml_pipeline"]
    assert "trained_model" in ml
    assert ml["trained_model"]["random_seed"] == 42
    assert ml["trained_model"]["model_type"] == "LightGBM"


@pytest.mark.asyncio
async def test_model_trainer_enforces_random_seed() -> None:
    """model_trainer always sets random_seed from config (overrides LLM value)."""
    from lightagent.agents.subgraphs.ml_pipeline.model_trainer import model_trainer_node

    mock_response = AIMessage(
        content=(
            '{"model_type": "RandomForest", "hyperparameters": {}, '
            '"training_time_seconds": 5.0, "framework": "sklearn", '
            '"task": "classification", '
            '"model_path": "data/workspace/ml_models/test/model.joblib", '
            '"random_seed": 999}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "iris",
            "task_type": "classification",
            "target_column": "species",
            "rows": 6,
            "columns": 5,
            "path": "",
            "column_types": {},
            "null_counts": {},
        },
        "feature_set": {
            "original_features": [],
            "engineered_features": [],
            "selected_features": [],
            "encoding_map": {},
            "scaling_method": "none",
            "train_shape": [4, 4],
            "test_shape": [2, 4],
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await model_trainer_node(state)

    # random_seed must always equal settings.ml_random_seed (42), not LLM's 999
    assert result["metadata"]["ml_pipeline"]["trained_model"]["random_seed"] == 42


@pytest.mark.asyncio
async def test_model_evaluator_produces_evaluation_report() -> None:
    """model_evaluator node populates ml_pipeline.evaluation_report in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.model_evaluator import (
        model_evaluator_node,
    )

    mock_response = AIMessage(
        content=(
            '{"metrics": {"f1": 0.85, "auc": 0.91, "accuracy": 0.87}, '
            '"primary_metric": "f1", '
            '"primary_score": 0.85, '
            '"confusion_matrix": [[80, 10], [5, 55]], '
            '"feature_importance": {"tenure": 0.35, "monthly_charges": 0.28}, '
            '"chart_paths": ["data/workspace/ml_models/churn/evaluation/roc.png"], '
            '"recommendation": "deploy"}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "churn",
            "task_type": "classification",
            "target_column": "churned",
            "rows": 5,
            "columns": 5,
            "path": "",
            "column_types": {},
            "null_counts": {},
        },
        "trained_model": {
            "model_type": "LightGBM",
            "hyperparameters": {},
            "training_time_seconds": 10.0,
            "framework": "flaml",
            "task": "classification",
            "model_path": "data/workspace/ml_models/churn/model.joblib",
            "random_seed": 42,
            "validation_score": 0.83,
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await model_evaluator_node(state)

    assert result["current_agent"] == "model_evaluator"
    ml = result["metadata"]["ml_pipeline"]
    assert "evaluation_report" in ml
    assert ml["evaluation_report"]["primary_score"] == 0.85
    assert ml["evaluation_report"]["recommendation"] == "deploy"


@pytest.mark.asyncio
async def test_model_evaluator_low_score_recommends_retrain() -> None:
    """model_evaluator sets recommendation=retrain when score is low."""
    from lightagent.agents.subgraphs.ml_pipeline.model_evaluator import (
        model_evaluator_node,
    )

    mock_response = AIMessage(
        content=(
            '{"metrics": {"f1": 0.50}, "primary_metric": "f1", '
            '"primary_score": 0.50, "feature_importance": {}, '
            '"chart_paths": [], "recommendation": "retrain"}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "test",
            "task_type": "classification",
            "target_column": "y",
            "rows": 5,
            "columns": 2,
            "path": "",
            "column_types": {},
            "null_counts": {},
        },
        "trained_model": {
            "model_type": "LogisticRegression",
            "hyperparameters": {},
            "training_time_seconds": 1.0,
            "framework": "sklearn",
            "task": "classification",
            "model_path": "data/workspace/ml_models/test/model.joblib",
            "random_seed": 42,
            "validation_score": 0.50,
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await model_evaluator_node(state)

    ml = result["metadata"]["ml_pipeline"]
    assert ml["evaluation_report"]["primary_score"] == 0.50
    assert ml["evaluation_report"]["recommendation"] == "retrain"


@pytest.mark.asyncio
async def test_model_exporter_produces_model_package() -> None:
    """model_exporter node populates ml_pipeline.model_package in metadata."""
    from lightagent.agents.subgraphs.ml_pipeline.model_exporter import (
        model_exporter_node,
    )

    mock_response = AIMessage(
        content=(
            '{"model_path": "data/workspace/ml_models/churn/model.joblib", '
            '"format": "joblib", '
            '"inference_code_path": "data/workspace/ml_models/churn/predict.py", '
            '"model_card": "# Churn Predictor\\nLightGBM model for churn.", '
            '"dependencies": ["scikit-learn>=1.5.0", "lightgbm>=4.5.0"], '
            '"input_schema": {"tenure": "float", "monthly_charges": "float"}, '
            '"output_schema": {"prediction": "int", "probability": "float"}}'
        )
    )
    state = _base_state()
    state["metadata"]["ml_pipeline"] = {
        "dataset_profile": {
            "name": "churn",
            "task_type": "classification",
            "target_column": "churned",
            "rows": 5,
            "columns": 5,
            "path": "",
            "column_types": {},
            "null_counts": {},
        },
        "trained_model": {
            "model_type": "LightGBM",
            "hyperparameters": {},
            "training_time_seconds": 10.0,
            "framework": "flaml",
            "task": "classification",
            "model_path": "data/workspace/ml_models/churn/model.joblib",
            "random_seed": 42,
            "validation_score": 0.85,
        },
        "evaluation_report": {
            "metrics": {"f1": 0.85},
            "primary_metric": "f1",
            "primary_score": 0.85,
            "feature_importance": {"tenure": 0.35},
            "chart_paths": [],
            "recommendation": "deploy",
        },
    }
    with patch("lightagent.providers.registry.ProviderRegistry.get_llm") as mock_llm:
        mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)
        result = await model_exporter_node(state)

    assert result["current_agent"] == "model_exporter"
    ml = result["metadata"]["ml_pipeline"]
    assert "model_package" in ml
    assert ml["model_package"]["format"] == "joblib"
    assert "predict.py" in ml["model_package"]["inference_code_path"]
