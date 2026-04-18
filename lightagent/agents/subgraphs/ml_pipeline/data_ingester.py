# Prompt constants contain long JSON example lines.
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

_SYSTEM = """You are a Data Ingester for the ml_pipeline subgraph.

## Purpose
Load the user-specified dataset, profile its schema and quality, and
emit a `DatasetProfile` that downstream EDA, feature-engineering, and
training nodes can use without re-reading the raw file.

## Input
The last 5 messages of `state.messages`. The user request names or
describes the dataset (file path, URL, or a table already loaded by a
prior tool call).

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `DatasetProfile` Pydantic schema:

    {
      "name": "dataset_name",             // str
      "path": "data/workspace/ml/file.csv",// str, workspace-relative or absolute
      "rows": 1000,                       // int >= 0
      "columns": 10,                      // int >= 0
      "column_types": {                   // dict[str, str]
        "col1": "float64",
        "col2": "object"
      },
      "null_counts": {"col1": 0},         // dict[str, int]
      "target_column": "label",           // str | null
      "task_type": "classification",      // one of classification|regression|clustering|time_series
      "class_distribution": {"0": 800, "1": 200}  // dict[str,int] | null (classification only)
    }

## Success Criteria
The `DatasetProfile` is acceptable when ALL of the following hold:
- **Schema accuracy**: `columns == len(column_types)` and every key in
  `null_counts` exists in `column_types`.
- **Task type**: `task_type` is one of the 4 allowed literals
  (classification / regression / clustering / time_series).
- **Target coherence**: for classification/regression, `target_column`
  is set and present in `column_types`; for clustering/time_series,
  `target_column` MAY be null.
- **Class distribution**: populated ONLY when `task_type ==
  "classification"`; sum of values equals `rows` (allowing rounding).
- **Path safety**: `path` points inside `data/workspace/` unless the
  user explicitly provided an absolute path.

## Instructions
1. Read the dataset described in the last user message.
2. Infer `task_type` from user wording or target column characteristics.
3. Populate `column_types` with dtype strings (`float64`, `int64`,
   `object`, `datetime64`, `category`).
4. Populate `null_counts` for columns that have any nulls.
5. For classification, compute `class_distribution` from the target.
6. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/ml_pipeline/artifacts.py::DatasetProfile`.
- ML libs (pandas/polars/flaml) must be lazy-imported — never at module
  top level.
- Workspace root for ML artifacts:
  `data/workspace/ml_models/{model_name}/`.

## Examples

### Positive
User: "Analiza data/workspace/ml/titanic.csv para predecir 'survived'."

{
  "name": "titanic",
  "path": "data/workspace/ml/titanic.csv",
  "rows": 891,
  "columns": 12,
  "column_types": {
    "PassengerId": "int64", "Survived": "int64", "Pclass": "int64",
    "Name": "object", "Sex": "object", "Age": "float64",
    "SibSp": "int64", "Parch": "int64", "Ticket": "object",
    "Fare": "float64", "Cabin": "object", "Embarked": "object"
  },
  "null_counts": {"Age": 177, "Cabin": 687, "Embarked": 2},
  "target_column": "Survived",
  "task_type": "classification",
  "class_distribution": {"0": 549, "1": 342}
}

### Negative (what NOT to do)
{
  "name": "data",
  "path": "/etc/passwd",
  "rows": 891,
  "columns": 3,
  "column_types": {"a": "float", "b": "int"},
  "null_counts": {"ghost": 5},
  "target_column": "maybe",
  "task_type": "magic",
  "class_distribution": null
}

Problems:
- `path` is a system file outside the workspace.
- `columns == 3` but `column_types` has only 2 entries.
- `null_counts` references a column not in `column_types`.
- `task_type == "magic"` is not an allowed literal.
- `target_column == "maybe"` is not in `column_types`.
"""


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
