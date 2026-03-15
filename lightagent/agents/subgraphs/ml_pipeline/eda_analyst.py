"""EDA Analyst agent node for the ml_pipeline subgraph.

Performs automated exploratory data analysis: correlations, outlier detection,
missing value patterns, class balance assessment, and chart generation.

Stores an :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.EDAReport`
under ``state["metadata"]["ml_pipeline"]["eda_report"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.ml_pipeline.artifacts import EDAReport
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.eda_analyst")
otel = OTelManager()

_SYSTEM = (
    "You are an EDA Analyst agent. Given a dataset profile, perform exploratory "
    "data analysis and recommend preprocessing steps.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "correlations": {"feature": 0.45},\n'
    '  "outlier_columns": ["col1"],\n'
    '  "missing_pattern": "MCAR",\n'
    '  "class_balance": "balanced",\n'
    '  "recommended_transforms": ["StandardScaler"],\n'
    '  "chart_paths": ["data/workspace/ml_models/{name}/eda/distributions.png"]\n'
    "}\n"
    "missing_pattern must be: MCAR, MAR, MNAR, or none\n"
    "class_balance must be: balanced, imbalanced, severely_imbalanced, or n/a"
)


async def eda_analyst_node(state: AgentState) -> dict[str, Any]:
    """Analyse the dataset and produce an EDA report.

    Args:
        state: Current agent state with ``dataset_profile`` in metadata.

    Returns:
        Partial state update with ``EDAReport`` in
        ``metadata["ml_pipeline"]["eda_report"]``.
    """
    with otel.start_span("ml_pipeline.eda_analyst") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "eda_analyst")

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
        span.set_attribute("lightagent.ml.dataset", dataset_name)

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
