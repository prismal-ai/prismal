"""Integration test — complete ML pipeline subgraph (F5-03).

Mocks all LLM calls so no API key is required.  Verifies that the 6-agent
pipeline produces all typed Pydantic artifacts in ``state["metadata"]``
without raising exceptions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


@pytest.fixture
def base_state() -> dict[str, Any]:
    """Return minimal AgentState with a user request."""
    return {
        "messages": [
            HumanMessage(
                content=(
                    "Train a classification model on "
                    "tests/fixtures/datasets/iris_sample.csv, "
                    "target column: species"
                )
            )
        ],
        "session_id": "test-ml-integration",
        "current_agent": "",
        "next_agent": None,
        "metadata": {},
    }


def _ai(content: str) -> AsyncMock:
    """Return a coroutine mock that resolves to an AIMessage."""
    return AsyncMock(return_value=AIMessage(content=content))


@pytest.mark.asyncio
async def test_data_ingester_produces_profile(
    base_state: dict[str, Any],
) -> None:
    """data_ingester node stores DatasetProfile in metadata."""
    import json

    from lightagent.agents.subgraphs.ml_pipeline.data_ingester import (
        data_ingester_node,
    )

    profile_json = json.dumps(
        {
            "name": "iris_sample",
            "path": "tests/fixtures/datasets/iris_sample.csv",
            "rows": 150,
            "columns": 5,
            "column_types": {"sepal_length": "float64", "species": "object"},
            "null_counts": {},
            "task_type": "classification",
            "target_column": "species",
            "class_distribution": {
                "setosa": 50,
                "versicolor": 50,
                "virginica": 50,
            },
        }
    )
    with patch(
        "lightagent.agents.subgraphs.ml_pipeline.data_ingester.ProviderRegistry.get_llm",
        return_value=type("LLM", (), {"ainvoke": _ai(profile_json)})(),
    ):
        result = await data_ingester_node(base_state)

    ml = result["metadata"]["ml_pipeline"]
    assert ml["dataset_profile"]["name"] == "iris_sample"
    assert ml["dataset_profile"]["task_type"] == "classification"


@pytest.mark.asyncio
async def test_full_pipeline_produces_all_artifacts(
    base_state: dict[str, Any],
) -> None:
    """Full 6-agent pipeline produces all 6 artifacts in metadata."""
    import json

    from lightagent.agents.subgraphs.ml_pipeline.data_ingester import (
        data_ingester_node,
    )
    from lightagent.agents.subgraphs.ml_pipeline.eda_analyst import (
        eda_analyst_node,
    )
    from lightagent.agents.subgraphs.ml_pipeline.feature_engineer import (
        feature_engineer_node,
    )
    from lightagent.agents.subgraphs.ml_pipeline.model_evaluator import (
        model_evaluator_node,
    )
    from lightagent.agents.subgraphs.ml_pipeline.model_exporter import (
        model_exporter_node,
    )
    from lightagent.agents.subgraphs.ml_pipeline.model_trainer import (
        model_trainer_node,
    )

    profile_json = json.dumps(
        {
            "name": "iris",
            "path": "iris.csv",
            "rows": 150,
            "columns": 5,
            "column_types": {},
            "null_counts": {},
            "task_type": "classification",
            "target_column": "species",
        }
    )
    eda_json = json.dumps(
        {
            "correlations": {},
            "outlier_columns": [],
            "chart_paths": [],
            "missing_pattern": "none",
            "class_balance": "balanced",
            "recommended_transforms": [],
        }
    )
    feat_json = json.dumps(
        {
            "original_features": ["sepal_length"],
            "engineered_features": [],
            "selected_features": ["sepal_length"],
            "encoding_map": {},
            "scaling_method": "standard",
            "train_shape": [120, 4],
            "test_shape": [30, 4],
        }
    )
    train_json = json.dumps(
        {
            "model_type": "LightGBM",
            "framework": "flaml",
            "task": "classification",
            "model_path": "model.joblib",
            "random_seed": 42,
            "validation_score": 0.95,
            "training_time_seconds": 5.0,
            "hyperparameters": {},
        }
    )
    eval_json = json.dumps(
        {
            "metrics": {"accuracy": 0.95},
            "primary_metric": "accuracy",
            "primary_score": 0.95,
            "feature_importance": {},
            "chart_paths": [],
            "recommendation": "deploy",
        }
    )
    export_json = json.dumps(
        {
            "model_path": "model.joblib",
            "format": "joblib",
            "inference_code_path": "predict.py",
            "model_card": "Model card for iris",
            "dependencies": ["scikit-learn"],
            "input_schema": {},
            "output_schema": {},
        }
    )

    def make_llm(resp: str) -> object:
        """Return a minimal fake LLM that resolves to resp."""
        return type("LLM", (), {"ainvoke": _ai(resp)})()

    state = dict(base_state)
    nodes = [
        (data_ingester_node, "data_ingester", profile_json),
        (eda_analyst_node, "eda_analyst", eda_json),
        (feature_engineer_node, "feature_engineer", feat_json),
        (model_trainer_node, "model_trainer", train_json),
        (model_evaluator_node, "model_evaluator", eval_json),
        (model_exporter_node, "model_exporter", export_json),
    ]
    for node_fn, mod_name, resp in nodes:
        mod_path = f"lightagent.agents.subgraphs.ml_pipeline.{mod_name}.ProviderRegistry.get_llm"
        with patch(mod_path, return_value=make_llm(resp)):
            update = await node_fn(state)  # type: ignore[arg-type]
        state.update(update)
        state["messages"] = list(state.get("messages", [])) + list(update.get("messages", []))

    ml = state["metadata"]["ml_pipeline"]
    assert "dataset_profile" in ml
    assert "eda_report" in ml
    assert "feature_set" in ml
    assert "trained_model" in ml
    assert "evaluation_report" in ml
    assert "model_package" in ml
    assert ml["evaluation_report"]["primary_score"] == 0.95
