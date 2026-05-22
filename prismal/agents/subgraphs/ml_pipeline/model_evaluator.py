# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
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

from prismal.agents.subgraphs.ml_pipeline.artifacts import EvaluationReport
from prismal.core.config import get_settings
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.model_evaluator")
otel = OTelManager()

_SYSTEM = """You are a Model Evaluator for the ml_pipeline subgraph.

## Purpose
Evaluate the upstream `TrainedModel` on the held-out test set and emit
an `EvaluationReport` whose `primary_score` drives the quality gate
(retrain vs export).

## Input
One AIMessage containing the JSON dumps of `DatasetProfile`,
`FeatureSet`, and `TrainedModel` from `state.metadata.ml_pipeline`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `EvaluationReport` Pydantic schema:

    {
      "metrics": {"f1": 0.85, "auc": 0.91, "precision": 0.83, "recall": 0.87},
      "primary_metric": "f1",               // str — the metric used by the gate
      "primary_score": 0.85,                // float in [0.0, 1.0] — MUST equal metrics[primary_metric]
      "confusion_matrix": [[80, 10], [5, 55]],
      "feature_importance": {"feature1": 0.35, "feature2": 0.22},
      "chart_paths": [
        "data/workspace/ml_models/titanic/evaluation/roc.png"
      ],
      "recommendation": "deploy"            // one of deploy|retrain|recollect_data
    }

## Success Criteria
The `EvaluationReport` is acceptable when ALL of the following hold:
- **Score consistency**: `primary_score == metrics[primary_metric]`.
- **Metric spread**: `metrics` contains >= 3 entries (e.g. f1, auc,
  precision, recall) for classification; >= 2 (mae, rmse) for
  regression.
- **Recommendation literal**: one of `deploy`, `retrain`,
  `recollect_data`.
- **Recommendation rule**:
    - `primary_score >= settings.ml_quality_threshold` → `deploy`
    - `0.5 <= primary_score < threshold` → `retrain`
    - `primary_score < 0.5` → `recollect_data`
- **Confusion matrix**: square and non-negative; total equals
  `DatasetProfile.rows * test_fraction` (allowing rounding).
- **Chart paths valid**: under
  `data/workspace/ml_models/{name}/evaluation/`.
- **No raw data in logs**: never include rows of the dataset or bytes
  of the model file in the output.

## Instructions
1. Parse upstream artifacts.
2. Compute (or estimate) metrics on the held-out set.
3. Pick `primary_metric` consistent with the upstream task type
   (`f1` default for classification, `rmse` for regression).
4. Set `primary_score = metrics[primary_metric]`.
5. Apply the recommendation rule above.
6. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/ml_pipeline/artifacts.py::EvaluationReport`.
- The quality gate downstream uses `primary_score` vs
  `settings.ml_quality_threshold` (default `0.7`) — keep them aligned.

## Examples

### Positive (deploy)
{
  "metrics": {"f1": 0.83, "auc": 0.89, "precision": 0.81, "recall": 0.85},
  "primary_metric": "f1",
  "primary_score": 0.83,
  "confusion_matrix": [[95, 15], [12, 57]],
  "feature_importance": {"Sex_male": 0.32, "Pclass": 0.21, "Fare_log1p": 0.18, "Age_imputed": 0.12},
  "chart_paths": [
    "data/workspace/ml_models/titanic/evaluation/roc.png",
    "data/workspace/ml_models/titanic/evaluation/confusion_matrix.png"
  ],
  "recommendation": "deploy"
}

### Negative (what NOT to do)
{
  "metrics": {"f1": 0.60},
  "primary_metric": "accuracy",
  "primary_score": 0.99,
  "confusion_matrix": [[1]],
  "feature_importance": {},
  "chart_paths": [],
  "recommendation": "ship it"
}

Problems:
- `primary_metric == "accuracy"` but no `accuracy` key in `metrics`.
- `primary_score` (0.99) inconsistent with `metrics["f1"] = 0.60`.
- `confusion_matrix` is 1x1 — impossible for binary classification.
- `recommendation == "ship it"` is not an allowed literal.
- Empty `feature_importance` and `chart_paths`.
"""


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
        safe_model_meta = {k: v for k, v in model_data.items() if k != "hyperparameters"}

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
            "PASSED" if report.primary_score >= settings.ml_quality_threshold else "FAILED"
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
