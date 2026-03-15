"""Model Exporter agent node for the ml_pipeline subgraph.

Serializes the trained model (joblib/ONNX/TorchScript), generates standalone
inference code (``predict.py``), and writes a model card.

All outputs are saved to ``{ml_workspace_root}/{model_name}/``.
Never log raw model files or full dataset contents to traces.

Stores a :class:`~lightagent.agents.subgraphs.ml_pipeline.artifacts.ModelPackage`
under ``state["metadata"]["ml_pipeline"]["model_package"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.ml_pipeline.artifacts import ModelPackage
from lightagent.core.config import get_settings
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.model_exporter")
otel = OTelManager()

_SYSTEM = (
    "You are a Model Exporter agent. Given a trained and evaluated model, "
    "export it with inference code and a model card.\n"
    "All files must be saved to data/workspace/ml_models/{model_name}/\n"
    "IMPORTANT: Never log raw model files or dataset contents to traces.\n"
    "Respond with ONLY a JSON object matching:\n"
    "{\n"
    '  "model_path": "data/workspace/ml_models/{name}/model.joblib",\n'
    '  "format": "joblib",\n'
    '  "inference_code_path": "data/workspace/ml_models/{name}/predict.py",\n'
    '  "model_card": "# Model Card\\n...",\n'
    '  "dependencies": ["scikit-learn>=1.5.0"],\n'
    '  "input_schema": {"feature1": "float"},\n'
    '  "output_schema": {"prediction": "int"}\n'
    "}\n"
    "format must be: joblib, onnx, or torchscript\n"
    "Always generate a meaningful model card with: dataset info, algorithm, "
    "metrics, limitations, and usage instructions."
)


async def model_exporter_node(state: AgentState) -> dict[str, Any]:
    """Export the trained model with inference code and model card.

    Args:
        state: Current agent state with ``trained_model`` and
            ``evaluation_report`` in metadata.

    Returns:
        Partial state update with ``ModelPackage`` in
        ``metadata["ml_pipeline"]["model_package"]``.
    """
    with otel.start_span("ml_pipeline.model_exporter") as span:
        span.set_attribute("lightagent.subgraph", "ml_pipeline")
        span.set_attribute("lightagent.agent", "model_exporter")

        settings = get_settings()
        ml: dict[str, Any] = dict(state.get("metadata", {}).get("ml_pipeline", {}))
        profile_data = ml.get("dataset_profile", {})
        model_data = ml.get("trained_model", {})
        eval_data = ml.get("evaluation_report", {})

        dataset_name = profile_data.get("name", "unknown")

        # Build safe context — exclude large/sensitive fields from traces
        safe_eval = {k: v for k, v in eval_data.items() if k != "confusion_matrix"}
        safe_model = {k: v for k, v in model_data.items() if k != "hyperparameters"}

        llm = ProviderRegistry().get_llm()
        context = (
            f"Dataset: {json.dumps(profile_data)}\n"
            f"Trained model: {json.dumps(safe_model)}\n"
            f"Evaluation: {json.dumps(safe_eval)}\n"
            f"Workspace root: {settings.ml_workspace_root}"
        )
        messages = [SystemMessage(content=_SYSTEM), AIMessage(content=context)]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            package = ModelPackage.model_validate(data)
        except Exception:
            base = f"{settings.ml_workspace_root}/{dataset_name}"
            package = ModelPackage(
                model_path=f"{base}/model.joblib",
                format="joblib",
                inference_code_path=f"{base}/predict.py",
                model_card=f"# {dataset_name} Model\nExported by LightAgent.",
                dependencies=[],
            )

        ml["model_package"] = package.model_dump()

        logger.info(
            "model_exporter.package_created",
            dataset=dataset_name,
            model_type=model_data.get("model_type", "unknown"),
            export_format=package.format,
            model_path=package.model_path,
        )
        span.set_attribute("lightagent.ml.export_format", package.format)

        return {
            "current_agent": "model_exporter",
            "messages": [
                AIMessage(
                    content=(
                        f"Model exported: {dataset_name} -> {package.model_path} "
                        f"(format={package.format})\n"
                        f"Inference code: {package.inference_code_path}\n"
                        f"Model card generated."
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "ml_pipeline": ml},
        }
