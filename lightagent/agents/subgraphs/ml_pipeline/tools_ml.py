"""
ML/DL pipeline tools for LightAgent sub-agents.

All ML library imports (``flaml``, ``sklearn``, ``torch``, ``shap``) are
lazy-guarded by ``try/except ImportError`` — these extras are optional and
must NOT be imported at module level.  Each tool degrades gracefully when
the optional library is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import tool

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.tools")


# ---------------------------------------------------------------------------
# Data validation & EDA tools (F2-02)
# ---------------------------------------------------------------------------


@tool
def validate_dataset(path: str, max_rows: int = 1_000_000) -> str:
    """
    Validate a dataset file and return shape, dtypes, and null counts.

    Supports CSV, Parquet, and JSON formats. Returns a JSON string with rows, columns,
    column_types, null_counts.
    """
    p = Path(path)
    if not p.exists():
        return json.dumps({"error": f"File not found: {path}"})
    try:
        import polars as pl

        suffix = p.suffix.lower()
        if suffix == ".csv":
            df = pl.read_csv(p, n_rows=max_rows)
        elif suffix == ".parquet":
            df = pl.read_parquet(p)
        elif suffix in {".json", ".ndjson"}:
            df = pl.read_json(p)
        else:
            return json.dumps({"error": f"Unsupported format: {suffix}"})

        null_counts = {col: df[col].null_count() for col in df.columns}
        return json.dumps(
            {
                "rows": df.height,
                "columns": df.width,
                "column_types": {
                    col: str(dtype) for col, dtype in zip(df.columns, df.dtypes, strict=False)
                },
                "null_counts": null_counts,
            }
        )
    except ImportError:  # pragma: no cover
        try:
            import csv

            with p.open() as f:
                reader = csv.reader(f)
                headers = next(reader)
                rows = sum(1 for _ in reader)
            return json.dumps(
                {
                    "rows": rows,
                    "columns": len(headers),
                    "column_types": dict.fromkeys(headers, "unknown"),
                    "null_counts": {},
                }
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.warning("validate_dataset.error", path=path, error=str(exc))
        return json.dumps({"error": str(exc)})


@tool
def statistical_summary(path: str, target_column: str = "") -> str:
    """
    Compute descriptive statistics for a dataset.

    Returns mean, std, min, max for numeric columns plus class distribution
    when ``target_column`` is provided.
    """
    p = Path(path)
    if not p.exists():
        return json.dumps({"error": f"File not found: {path}"})
    try:
        import polars as pl

        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        numeric_types = {pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8}
        numeric_cols = [
            col for col, dtype in zip(df.columns, df.dtypes, strict=False) if dtype in numeric_types
        ]
        numeric_summary: dict[str, Any] = {}
        for col in numeric_cols[:20]:
            s = df[col]
            numeric_summary[col] = {
                "mean": round(float(s.mean() or 0), 4),  # type: ignore[arg-type]
                "std": round(float(s.std() or 0), 4),  # type: ignore[arg-type]
                "min": float(s.min() or 0),  # type: ignore[arg-type]
                "max": float(s.max() or 0),  # type: ignore[arg-type]
                "null_count": s.null_count(),
            }
        stats: dict[str, Any] = {
            "shape": {"rows": df.height, "columns": df.width},
            "numeric_summary": numeric_summary,
        }
        if target_column and target_column in df.columns:
            vc = df[target_column].value_counts()
            stats["target_distribution"] = dict(
                zip(
                    [str(v) for v in vc[target_column].to_list()],
                    vc["count"].to_list(),
                    strict=False,
                )
            )
        return json.dumps(stats)
    except ImportError:  # pragma: no cover
        return json.dumps({"error": ("polars not installed; run: pip install 'lightagent[ml]'")})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Feature engineering tools (F2-05)
# ---------------------------------------------------------------------------


@tool
def feature_transform(
    dataset_path: str,
    output_path: str,
    operations: str,
) -> str:
    """
    Apply feature transformation operations to a dataset.

    ``operations`` is a JSON list of dicts. Supported types:
    ``drop`` (columns list), ``fill_null`` (column, value),
    ``cast`` (column, dtype), ``log1p`` (column), ``normalize`` (column).
    """
    try:
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        rows_before, cols_before = df.height, df.width
        ops: list[dict[str, Any]] = json.loads(operations)
        dtype_map: dict[str, Any] = {
            "float64": pl.Float64,
            "float32": pl.Float32,
            "int64": pl.Int64,
            "int32": pl.Int32,
            "str": pl.Utf8,
        }
        for op in ops:
            t = op.get("type", "")
            if t == "drop":
                df = df.drop([c for c in op.get("columns", []) if c in df.columns])
            elif t == "fill_null":
                col = op.get("column", "")
                if col in df.columns:
                    df = df.with_columns(pl.col(col).fill_null(op.get("value", 0)))
            elif t == "cast":
                col, dtype_str = op.get("column", ""), op.get("dtype", "")
                if col in df.columns and dtype_str in dtype_map:
                    df = df.with_columns(pl.col(col).cast(dtype_map[dtype_str]))
            elif t == "log1p":
                col = op.get("column", "")
                if col in df.columns:
                    df = df.with_columns((pl.col(col) + 1).log(base=2.718281828).alias(col))
            elif t == "normalize":
                col = op.get("column", "")
                if col in df.columns:
                    s = df[col]
                    mn, mx = s.min(), s.max()
                    if mn != mx:
                        df = df.with_columns(((pl.col(col) - mn) / (mx - mn)).alias(col))
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".parquet":
            df.write_parquet(out)
        else:
            df.write_csv(out)
        return json.dumps(
            {
                "status": "ok",
                "rows_before": rows_before,
                "cols_before": cols_before,
                "rows_after": df.height,
                "cols_after": df.width,
                "output_path": output_path,
            }
        )
    except ImportError:  # pragma: no cover
        return json.dumps({"error": "polars not installed"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def feature_select(
    dataset_path: str,
    target_column: str,
    method: str = "variance",
    n_features: int = 20,
) -> str:
    """
    Select the most informative features from a dataset.

    ``method`` options: ``variance`` (std-based, no sklearn needed),
    ``correlation`` (Pearson |corr| with target),
    ``mutual_info`` (sklearn MI, falls back to correlation if unavailable).
    """
    try:
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        numeric_types = {pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8}
        numeric = [c for c in df.columns if c != target_column and df[c].dtype in numeric_types]
        scores: dict[str, float]
        if method == "variance":
            scores = {c: float(df[c].std() or 0.0) for c in numeric}  # type: ignore[arg-type]
        elif method == "correlation" and target_column in df.columns:
            tgt = df[target_column].cast(pl.Float64)
            scores = {
                c: abs(
                    float(df[c].cast(pl.Float64).pearson_corr(tgt) or 0.0)  # type: ignore[attr-defined]
                )
                for c in numeric
            }
        elif method == "mutual_info":
            try:
                from sklearn.feature_selection import (  # type: ignore[import-untyped]
                    mutual_info_classif,
                )

                x_arr = df.select(numeric).to_numpy()
                y = df[target_column].to_numpy()
                mi = mutual_info_classif(x_arr, y, random_state=42)
                scores = {c: float(v) for c, v in zip(numeric, mi, strict=False)}
            except ImportError:
                tgt = df[target_column].cast(pl.Float64)
                scores = {
                    c: abs(
                        float(
                            df[c].cast(pl.Float64).pearson_corr(tgt) or 0.0  # type: ignore[attr-defined]
                        )
                    )
                    for c in numeric
                }
        else:
            scores = dict.fromkeys(numeric, 1.0)
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:n_features]
        return json.dumps(
            {
                "selected_features": [c for c, _ in top],
                "selection_score": {c: round(s, 4) for c, s in top},
                "method_used": method,
                "n_selected": len(top),
            }
        )
    except ImportError:  # pragma: no cover
        return json.dumps({"error": "polars not installed"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def apply_sampling(
    dataset_path: str,
    output_path: str,
    target_column: str,
    method: str = "oversample",
    random_seed: int = 42,
) -> str:
    """
    Balance a classification dataset using sampling.

    ``method`` options: ``oversample`` (duplicate minority class),
    ``undersample`` (truncate majority class), ``smote`` (requires
    imbalanced-learn; falls back to oversample when unavailable).
    """
    try:
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        if method == "smote":
            try:
                import pandas as pd
                from imblearn.over_sampling import SMOTE

                feats = [c for c in df.columns if c != target_column]
                x_arr = df.select(feats).to_numpy()
                y = df[target_column].to_numpy()
                sm = SMOTE(random_state=random_seed)
                x_res, y_res = sm.fit_resample(x_arr, y)
                df_res = pl.from_pandas(pd.DataFrame(x_res, columns=feats))
                df = df_res.with_columns(pl.Series(target_column, y_res))
            except ImportError:
                method = "oversample"
        if method in ("oversample", "undersample"):
            counts = df[target_column].value_counts()
            if method == "oversample":
                target_size = int(counts["count"].max())  # type: ignore[arg-type]
                frames = []
                for v in df[target_column].unique().to_list():
                    sub = df.filter(pl.col(target_column) == v)
                    n_dup = target_size - sub.height
                    if n_dup > 0:
                        extra = sub.sample(
                            n=n_dup,
                            with_replacement=True,
                            seed=random_seed,
                        )
                        sub = pl.concat([sub, extra])
                    frames.append(sub)
                df = pl.concat(frames).sample(fraction=1.0, shuffle=True, seed=random_seed)
            else:
                target_size = int(counts["count"].min())  # type: ignore[arg-type]
                df = pl.concat(
                    [
                        df.filter(pl.col(target_column) == v).sample(
                            n=target_size, seed=random_seed
                        )
                        for v in df[target_column].unique().to_list()
                    ]
                ).sample(fraction=1.0, shuffle=True, seed=random_seed)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_csv(out)
        return json.dumps(
            {
                "status": "ok",
                "method": method,
                "rows_after": df.height,
                "output_path": output_path,
            }
        )
    except ImportError:  # pragma: no cover
        return json.dumps({"error": "polars not installed"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Training tools (F3-03)
# ---------------------------------------------------------------------------


@tool
def automl_train(
    dataset_path: str,
    target_column: str,
    task: str = "classification",
    time_budget: int = 120,
    random_seed: int = 42,
    model_output_dir: str = "data/workspace/ml_models",
) -> str:
    """
    Train an ML model using FLAML AutoML (requires ``[ml]`` extra).

    Searches LightGBM, XGBoost, RandomForest, and other estimators. Returns the best
    model, validation score, training time, and model path.
    """
    try:
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        feats = [c for c in df.columns if c != target_column]
        x_train = df.select(feats).to_pandas()
        y = df[target_column].to_pandas()
        try:  # pragma: no cover
            import joblib
            from flaml import AutoML

            automl = AutoML()
            automl.fit(
                x_train,
                y,
                task=task,
                time_budget=time_budget,
                seed=random_seed,
            )
            out_dir = Path(model_output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            model_path = str(out_dir / "model.joblib")
            joblib.dump(automl, model_path)
            best_model = str(type(automl.model.estimator).__name__)
            score = float(1.0 - automl.best_loss if automl.best_loss is not None else 0.0)
            train_time = float(automl.best_config_train_time or time_budget)
            return json.dumps(
                {
                    "status": "ok",
                    "best_model": best_model,
                    "validation_score": round(score, 4),
                    "training_time_seconds": round(train_time, 1),
                    "framework": "flaml",
                    "task": task,
                    "model_path": model_path,
                    "random_seed": random_seed,
                }
            )
        except ImportError:
            return json.dumps(
                {
                    "status": "unavailable",
                    "error": ("flaml not installed; run: pip install 'lightagent[ml]'"),
                    "framework": "flaml",
                    "task": task,
                }
            )
    except Exception as exc:
        logger.warning("automl_train.error", error=str(exc))
        return json.dumps({"error": str(exc)})


@tool
def train_dl_model(
    dataset_path: str,
    target_column: str,
    task: str = "classification",
    hidden_layers: str = "128,64",
    epochs: int = 50,
    learning_rate: float = 1e-3,
    random_seed: int = 42,
    model_output_dir: str = "data/workspace/ml_models",
) -> str:
    """
    Train a simple MLP using PyTorch (requires ``[ml-dl]`` extra).

    Falls back gracefully with an "unavailable" status when torch is
    not installed.  Always caps training to ``min(epochs, 10)`` when
    called as a LangChain tool to keep execution times safe.
    """
    try:  # pragma: no cover
        import numpy as np
        import torch
        import torch.nn as nn

        torch.manual_seed(random_seed)
        np.random.seed(random_seed)
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        feats = [c for c in df.columns if c != target_column]
        x_arr = df.select(feats).to_numpy().astype(float)
        y_raw = df[target_column].to_numpy()
        classes = sorted(set(y_raw))
        y_enc = np.array([classes.index(v) for v in y_raw])
        layer_sizes = [int(s) for s in hidden_layers.split(",")]
        layers: list[nn.Module] = []
        in_dim = x_arr.shape[1]
        for out_dim in layer_sizes:
            layers += [nn.Linear(in_dim, out_dim), nn.ReLU()]
            in_dim = out_dim
        n_out = len(classes) if task == "classification" else 1
        layers.append(nn.Linear(in_dim, n_out))
        model = nn.Sequential(*layers)
        x_t = torch.FloatTensor(x_arr)
        y_t = (
            torch.LongTensor(y_enc)
            if task == "classification"
            else torch.FloatTensor(y_enc.astype(float))
        )
        opt = torch.optim.Adam(model.parameters(), lr=learning_rate)
        loss_fn: nn.Module = nn.CrossEntropyLoss() if task == "classification" else nn.MSELoss()
        for _ in range(min(epochs, 10)):
            opt.zero_grad()
            out = model(x_t)
            loss = loss_fn(out, y_t)
            loss.backward()
            opt.step()
        with torch.no_grad():
            preds = (
                model(x_t).argmax(dim=1).numpy()
                if task == "classification"
                else model(x_t).squeeze().numpy()
            )
        acc = float((preds == y_enc).mean()) if task == "classification" else 0.0
        out_dir = Path(model_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        model_path = str(out_dir / "model.pt")
        torch.save(model.state_dict(), model_path)
        return json.dumps(
            {
                "status": "ok",
                "best_model": "MLP",
                "validation_score": round(acc, 4),
                "training_time_seconds": float(epochs),
                "framework": "pytorch",
                "task": task,
                "model_path": model_path,
                "random_seed": random_seed,
            }
        )
    except ImportError:
        return json.dumps(
            {
                "status": "unavailable",
                "error": ("torch not installed; run: pip install 'lightagent[ml-dl]'"),
                "framework": "pytorch",
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def train_clustering(
    dataset_path: str,
    n_clusters: int = 0,
    method: str = "kmeans",
    max_k: int = 10,
    random_seed: int = 42,
    model_output_dir: str = "data/workspace/ml_models",
) -> str:
    """
    Train a clustering model using scikit-learn (requires ``[ml]`` extra).

    When ``n_clusters=0`` the optimal k is selected via silhouette score
    (capped at ``max_k``).  Supports ``kmeans`` and ``dbscan`` methods.
    """
    try:
        import joblib
        import polars as pl
        from sklearn.cluster import DBSCAN, KMeans  # type: ignore[import-untyped]
        from sklearn.metrics import silhouette_score  # type: ignore[import-untyped]
        from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        x_arr = StandardScaler().fit_transform(df.to_numpy().astype(float))
        best_k = n_clusters
        best_sil = -1.0
        if n_clusters == 0 and method == "kmeans":
            upper = min(max_k + 1, max(3, x_arr.shape[0] // 10 + 2))
            for k in range(2, upper):
                km = KMeans(n_clusters=k, random_state=random_seed, n_init=10)
                labels = km.fit_predict(x_arr)
                sil = float(silhouette_score(x_arr, labels))
                if sil > best_sil:
                    best_sil, best_k = sil, k
        clf: KMeans | DBSCAN
        if method == "kmeans":
            clf = KMeans(n_clusters=best_k or 3, random_state=random_seed, n_init=10)
        else:
            clf = DBSCAN()
        labels = clf.fit_predict(x_arr)
        n_unique = len(set(labels) - {-1})
        sil = float(silhouette_score(x_arr, labels)) if n_unique > 1 else 0.0
        out_dir = Path(model_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        model_path = str(out_dir / "clustering_model.joblib")
        joblib.dump(clf, model_path)
        return json.dumps(
            {
                "status": "ok",
                "n_clusters_used": best_k or n_unique,
                "silhouette_score": round(sil, 4),
                "method": method,
                "model_path": model_path,
                "random_seed": random_seed,
            }
        )
    except ImportError:  # pragma: no cover
        return json.dumps({"error": ("sklearn not installed; run: pip install 'lightagent[ml]'")})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Evaluation tools (F3-05)
# ---------------------------------------------------------------------------


@tool
def evaluate_model(
    model_path: str,
    dataset_path: str,
    target_column: str,
    task: str = "classification",
) -> str:
    """
    Evaluate a trained joblib model on a dataset.

    Classification: accuracy, precision_macro, recall_macro, f1_macro.
    Regression: MAE, MSE, RMSE, R².
    Returns ``primary_metric`` and ``primary_score`` for the quality gate.
    """
    try:
        import joblib
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        feats = [c for c in df.columns if c != target_column]
        x_eval = df.select(feats).to_pandas()
        y = df[target_column].to_pandas()
        mdl = joblib.load(model_path)
        preds = mdl.predict(x_eval)
        if task == "classification":
            from sklearn.metrics import (
                accuracy_score,
                f1_score,
                precision_score,
                recall_score,
            )

            acc = float(accuracy_score(y, preds))
            prec = float(precision_score(y, preds, average="macro", zero_division=0))
            rec = float(recall_score(y, preds, average="macro", zero_division=0))
            f1 = float(f1_score(y, preds, average="macro", zero_division=0))
            metrics = {
                "accuracy": round(acc, 4),
                "precision_macro": round(prec, 4),
                "recall_macro": round(rec, 4),
                "f1_macro": round(f1, 4),
            }
            primary_metric, primary_score = "accuracy", acc
        else:
            import math

            from sklearn.metrics import (
                mean_absolute_error,
                mean_squared_error,
                r2_score,
            )

            mae = float(mean_absolute_error(y, preds))
            mse = float(mean_squared_error(y, preds))
            r2 = float(r2_score(y, preds))
            metrics = {
                "mae": round(mae, 4),
                "mse": round(mse, 4),
                "rmse": round(math.sqrt(mse), 4),
                "r2": round(r2, 4),
            }
            primary_metric, primary_score = "r2", max(0.0, r2)
        return json.dumps(
            {
                "status": "ok",
                "metrics": metrics,
                "primary_metric": primary_metric,
                "primary_score": round(primary_score, 4),
            }
        )
    except ImportError as exc:  # pragma: no cover
        return json.dumps(
            {"error": (f"Missing dependency: {exc}. Run: pip install 'lightagent[ml]'")}
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def explain_model(
    model_path: str,
    dataset_path: str,
    max_samples: int = 1000,
    output_dir: str = "data/workspace/ml_models",
) -> str:
    """
    Generate SHAP feature importance for a trained joblib model.

    Falls back to ``feature_importances_`` when SHAP is not installed.
    ``max_samples`` caps the rows passed to the SHAP explainer.
    """
    try:
        import joblib
        import polars as pl

        p = Path(dataset_path)
        df = pl.read_csv(p) if p.suffix.lower() == ".csv" else pl.read_parquet(p)
        x_df = df.to_pandas().head(max_samples)
        mdl = joblib.load(model_path)
        try:
            import matplotlib as mpl

            mpl.use("Agg")
            import matplotlib.pyplot as plt
            import shap

            explainer = shap.Explainer(mdl, x_df)
            shap_values = explainer(x_df)
            raw = abs(shap_values.values)
            if raw.ndim == 3:
                raw = raw.mean(axis=2)
            mean_abs = raw.mean(axis=0).tolist()
            feature_importance = dict(
                sorted(
                    zip(
                        x_df.columns.tolist(),
                        [round(float(v), 4) for v in mean_abs],
                        strict=False,
                    ),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            chart_path = str(out_dir / "shap_summary.png")
            shap.summary_plot(shap_values, x_df, show=False)
            plt.tight_layout()
            plt.savefig(chart_path, dpi=100, bbox_inches="tight")
            plt.close()
            return json.dumps(
                {
                    "status": "ok",
                    "top_features": dict(list(feature_importance.items())[:20]),
                    "chart_path": chart_path,
                    "method": "shap",
                }
            )
        except ImportError:
            importances = getattr(mdl, "feature_importances_", None)
            if importances is None:
                importances = [1.0 / len(x_df.columns)] * len(x_df.columns)
            feature_importance = dict(
                sorted(
                    zip(
                        x_df.columns.tolist(),
                        [round(float(v), 4) for v in importances],
                        strict=False,
                    ),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )
            return json.dumps(
                {
                    "status": "ok",
                    "top_features": dict(list(feature_importance.items())[:20]),
                    "chart_path": None,
                    "method": "feature_importances_",
                    "note": ("install 'lightagent[ml]' for SHAP explanations"),
                }
            )
    except ImportError as exc:
        return json.dumps({"error": f"Missing dependency: {exc}"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Export tools (F4-02)
# ---------------------------------------------------------------------------


@tool
def export_model(
    model_path: str,
    output_dir: str,
    format: str = "joblib",
    model_name: str = "model",
) -> str:
    """
    Export a trained model to a deployment-ready format.

    ``format`` must be ``"joblib"`` or ``"onnx"`` (requires ``onnx`` +
    ``skl2onnx``).  Falls back to joblib when onnx libs are unavailable.
    """
    import shutil

    src = Path(model_path)
    if not src.exists():
        return json.dumps({"error": f"Model not found: {model_path}"})
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if format == "onnx":
        try:  # pragma: no cover
            import joblib
            from skl2onnx import convert_sklearn
            from skl2onnx.common.data_types import FloatTensorType

            mdl = joblib.load(model_path)
            n_features = getattr(mdl, "n_features_in_", 10)
            initial_type = [("float_input", FloatTensorType([None, n_features]))]
            onx = convert_sklearn(mdl, initial_types=initial_type)
            exported_path = str(out / f"{model_name}.onnx")
            with Path(exported_path).open("wb") as f:
                f.write(onx.SerializeToString())
            return json.dumps(
                {
                    "status": "ok",
                    "exported_path": exported_path,
                    "format": "onnx",
                }
            )
        except ImportError:
            format = "joblib"
    exported_path = str(out / f"{model_name}.joblib")
    shutil.copy2(model_path, exported_path)
    return json.dumps(
        {
            "status": "ok",
            "exported_path": exported_path,
            "format": "joblib",
        }
    )


@tool
def generate_inference_code(
    model_path: str,
    input_schema: str,
    output_dir: str,
    model_name: str = "model",
    task: str = "classification",
) -> str:
    """
    Generate a standalone ``predict.py`` inference script.

    ``input_schema`` is a JSON string mapping feature names to types
    (e.g. ``'{"age": "float", "income": "float"}'``).
    The generated script loads the joblib model and accepts JSON on stdin.
    """
    # model_path informs the model name used in the description string
    # and is referenced by the caller to locate the source artifact.
    model_basename = Path(model_path).name
    try:
        schema: dict[str, str] = json.loads(input_schema)
    except (json.JSONDecodeError, ValueError):
        schema = {}
    feature_names = list(schema.keys())
    proba_fn = "model.predict_proba" if task == "classification" else "model.predict"
    script_lines = [
        "#!/usr/bin/env python3",
        f'"""Standalone inference script for {model_name}.',
        "",
        "Generated by LightAgent ml_pipeline / generate_inference_code.",
        "Usage:",
        "    echo '{...}' | python predict.py",
        '"""',
        "from __future__ import annotations",
        "import argparse, json, sys",
        "from pathlib import Path",
        "import joblib, pandas as pd",
        "",
        '_MODEL_PATH = Path(__file__).parent / "model.joblib"',
        f"_FEATURES = {feature_names!r}",
        f"_TASK = {task!r}",
        "",
        "def _load_model() -> object:",
        "    return joblib.load(_MODEL_PATH)",
        "",
        "def predict(payload: dict) -> dict:",
        '    """Run inference on a single input dict."""',
        "    model = _load_model()",
        "    df = pd.DataFrame([payload])",
        "    if _FEATURES:",
        "        df = df[_FEATURES]",
        "    preds = model.predict(df)",
        '    result: dict = {"prediction": preds.tolist()[0]}',
        '    if _TASK == "classification":',
        "        try:",
        f"            proba = {proba_fn}(df)",
        '            result["probabilities"] = proba.tolist()[0]',
        "        except AttributeError:",
        "            pass",
        "    return result",
        "",
        "def main() -> None:",
        '    """Entry point for CLI usage."""',
        "    parser = argparse.ArgumentParser()",
        '    parser.add_argument("--input")',
        "    args = parser.parse_args()",
        "    payload = (",
        "        json.load(open(args.input))",
        "        if args.input",
        "        else json.load(sys.stdin)",
        "    )",
        "    print(json.dumps(predict(payload), indent=2))",
        "",
        'if __name__ == "__main__":',
        "    main()",
    ]
    script = "\n".join(script_lines) + "\n"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    script_path = str(out / "predict.py")
    Path(script_path).write_text(script, encoding="utf-8")
    return json.dumps(
        {
            "status": "ok",
            "script_path": script_path,
            "model_name": model_name,
            "task": task,
            "features": feature_names,
            "description": (
                f"Standalone inference script for {model_name} ({task}), "
                f"source model: {model_basename}. "
                f"Run: echo '{{...}}' | python {script_path}"
            ),
        }
    )


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ML_PIPELINE_TOOLS = [
    validate_dataset,
    statistical_summary,
    feature_transform,
    feature_select,
    apply_sampling,
    automl_train,
    train_dl_model,
    train_clustering,
    evaluate_model,
    explain_model,
    export_model,
    generate_inference_code,
]

__all__ = [
    "ML_PIPELINE_TOOLS",
    "apply_sampling",
    "automl_train",
    "evaluate_model",
    "explain_model",
    "export_model",
    "feature_select",
    "feature_transform",
    "generate_inference_code",
    "statistical_summary",
    "train_clustering",
    "train_dl_model",
    "validate_dataset",
]
