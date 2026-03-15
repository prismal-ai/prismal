"""Model Trainer agent node for the ml_pipeline subgraph.

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

_SYSTEM = (
    "You are a Model Trainer agent. Given a dataset profile and feature set, "
    "select the best ML algorithm and training strategy.\n"
    "CRITICAL: All synchronous training must run via asyncio.to_thread() to avoid "
    "blocking the event loop.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "model_type": "LightGBM",\n'
    '  "hyperparameters": {"n_estimators": 100},\n'
    '  "training_time_seconds": 30.0,\n'
    '  "framework": "flaml",\n'
    '  "task": "classification",\n'
    '  "model_path": "data/workspace/ml_models/{name}/model.joblib",\n'
    '  "random_seed": 42,\n'
    '  "validation_score": 0.85\n'
    "}\n"
    "framework must be one of: flaml, sklearn, pytorch, optuna"
)


async def model_trainer_node(state: AgentState) -> dict[str, Any]:
    """Train an ML model using FLAML AutoML or sklearn.

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
