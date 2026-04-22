"""
ML Pipeline Subgraph — Pipeline completo de Machine Learning
=============================================================
Subgraph: lightagent.agents.subgraphs.ml_pipeline

Dataset: UCI Heart Disease / Iris / Breast Cancer (scikit-learn datasets)
  • Heart Disease: 303 registros, 14 características, predicción binaria.
  • Referencia: https://archive.ics.uci.edu/ml/datasets/heart+disease
  • Por qué: El ML Pipeline tiene 6 nodos especializados (ingestión, EDA,
    feature engineering, entrenamiento, evaluación, exportación) con una
    quality gate que requiere score >= 0.7. Los datasets de UCI son
    benchmarks clásicos para demostrar pipelines end-to-end.

Descripción del subgraph ML Pipeline:
  data_ingester → eda_analyst → feature_engineer → model_trainer
  → model_evaluator → [quality gate: score>=0.7?] → model_exporter
  - Si score < 0.7 → re-entrenar (máximo 3 iteraciones)

Nodos:
  1. data_ingester     — carga y valida datos CSV/Parquet
  2. eda_analyst       — análisis exploratorio (distribuciones, correlaciones)
  3. feature_engineer  — selección y transformación de features
  4. model_trainer     — entrenamiento del modelo ML
  5. model_evaluator   — evaluación en test set (accuracy, F1, AUC)
  6. [quality_gate]    — si primary_score < 0.7 → retrain (max 3)
  7. model_exporter    — serialización y exportación del modelo

Uso:
    uv run python examples/subgraphs/01_ml_pipeline.py
"""

from __future__ import annotations

import asyncio

from lightagent.agents.state import initial_state
from lightagent.agents.subgraphs.ml_pipeline.builder import (
    build_ml_pipeline_subgraph,
    register_ml_pipeline,
)

# ── Dataset: descripción del problema y datos ─────────────────────────────────
# En un entorno real, el agente accede a archivos CSV/Parquet reales.
# Aquí se proveen metadatos que el agente usará para orquestar el pipeline.

ML_TASK = {
    "dataset_name": "UCI Heart Disease",
    "dataset_path": "data/heart_disease.csv",  # ruta que usaría el agente
    "description": (
        "Predicción de enfermedad cardíaca en 303 pacientes. "
        "14 características: edad, sexo, tipo de dolor en el pecho, "
        "presión sanguínea, colesterol, glucosa en ayunas, "
        "resultados del ECG, frecuencia cardíaca máxima, angina, "
        "depresión ST, pendiente ST, número de vasos coloreados, "
        "thal, y objetivo (0=no enfermedad, 1=enfermedad)."
    ),
    "target_column": "target",
    "task_type": "binary_classification",
    "evaluation_metric": "accuracy",
    "target_score": 0.80,
    "train_test_split": 0.8,
    "algorithms_to_try": ["RandomForest", "GradientBoosting", "LogisticRegression"],
}


async def main() -> None:
    print("=" * 70)
    print("  ML Pipeline Subgraph — Dataset: UCI Heart Disease")
    print("=" * 70)

    # Registrar e inicializar el subgraph
    print("\n[Inicializando subgraph ML Pipeline]")
    await register_ml_pipeline()
    subgraph = await build_ml_pipeline_subgraph()
    print("  ✓ Subgraph compilado con 6 nodos + quality gate")

    # Mostrar arquitectura
    print("\n[Arquitectura del subgraph]")
    print("  data_ingester")
    print("       ↓")
    print("  eda_analyst")
    print("       ↓")
    print("  feature_engineer")
    print("       ↓")
    print("  model_trainer")
    print("       ↓")
    print("  model_evaluator")
    print("       ↓")
    print("  [quality_gate: score >= 0.7?]")
    print("       ├── SÍ → model_exporter → END")
    print("       └── NO → model_trainer (re-entrenamiento, max 3 ciclos)")

    # Preparar el estado inicial
    state = initial_state()
    state["messages"] = []
    state["metadata"] = {
        "task": ML_TASK,
        "pipeline": "ml_pipeline",
        "thread_id": "ml_heart_disease_001",
    }

    # Configuración del thread de LangGraph
    config = {
        "configurable": {
            "thread_id": "ml_heart_disease_001",
            "recursion_limit": 20,
        }
    }

    # Mensaje inicial con instrucciones
    from langchain_core.messages import HumanMessage

    state["messages"] = [
        HumanMessage(
            content=(
                f"Ejecuta un pipeline completo de Machine Learning para el dataset: "
                f"{ML_TASK['dataset_name']}.\n\n"
                f"Descripción: {ML_TASK['description']}\n\n"
                f"Tarea: {ML_TASK['task_type']}\n"
                f"Métrica objetivo: {ML_TASK['evaluation_metric']} >= {ML_TASK['target_score']}\n"
                f"Algoritmos a probar: {', '.join(ML_TASK['algorithms_to_try'])}"
            )
        )
    ]

    print(f"\n[Ejecutando pipeline para: {ML_TASK['dataset_name']}]")
    print(f"  Tarea       : {ML_TASK['task_type']}")
    print(f"  Score mínimo: {ML_TASK['target_score']}")
    print(f"  Algoritmos  : {ML_TASK['algorithms_to_try']}")

    # Ejecutar el subgraph
    try:
        final_state = await subgraph.ainvoke(state, config=config)

        print("\n[Resultados del pipeline]")

        # Extraer métricas del metadata
        pipeline_meta = final_state.get("metadata", {}).get("ml_pipeline", {})
        if pipeline_meta:
            print(f"  Score final   : {pipeline_meta.get('primary_score', 'N/A'):.3f}")
            print(f"  Modelo elegido: {pipeline_meta.get('best_model', 'N/A')}")
            print(f"  Iteraciones   : {pipeline_meta.get('training_iterations', 1)}")
            print(f"  Features usadas: {pipeline_meta.get('n_features', 'N/A')}")
            print(f"  Modelo exportado: {pipeline_meta.get('export_path', 'N/A')}")

        # Último mensaje del agente
        messages = final_state.get("messages", [])
        if messages:
            print("\n  Último mensaje del pipeline:")
            print(f"  {str(messages[-1].content)[:300]}")

    except Exception as exc:
        print(f"\n  Error en la ejecución: {exc}")
        print("  (Verifica que el dataset esté disponible y el LLM configurado)")

    # Demostrar uso directo de nodos individuales
    print("\n[Uso alternativo: nodos individuales]")
    print("  from lightagent.agents.subgraphs.ml_pipeline import (")
    print("      data_ingester_node,")
    print("      eda_analyst_node,")
    print("      feature_engineer_node,")
    print("      model_trainer_node,")
    print("      model_evaluator_node,")
    print("      model_exporter_node,")
    print("  )")
    print("  # Cada nodo se puede usar independientemente en un grafo propio")

    # Quality gate
    print("\n[Quality Gate]")
    print("  El score_gate evalúa metadata['ml_pipeline']['primary_score']")
    print("  Si score >= 0.7 → exporta el modelo")
    print("  Si score < 0.7  → re-entrena (max 3 intentos)")
    print("  Si tras 3 intentos no supera 0.7 → exporta el mejor modelo obtenido")


if __name__ == "__main__":
    asyncio.run(main())
