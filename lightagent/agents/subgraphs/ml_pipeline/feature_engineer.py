"""Feature Engineer agent node for the ml_pipeline subgraph.

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

from lightagent.agents.subgraphs.ml_pipeline.artifacts import FeatureSet
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.feature_engineer")
otel = OTelManager()

_SYSTEM = (
    "You are a Feature Engineer agent. Given a dataset profile and EDA report, "
    "design the feature engineering pipeline and produce a FeatureSet plan.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "original_features": ["col1", "col2"],\n'
    '  "engineered_features": ["col1", "col2_encoded"],\n'
    '  "selected_features": ["col1", "col2_encoded"],\n'
    '  "encoding_map": {"col2": "one-hot"},\n'
    '  "scaling_method": "StandardScaler",\n'
    '  "train_shape": [800, 10],\n'
    '  "test_shape": [200, 10]\n'
    "}\n"
    "encoding_map values from: one-hot, label, ordinal, target, none\n"
    "scaling_method from: StandardScaler, MinMaxScaler, RobustScaler, none"
)


async def feature_engineer_node(state: AgentState) -> dict[str, Any]:
    """Design and plan feature transformations based on dataset profile and EDA.

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
        context = (
            f"Dataset profile: {json.dumps(profile_data)}\n"
            f"EDA report: {json.dumps(eda_data)}"
        )
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
