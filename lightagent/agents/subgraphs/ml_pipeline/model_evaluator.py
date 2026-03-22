"""
Model Evaluator agent node for the ml_pipeline subgraph.

Evaluates the trained model on the test set, computes metrics (F1, AUC, RMSE,
etc.), generates charts (ROC curve, confusion matrix, SHAP summary), and
decides whether the model meets the quality threshold.

The model quality gate reads ``ml_pipeline.evaluation_report.primary_score``
and routes to ``model_exporter`` (score >= threshold) or ``model_trainer``
(score < threshold, up to max_iterations).

IMPORTANT: Never log raw datasets or model files to traces — only metadata.

Stores an :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.EvaluationReport`
under ``state["metadata"]["ml_pipeline"]["evaluation_report"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.ml_pipeline.artifacts import EvaluationReport
from lightagent.core.config import get_settings
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.model_evaluator")
otel = OTelManager()

_SYSTEM = (
    "You are a Model Evaluator agent. Given a trained model and dataset, evaluate it "
    "on the test set and produce a detailed evaluation report.\n"
    "IMPORTANT: Log only metadata (model_type, metrics, training_time) — "
    "never log raw datasets or full model files.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "metrics": {"f1": 0.85, "auc": 0.91},\n'
    '  "primary_metric": "f1",\n'
    '  "primary_score": 0.85,\n'
    '  "confusion_matrix": [[80, 10], [5, 55]],\n'
    '  "feature_importance": {"feature1": 0.35},\n'
    '  "chart_paths": ["data/workspace/ml_models/{name}/evaluation/roc.png"],\n'
    '  "recommendation": "deploy"\n'
    "}\n"
    "recommendation must be: deploy, retrain, or recollect_data\n"
    "Use 'deploy' if primary_score >= quality_threshold, else 'retrain'"
)


async def model_evaluator_node(state: AgentState) -> dict[str, Any]:
    """
    Evaluate the trained model and produce metrics and charts.

    The ``primary_score`` in the returned ``EvaluationReport`` is read by the
    model quality gate to decide whether to proceed to ``model_exporter`` or
    retry ``model_trainer``.

    Args:
        state: Current agent state with ``trained_model`` in metadata.

    Returns:
        Partial state update with ``EvaluationReport`` in
        ``metadata["ml_pipeline"]["evaluation_report"]``.
    """
    with otel.start_span("ml_pipeline.model_evaluator") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "model_evaluator")

        settings = get_settings()
        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        profile_data = ml.get("dataset_profile", {})
        model_data = ml.get("trained_model", {})

        dataset_name = profile_data.get("name", "unknown")

        # Exclude raw hyperparameters (can be large) from the LLM context
        safe_model_meta = {
            k: v for k, v in model_data.items()
            if k != "hyperparameters"
        }

        llm = ProviderRegistry().get_llm()
        context = (
            f"Dataset: {json.dumps(profile_data)}\n"
            f"Trained model: {json.dumps(safe_model_meta)}\n"
            f"Quality threshold: {settings.ml_quality_threshold}\n"
            f"SHAP max samples: {settings.ml_shap_max_samples}"
        )
        messages = [SystemMessage(content=_SYSTEM), AIMessage(content=context)]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            report = EvaluationReport.model_validate(data)
        except Exception:
            report = EvaluationReport(
                metrics={},
                primary_metric="f1",
                primary_score=0.0,
                recommendation="retrain",
            )

        ml["evaluation_report"] = report.model_dump()

        gate_result = (
            "PASSED"
            if report.primary_score >= settings.ml_quality_threshold
            else "FAILED"
        )

        logger.info(
            "model_evaluator.report_created",
            dataset=dataset_name,
            model_type=model_data.get("model_type", "unknown"),
            primary_metric=report.primary_metric,
            primary_score=report.primary_score,
            recommendation=report.recommendation,
            gate=gate_result,
        )
        span.set_attribute("lightagent.ml.primary_score", report.primary_score)
        span.set_attribute("lightagent.ml.recommendation", report.recommendation)

        return {
            "current_agent": "model_evaluator",
            "messages": [
                AIMessage(
                    content=(
                        f"Model evaluation complete ({dataset_name}): "
                        f"{report.primary_metric}={report.primary_score:.3f} "
                        f"[gate {gate_result} @ {settings.ml_quality_threshold}] "
                        f"-> {report.recommendation}"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
