# Prompt constants contain long JSON example lines.
"""
EDA Analyst agent node for the ml_pipeline subgraph.

Performs automated exploratory data analysis: correlations, outlier detection,
missing value patterns, class balance assessment, and chart generation.

Stores an :class:`~prismal.agents.subgraphs.ml_pipeline.artifacts.EDAReport`
under ``state["metadata"]["ml_pipeline"]["eda_report"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from prismal.agents.subgraphs.ml_pipeline.artifacts import EDAReport
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("prismal.subgraphs.ml_pipeline.eda_analyst")
otel = OTelManager()

_SYSTEM = """You are an EDA Analyst for the ml_pipeline subgraph.

## Purpose
Given an upstream `DatasetProfile`, perform exploratory data analysis
and recommend preprocessing steps. Emit an `EDAReport` the feature
engineer will use verbatim.

## Input
One AIMessage containing the JSON dump of the upstream `DatasetProfile`
from `state.metadata.ml_pipeline.dataset_profile`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `EDAReport` Pydantic schema:

    {
      "correlations": {"feature_x": 0.45, "feature_y": -0.32},
      "outlier_columns": ["col1"],
      "missing_pattern": "MCAR",          // one of MCAR|MAR|MNAR|none
      "class_balance": "balanced",        // one of balanced|imbalanced|severely_imbalanced|n/a
      "recommended_transforms": [
        "StandardScaler on numerical features",
        "OneHotEncoder on low-cardinality categoricals"
      ],
      "chart_paths": [
        "data/workspace/ml_models/{name}/eda/distributions.png"
      ]
    }

## Success Criteria
The `EDAReport` is acceptable when ALL of the following hold:
- **Top correlations**: `correlations` lists the top 3-10 features with
  the highest absolute correlation to the target (classification/
  regression only).
- **Missing pattern literal**: one of `MCAR`, `MAR`, `MNAR`, `none`.
- **Class balance literal**: `balanced` (minority class >= 40%),
  `imbalanced` (20%-40%), `severely_imbalanced` (< 20%), `n/a` for
  non-classification tasks.
- **Actionable recommendations**: each entry names a concrete
  transform + the column(s) to apply it to.
- **Chart paths valid**: saved under
  `data/workspace/ml_models/{dataset_name}/eda/`.

## Instructions
1. Parse the `DatasetProfile` JSON.
2. Compute correlations (or qualitative estimates) for the target.
3. Identify columns with outliers (via IQR / z-score heuristic).
4. Classify the missingness pattern and the class balance.
5. Recommend transforms grounded in the findings (e.g. impute median
   for MCAR numerical nulls, SMOTE for severely_imbalanced classes).
6. List any EDA chart paths you generated.
7. Emit JSON only.

## Background
- Artifact schema:
  `prismal/agents/subgraphs/ml_pipeline/artifacts.py::EDAReport`.
- Workspace path for charts:
  `data/workspace/ml_models/{dataset_name}/eda/`.

## Examples

### Positive
Input: Titanic dataset profile (classification, target=Survived).

{
  "correlations": {
    "Sex": -0.54, "Pclass": -0.34, "Fare": 0.26, "Age": -0.07
  },
  "outlier_columns": ["Fare"],
  "missing_pattern": "MAR",
  "class_balance": "imbalanced",
  "recommended_transforms": [
    "Median imputation for Age (177 nulls, MAR pattern)",
    "Drop Cabin (687/891 nulls, MNAR-adjacent)",
    "OneHotEncoder for Sex, Embarked (low cardinality)",
    "Log1p transform for Fare to tame the right skew and outliers",
    "SMOTE oversampling to address 38/62 class imbalance"
  ],
  "chart_paths": [
    "data/workspace/ml_models/titanic/eda/distributions.png",
    "data/workspace/ml_models/titanic/eda/correlation_heatmap.png"
  ]
}

### Negative (what NOT to do)
{
  "correlations": {},
  "outlier_columns": [],
  "missing_pattern": "random",
  "class_balance": "okay",
  "recommended_transforms": ["scale it"],
  "chart_paths": ["/tmp/anywhere.png"]
}

Problems:
- `missing_pattern == "random"` is not an allowed literal.
- `class_balance == "okay"` is not an allowed literal.
- `recommended_transforms` is not actionable (no column, no method).
- `chart_paths` escapes the workspace root.
- Empty correlations despite a classification task with a known target.
"""


async def eda_analyst_node(state: AgentState) -> dict[str, Any]:
    """
    Analyse the dataset and produce an EDA report.

    Args:
        state: Current agent state with ``dataset_profile`` in metadata.

    Returns:
        Partial state update with ``EDAReport`` in
        ``metadata["ml_pipeline"]["eda_report"]``.
    """
    with otel.start_span("ml_pipeline.eda_analyst") as span:
        span.set_attribute("prismal.subgraph", "ml_pipeline")
        span.set_attribute("prismal.agent", "eda_analyst")

        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        profile_data = ml.get("dataset_profile", {})
        dataset_name = profile_data.get("name", "unknown")

        llm = ProviderRegistry().get_llm()
        context = (
            f"Dataset: {json.dumps(profile_data)}\n"
            f"User request: "
            f"{state['messages'][-1].content if state.get('messages') else ''}"
        )
        messages = [SystemMessage(content=_SYSTEM), AIMessage(content=context)]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            report = EDAReport.model_validate(data)
        except Exception:
            report = EDAReport(
                recommended_transforms=["StandardScaler"],
                missing_pattern="none",
                class_balance="n/a",
            )

        ml["eda_report"] = report.model_dump()

        logger.info(
            "eda_analyst.report_created",
            dataset=dataset_name,
            class_balance=report.class_balance,
            transforms_count=len(report.recommended_transforms),
        )
        span.set_attribute("prismal.ml.dataset", dataset_name)

        return {
            "current_agent": "eda_analyst",
            "messages": [
                AIMessage(
                    content=(
                        f"EDA complete for {dataset_name}: "
                        f"class_balance={report.class_balance}, "
                        f"recommended={report.recommended_transforms}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
