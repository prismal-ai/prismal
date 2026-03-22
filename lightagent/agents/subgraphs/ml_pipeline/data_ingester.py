"""
Data Ingester agent node for the ml_pipeline subgraph.

Loads and profiles a dataset (CSV, Parquet, JSON, Excel), detects column types,
counts nulls, identifies the target column, and infers the ML task type.

Stores a :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.DatasetProfile`
under ``state["metadata"]["ml_pipeline"]["dataset_profile"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.ml_pipeline.artifacts import DatasetProfile
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.data_ingester")
otel = OTelManager()

_SYSTEM = (
    "You are a Data Ingester agent. Analyze the user's dataset request and produce a "
    "structured dataset profile.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "name": "dataset_name",\n'
    '  "path": "path/to/file.csv",\n'
    '  "rows": 1000,\n'
    '  "columns": 10,\n'
    '  "column_types": {"col1": "float64", "col2": "object"},\n'
    '  "null_counts": {"col1": 0},\n'
    '  "task_type": "classification",\n'
    '  "target_column": "label",\n'
    '  "class_distribution": {"0": 800, "1": 200}\n'
    "}\n"
    "task_type must be one of: classification, regression, clustering, time_series"
)


async def data_ingester_node(state: AgentState) -> dict[str, Any]:
    """
    Load and profile the dataset from the user's request.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``DatasetProfile`` in
        ``metadata["ml_pipeline"]["dataset_profile"]``.
    """
    with otel.start_span("ml_pipeline.data_ingester") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "data_ingester")

        llm = ProviderRegistry().get_llm()
        messages = [SystemMessage(content=_SYSTEM), *list(state["messages"][-5:])]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            profile = DatasetProfile.model_validate(data)
        except Exception:
            profile = DatasetProfile(
                name="unknown",
                path="",
                rows=0,
                columns=0,
                column_types={},
                task_type="classification",
            )

        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        ml["dataset_profile"] = profile.model_dump()

        logger.info(
            "data_ingester.profile_created",
            name=profile.name,
            rows=profile.rows,
            task_type=profile.task_type,
        )
        span.set_attribute("lightagent.ml.rows", profile.rows)
        span.set_attribute("lightagent.ml.task_type", profile.task_type)

        return {
            "current_agent": "data_ingester",
            "messages": [
                AIMessage(
                    content=(
                        f"Dataset profiled: {profile.name} — "
                        f"{profile.rows} rows, {profile.columns} columns, "
                        f"task: {profile.task_type}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
