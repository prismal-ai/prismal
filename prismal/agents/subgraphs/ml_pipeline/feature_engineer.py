# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""
Feature Engineer agent node for the ml_pipeline subgraph.

Applies feature transformations (encoding, scaling, feature selection, SMOTE for
class imbalance) and produces a train/test split plan.

Stores a :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.FeatureSet`
under ``state["metadata"]["ml_pipeline"]["feature_set"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from prismal.agents.subgraphs.ml_pipeline.artifacts import FeatureSet
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.feature_engineer")
otel = OTelManager()

_SYSTEM = """You are a Feature Engineer for the ml_pipeline subgraph.

## Purpose
Convert a `DatasetProfile` + `EDAReport` into a concrete `FeatureSet`
plan with encodings, scaling, train/test shapes, and the final list of
features to feed the model trainer.

## Input
One AIMessage carrying the JSON dumps of the upstream `DatasetProfile`
and `EDAReport` from `state.metadata.ml_pipeline`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `FeatureSet` Pydantic schema:

    {
      "original_features": ["col1", "col2"],
      "engineered_features": ["col1", "col2_one_hot_a", "col2_one_hot_b"],
      "selected_features": ["col1", "col2_one_hot_a"],
      "encoding_map": {"col2": "one-hot"},          // one of one-hot|label|ordinal|target|none
      "scaling_method": "StandardScaler",            // one of StandardScaler|MinMaxScaler|RobustScaler|none
      "train_shape": [800, 10],                      // [rows, cols]
      "test_shape": [200, 10],
      "feature_pipeline_path": "data/workspace/ml_models/titanic/features/pipeline.joblib"
    }

## Success Criteria
The `FeatureSet` is acceptable when ALL of the following hold:
- **Cardinality sanity**: `train_shape[0] + test_shape[0] == rows`
  from the upstream `DatasetProfile` (allowing ±1 for odd splits).
- **Shape-column consistency**: `train_shape[1] ==
  len(selected_features) == test_shape[1]`.
- **Encoding literals**: every value in `encoding_map` is one of
  `one-hot`, `label`, `ordinal`, `target`, `none`.
- **Scaling literal**: `scaling_method` is one of `StandardScaler`,
  `MinMaxScaler`, `RobustScaler`, `none`.
- **Derived from EDA**: encodings and scaling match the
  `recommended_transforms` from the upstream EDAReport.
- **Pipeline path**: `feature_pipeline_path` lives under
  `data/workspace/ml_models/{name}/features/`.

## Instructions
1. Parse upstream `DatasetProfile` + `EDAReport` JSON.
2. Copy `original_features` from `DatasetProfile.column_types` keys.
3. Apply recommended transforms from EDA to produce `engineered_features`.
4. Pick `selected_features` (drop highly-correlated or low-variance
   engineered columns).
5. Populate `encoding_map` per categorical column.
6. Pick the scaling method matching the EDA recommendation.
7. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/ml_pipeline/artifacts.py::FeatureSet`.
- Pipelines are serialised via joblib; path must stay inside
  `data/workspace/ml_models/{name}/features/`.

## Examples

### Positive
{
  "original_features": [
    "Pclass","Sex","Age","SibSp","Parch","Fare","Embarked","Cabin"
  ],
  "engineered_features": [
    "Pclass","Sex_male","Age_imputed","FamilySize","Fare_log1p","Embarked_C","Embarked_Q","Embarked_S"
  ],
  "selected_features": [
    "Pclass","Sex_male","Age_imputed","FamilySize","Fare_log1p","Embarked_C","Embarked_Q"
  ],
  "encoding_map": {"Sex": "one-hot", "Embarked": "one-hot"},
  "scaling_method": "StandardScaler",
  "train_shape": [712, 7],
  "test_shape": [179, 7],
  "feature_pipeline_path": "data/workspace/ml_models/titanic/features/pipeline.joblib"
}

### Negative (what NOT to do)
{
  "original_features": ["a","b","c"],
  "engineered_features": ["a","b","c","d"],
  "selected_features": ["a","b"],
  "encoding_map": {"b": "magic"},
  "scaling_method": "fancy",
  "train_shape": [100, 10],
  "test_shape": [50, 5],
  "feature_pipeline_path": "/tmp/pipeline.joblib"
}

Problems:
- `train_shape[1] == 10` but `selected_features` has 2 entries.
- `test_shape[1] != train_shape[1]` (5 vs 10).
- `encoding_map` value "magic" not an allowed literal.
- `scaling_method` "fancy" not an allowed literal.
- `feature_pipeline_path` escapes the workspace.
"""


async def feature_engineer_node(state: AgentState) -> dict[str, Any]:
    """
    Design and plan feature transformations based on dataset profile and EDA.

    Args:
        state: Current agent state with ``dataset_profile`` and ``eda_report``.

    Returns:
        Partial state update with ``FeatureSet`` in
        ``metadata["ml_pipeline"]["feature_set"]``.
    """
    with otel.start_span("ml_pipeline.feature_engineer") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "feature_engineer")

        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        profile_data = ml.get("dataset_profile", {})
        eda_data = ml.get("eda_report", {})

        llm = ProviderRegistry().get_llm()
        context = f"Dataset profile: {json.dumps(profile_data)}\nEDA report: {json.dumps(eda_data)}"
        messages = [SystemMessage(content=_SYSTEM), AIMessage(content=context)]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            feature_set = FeatureSet.model_validate(data)
        except Exception:
            cols = list(profile_data.get("column_types", {}).keys())
            n = len(cols)
            total_rows = int(profile_data.get("rows", 0))
            feature_set = FeatureSet(
                original_features=cols,
                engineered_features=cols,
                selected_features=cols,
                train_shape=(int(total_rows * 0.8), n),
                test_shape=(int(total_rows * 0.2), n),
            )

        ml["feature_set"] = feature_set.model_dump()

        n_features = len(feature_set.selected_features)
        logger.info(
            "feature_engineer.feature_set_created",
            selected_count=n_features,
            train_rows=feature_set.train_shape[0],
            scaling=feature_set.scaling_method,
        )
        span.set_attribute("lightagent.ml.feature_count", n_features)

        return {
            "current_agent": "feature_engineer",
            "messages": [
                AIMessage(
                    content=(
                        f"Feature engineering complete: "
                        f"{n_features} features selected, "
                        f"train={feature_set.train_shape}, "
                        f"test={feature_set.test_shape}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
