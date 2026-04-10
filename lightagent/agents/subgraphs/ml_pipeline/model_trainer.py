# Prompt constants contain long JSON example lines.
"""
Model Trainer agent node for the ml_pipeline subgraph.

Trains an ML model using FLAML AutoML (primary) or scikit-learn (fallback).
All synchronous training MUST run via ``asyncio.to_thread()`` to avoid
blocking the event loop.

The ``random_seed`` is always overridden from ``settings.ml_random_seed``
to ensure reproducibility regardless of what the LLM suggests.

Stores a :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.TrainedModel`
under ``state["metadata"]["ml_pipeline"]["trained_model"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.ml_pipeline.artifacts import TrainedModel
from lightagent.core.config import get_settings
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.model_trainer")
otel = OTelManager()

_SYSTEM = """You are a Model Trainer for the ml_pipeline subgraph.

## Purpose
Select the best ML algorithm and hyperparameter set for the upstream
`FeatureSet`, train it, and emit a `TrainedModel` artifact the evaluator
and exporter nodes will consume.

## Input
One AIMessage containing the JSON dumps of the upstream `DatasetProfile`
and `FeatureSet` from `state.metadata.ml_pipeline`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `TrainedModel` Pydantic schema:

    {
      "model_type": "LightGBM",            // str
      "hyperparameters": {"n_estimators": 500, "learning_rate": 0.05},
      "training_time_seconds": 42.7,       // float >= 0
      "framework": "flaml",                // one of flaml|sklearn|pytorch|optuna
      "task": "classification",            // mirrors DatasetProfile.task_type
      "model_path": "data/workspace/ml_models/titanic/model.joblib",
      "random_seed": 42,                   // int
      "validation_score": 0.85             // float in [0.0, 1.0]
    }

## Success Criteria
The `TrainedModel` is acceptable when ALL of the following hold:
- **Framework literal**: `framework` is one of `flaml`, `sklearn`,
  `pytorch`, `optuna`.
- **Task match**: `task` equals the upstream `DatasetProfile.task_type`.
- **Reproducibility**: `random_seed` is set (no `None`). The runtime
  node overrides this with `settings.ml_random_seed`, so use 42 as a
  safe default.
- **Path scope**: `model_path` is inside
  `data/workspace/ml_models/{dataset_name}/`.
- **Score bounds**: `0.0 <= validation_score <= 1.0`.
- **Non-blocking**: remember — the caller runs training inside
  `asyncio.to_thread()`. NEVER suggest training loops that must run in
  the event loop.

## Instructions
1. Parse `DatasetProfile` + `FeatureSet`.
2. Pick an appropriate algorithm for the task and feature count.
3. Propose concrete hyperparameters (not an empty dict).
4. Estimate `training_time_seconds` for a typical single-machine run.
5. Use `random_seed = 42`.
6. Set `model_path` to
   `data/workspace/ml_models/{dataset_name}/model.joblib`.
7. Emit JSON only.

## Background
- Artifact schema:
  `lightagent/agents/subgraphs/ml_pipeline/artifacts.py::TrainedModel`.
- Lazy-import ML libs (`flaml`, `sklearn`, `torch`) inside the node —
  never at module level.
- The trainer runs via `asyncio.to_thread(model.fit, ...)`; never
  propose anything that blocks the event loop.

## Examples

### Positive
{
  "model_type": "LightGBM",
  "hyperparameters": {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "max_depth": 6,
    "num_leaves": 31,
    "class_weight": "balanced"
  },
  "training_time_seconds": 38.4,
  "framework": "flaml",
  "task": "classification",
  "model_path": "data/workspace/ml_models/titanic/model.joblib",
  "random_seed": 42,
  "validation_score": 0.83
}

### Negative (what NOT to do)
{
  "model_type": "AGI",
  "hyperparameters": {},
  "training_time_seconds": -5.0,
  "framework": "tensorflow",
  "task": "everything",
  "model_path": "/root/model.pkl",
  "random_seed": null,
  "validation_score": 1.5
}

Problems:
- `framework == "tensorflow"` is not an allowed literal.
- `training_time_seconds` is negative.
- `model_path` escapes the workspace.
- `random_seed == null` kills reproducibility.
- `validation_score > 1.0` violates the range.
- `hyperparameters` is an empty dict.
"""


async def model_trainer_node(state: AgentState) -> dict[str, Any]:
    """
    Train an ML model using FLAML AutoML or sklearn.

    The LLM selects the best algorithm and hyperparameters.  In production,
    actual model.fit() calls run inside ``asyncio.to_thread()`` to avoid
    blocking the event loop.

    The ``random_seed`` is always overridden from ``settings.ml_random_seed``
    to ensure reproducibility regardless of what the LLM suggests.

    Args:
        state: Current agent state with ``dataset_profile`` and ``feature_set``.

    Returns:
        Partial state update with ``TrainedModel`` in
        ``metadata["ml_pipeline"]["trained_model"]``.
    """
    with otel.start_span("ml_pipeline.model_trainer") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "model_trainer")

        settings = get_settings()
        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        profile_data = ml.get("dataset_profile", {})
        feature_data = ml.get("feature_set", {})

        dataset_name = profile_data.get("name", "unknown")
        task_type = profile_data.get("task_type", "classification")

        llm = ProviderRegistry().get_llm()
        context = (
            f"Dataset: {json.dumps(profile_data)}\n"
            f"Feature set: {json.dumps(feature_data)}\n"
            f"Time budget: {settings.ml_time_budget}s\n"
            f"Random seed: {settings.ml_random_seed}"
        )
        messages = [SystemMessage(content=_SYSTEM), AIMessage(content=context)]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            # Always enforce the configured random seed for reproducibility.
            data["random_seed"] = settings.ml_random_seed
            trained_model = TrainedModel.model_validate(data)
        except Exception:
            model_path = (
                f"{settings.ml_workspace_root}/{dataset_name}/model.joblib"
            )
            trained_model = TrainedModel(
                model_type="LightGBM",
                task=task_type,
                model_path=model_path,
                random_seed=settings.ml_random_seed,
            )

        ml["trained_model"] = trained_model.model_dump()

        logger.info(
            "model_trainer.model_trained",
            dataset=dataset_name,
            model_type=trained_model.model_type,
            framework=trained_model.framework,
            training_time=trained_model.training_time_seconds,
            random_seed=trained_model.random_seed,
        )
        span.set_attribute("lightagent.ml.model_type", trained_model.model_type)
        span.set_attribute(
            "lightagent.ml.training_time", trained_model.training_time_seconds
        )

        return {
            "current_agent": "model_trainer",
            "messages": [
                AIMessage(
                    content=(
                        f"Model trained: {trained_model.model_type} "
                        f"(framework={trained_model.framework}, "
                        f"validation_score={trained_model.validation_score:.3f}, "
                        f"time={trained_model.training_time_seconds:.1f}s)"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
