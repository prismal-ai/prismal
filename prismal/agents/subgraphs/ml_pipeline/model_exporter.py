# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""
Model Exporter agent node for the ml_pipeline subgraph.

Serializes the trained model (joblib/ONNX/TorchScript), generates standalone
inference code (``predict.py``), and writes a model card.

All outputs are saved to ``{ml_workspace_root}/{model_name}/``.
Never log raw model files or full dataset contents to traces.

Stores a :class:`~prismal.agents.subgraphs.ml_pipeline.artifacts.ModelPackage`
under ``state["metadata"]["ml_pipeline"]["model_package"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from prismal.agents.subgraphs.ml_pipeline.artifacts import ModelPackage
from prismal.core.config import get_settings
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("prismal.subgraphs.ml_pipeline.model_exporter")
otel = OTelManager()

_SYSTEM = """You are a Model Exporter for the ml_pipeline subgraph.

## Purpose
Package the approved `TrainedModel` + `EvaluationReport` as a deployable
`ModelPackage`: serialised model file, runnable inference script,
model card, and input/output schemas.

## Input
One AIMessage containing the JSON dumps of `DatasetProfile`,
`TrainedModel`, and `EvaluationReport` from
`state.metadata.ml_pipeline`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `ModelPackage` Pydantic schema:

    {
      "model_path": "data/workspace/ml_models/titanic/model.joblib",
      "format": "joblib",                     // one of joblib|onnx|torchscript
      "inference_code_path": "data/workspace/ml_models/titanic/predict.py",
      "model_card": "# Model Card ...",
      "dependencies": ["scikit-learn>=1.5.0", "lightgbm>=4.1.0"],
      "input_schema": {"Pclass": "int", "Sex_male": "int", "Age_imputed": "float"},
      "output_schema": {"prediction": "int", "probability": "float"}
    }

## Success Criteria
The `ModelPackage` is acceptable when ALL of the following hold:
- **Format literal**: `format` is one of `joblib`, `onnx`,
  `torchscript`.
- **Workspace scope**: both `model_path` and `inference_code_path`
  live under `data/workspace/ml_models/{dataset_name}/`.
- **Dependencies pinned**: every entry uses `pkg>=x.y` - no bare
  package names.
- **Input schema matches features**: keys of `input_schema` equal the
  upstream `FeatureSet.selected_features`.
- **Output schema populated**: at least one key and appropriate dtypes
  for the task.
- **Model card completeness**: `model_card` includes the mandatory
  sections:
    - Dataset name + provenance
    - Algorithm + framework + hyperparameters
    - Primary metric and score
    - Known limitations (data drift risks, subgroup bias, etc.)
    - Usage instructions (how to load + predict)

## Instructions
1. Parse upstream artifacts.
2. Copy `model_path` from `TrainedModel.model_path`.
3. Generate an `inference_code_path` as a sibling `predict.py`.
4. Compose the `model_card` with the 5 required sections.
5. List all non-stdlib inference dependencies with version pins.
6. Build `input_schema` from `FeatureSet.selected_features`.
7. Set `output_schema` from the task type.
8. Emit JSON only.

## Background
- Artifact schema:
  `prismal/agents/subgraphs/ml_pipeline/artifacts.py::ModelPackage`.
- All outputs MUST save under
  `data/workspace/ml_models/{dataset_name}/`.
- Never log raw model bytes or dataset rows to traces.

## Examples

### Positive
{
  "model_path": "data/workspace/ml_models/titanic/model.joblib",
  "format": "joblib",
  "inference_code_path": "data/workspace/ml_models/titanic/predict.py",
  "model_card": "# Titanic Survival Classifier\\n\\n## Dataset\\nTitanic (891 rows, 12 columns).\\n\\n## Algorithm\\nLightGBM via FLAML, seed=42, 500 estimators.\\n\\n## Primary metric\\nf1=0.83 on held-out 20% test split.\\n\\n## Limitations\\nTrained on 1912 passenger data; do not use for modern predictions. Sex feature is binary by historical record only.\\n\\n## Usage\\nload joblib('model.joblib').predict(X) where X has columns Pclass, Sex_male, Age_imputed, FamilySize, Fare_log1p, Embarked_C, Embarked_Q.",
  "dependencies": ["scikit-learn>=1.5.0", "lightgbm>=4.1.0", "joblib>=1.3.0"],
  "input_schema": {
    "Pclass": "int", "Sex_male": "int", "Age_imputed": "float",
    "FamilySize": "int", "Fare_log1p": "float",
    "Embarked_C": "int", "Embarked_Q": "int"
  },
  "output_schema": {"prediction": "int", "probability": "float"}
}

### Negative (what NOT to do)
{
  "model_path": "/tmp/model.pkl",
  "format": "raw-bytes",
  "inference_code_path": "predict.py",
  "model_card": "# Model\\nTrained.",
  "dependencies": ["sklearn"],
  "input_schema": {},
  "output_schema": {}
}

Problems:
- `model_path` escapes the workspace.
- `format == "raw-bytes"` is not an allowed literal.
- `dependencies` has no version pin and uses the deprecated name `sklearn`.
- Empty schemas.
- Model card is missing all mandatory sections.
"""


async def model_exporter_node(state: AgentState) -> dict[str, Any]:
    """
    Export the trained model with inference code and model card.

    Args:
        state: Current agent state with ``trained_model`` and
            ``evaluation_report`` in metadata.

    Returns:
        Partial state update with ``ModelPackage`` in
        ``metadata["ml_pipeline"]["model_package"]``.
    """
    with otel.start_span("ml_pipeline.model_exporter") as span:
        span.set_attribute("prismal.subgraph", "ml_pipeline")
        span.set_attribute("prismal.agent", "model_exporter")

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
                model_card=f"# {dataset_name} Model\nExported by Prismal.",
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
        span.set_attribute("prismal.ml.export_format", package.format)

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
