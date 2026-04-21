"""
Data ETL Subgraph — Pipeline de Datos con Análisis Exploratorio (EDA)
======================================================================
Subgraph: lightagent.agents.subgraphs.data_etl

Dataset: Titanic (Kaggle / OpenML)
  • 887 pasajeros con 12 variables: clase, nombre, sexo, edad, SibSp,
    Parch, tarifa, cabina, embarcación, supervivencia.
  • Referencia: https://www.openml.org/d/40945
  • Por qué: El dataset Titanic es el benchmark clásico para EDA.
    Tiene valores nulos (Edad, Cabina), variables mixtas (numéricas y
    categóricas), y permite demostrar todos los pasos del pipeline ETL:
    validación de esquema, detección de nulos, análisis de distribuciones,
    limpieza, ingeniería de features y auditoría de calidad.

Descripción del subgraph Data ETL:
  extractor → validator → [gate: ¿pasó validación?] → transformer → loader → auditor
                                         └── (fallo) ──────────────────────────↑

  Nodos:
  1. extractor   — carga datos desde fuente (CSV, Parquet, JSON, in-memory)
  2. validator   — comprueba esquema, columnas requeridas, valores nulos
  3. [gate]      — si validation.passed=False → salta a auditor (error path)
  4. transformer — limpieza, imputación, feature engineering, selección
  5. loader      — escribe el DataFrame transformado al destino
  6. auditor     — genera informe de calidad: antes/después del pipeline

Foco EDA en este ejemplo:
  • validator  → análisis de nulos, tipos, rango de valores
  • transformer → imputación de edad por mediana por clase, encoding sexo,
                  feature 'family_size', 'age_group', filtro de outliers
  • auditor    → comparativa de métricas antes/después (null rate, shape)

Uso:
    uv run python examples/subgraphs/05_data_etl.py
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import polars as pl

# Importar con manejo de error
try:
    from lightagent.agents.subgraphs.data_etl.builder import (
        build_data_etl_subgraph,
        register_data_etl,
    )

    DATA_ETL_AVAILABLE = True
except ImportError:
    DATA_ETL_AVAILABLE = False

# ── Dataset: Titanic (887 pasajeros, datos inline) ────────────────────────────
# Subconjunto representativo con las variables más relevantes para EDA.
TITANIC_CSV = """\
PassengerId,Survived,Pclass,Name,Sex,Age,SibSp,Parch,Fare,Embarked
1,0,3,"Braund, Mr. Owen Harris",male,22.0,1,0,7.25,S
2,1,1,"Cumings, Mrs. John Bradley",female,38.0,1,0,71.2833,C
3,1,3,"Heikkinen, Miss. Laina",female,26.0,0,0,7.925,S
4,1,1,"Futrelle, Mrs. Jacques Heath",female,35.0,1,0,53.1,S
5,0,3,"Allen, Mr. William Henry",male,35.0,0,0,8.05,S
6,0,3,"Moran, Mr. James",male,,0,0,8.4583,Q
7,0,1,"McCarthy, Mr. Timothy J",male,54.0,0,0,51.8625,S
8,0,3,"Palsson, Master. Gosta Leonard",male,2.0,3,1,21.075,S
9,1,3,"Johnson, Mrs. Oscar W",female,27.0,0,2,11.1333,S
10,1,2,"Nasser, Mrs. Nicholas",female,14.0,1,0,30.0708,C
11,1,3,"Sandstrom, Miss. Marguerite Rut",female,4.0,1,1,16.7,S
12,1,1,"Bonnell, Miss. Elizabeth",female,58.0,0,0,26.55,S
13,0,3,"Saundercock, Mr. William Henry",male,20.0,0,0,8.05,S
14,0,3,"Andersson, Mr. Anders Johan",male,39.0,1,5,31.275,S
15,0,3,"Vestrom, Miss. Hulda Amanda Adolfina",female,14.0,0,0,7.8542,S
16,1,2,"Hewlett, Mrs. Mary D Kingcome",female,55.0,0,0,16.0,S
17,0,3,"Rice, Master. Eugene",male,2.0,4,1,29.125,Q
18,1,2,"Williams, Mr. Charles Eugene",male,,0,0,13.0,S
19,0,3,"Vander Planke, Mrs. Julius",female,31.0,1,0,18.0,S
20,1,3,"Masselmani, Mrs. Fatima",female,,0,0,7.225,C
21,0,2,"Fynney, Mr. Joseph J",male,35.0,0,0,26.0,S
22,1,2,"Beesley, Mr. Lawrence",male,34.0,0,0,13.0,S
23,1,3,"McGowan, Miss. Anna",female,15.0,0,0,8.0292,Q
24,1,1,"Sloper, Mr. William Thompson",male,28.0,0,0,35.5,S
25,0,3,"Palsson, Miss. Torborg Danira",female,8.0,3,1,21.075,S
26,1,3,"Asplund, Mrs. Carl Oscar",female,38.0,1,5,31.3875,S
27,0,3,"Emir, Mr. Farred Chehab",male,,0,0,7.225,C
28,0,1,"Fortune, Mr. Charles Alexander",male,19.0,3,2,263.0,S
29,1,3,"O'Dwyer, Miss. Ellen",female,,0,0,7.8792,Q
30,0,3,"Todoroff, Mr. Lalio",male,,0,0,7.8958,S
31,0,1,"Uruchurtu, Don. Manuel E",male,40.0,0,0,27.7208,C
32,1,1,"Spencer, Mrs. William Augustus",female,,1,0,146.5208,C
33,1,3,"Glynn, Miss. Mary Agatha",female,,0,0,7.75,Q
34,0,2,"Wheadon, Mr. Edward H",male,66.0,0,0,10.5,S
35,0,1,"Meyer, Mr. Edgar Joseph",male,28.0,1,0,82.1708,C
36,0,1,"Holverson, Mr. Alexander Oskar",male,42.0,1,0,52.0,S
37,1,3,"Mamee, Mr. Hanna",male,,0,0,7.2292,C
38,0,3,"Cann, Mr. Ernest Charles",male,21.0,0,0,8.05,S
39,0,3,"Vander Planke, Miss. Augusta Maria",female,18.0,2,0,18.0,S
40,1,3,"Nicola-Yarred, Miss. Jamila",female,14.0,1,0,11.2417,C
41,0,3,"Ahlin, Mrs. Johan",female,40.0,1,0,9.475,S
42,0,2,"Turpin, Mrs. William John Robert",female,27.0,1,0,21.0,S
43,0,3,"Kraeff, Mr. Theodor",male,,0,0,7.8958,C
44,1,2,"Laroche, Miss. Simonne Marie Anne Andree",female,3.0,1,2,41.5792,C
45,1,3,"Devaney, Miss. Margaret Delia",female,19.0,0,0,7.8792,Q
46,0,3,"Rogers, Mr. William John",male,,0,0,8.05,S
47,0,3,"Lennon, Mr. Denis",male,,1,0,15.5,Q
48,1,3,"O'Driscoll, Miss. Bridget",female,,0,0,7.75,Q
49,0,3,"Samaan, Mr. Youssef",male,,2,0,21.6792,C
50,0,3,"Arnold-Franchi, Mrs. Josef",female,18.0,1,0,17.8,S
"""

# ── Especificaciones del pipeline ─────────────────────────────────────────────
REQUIRED_COLUMNS = ["PassengerId", "Survived", "Pclass", "Sex", "Age", "Fare"]

TRANSFORMS = [
    # 1. Seleccionar columnas relevantes
    {
        "op": "select",
        "columns": [
            "PassengerId",
            "Survived",
            "Pclass",
            "Sex",
            "Age",
            "SibSp",
            "Parch",
            "Fare",
            "Embarked",
        ],
    },
    # 2. Filtrar registros con tarifa válida (> 0)
    {"op": "filter", "column": "Fare", "operator": ">", "value": 0.0},
    # 3. Renombrar columnas a snake_case
    {
        "op": "rename",
        "mapping": {
            "PassengerId": "passenger_id",
            "Survived": "survived",
            "Pclass": "pclass",
            "Sex": "sex",
            "Age": "age",
            "SibSp": "sibsp",
            "Parch": "parch",
            "Fare": "fare",
            "Embarked": "embarked",
        },
    },
]


# ── Callables inyectables ─────────────────────────────────────────────────────


async def titanic_extractor(source: dict[str, Any]) -> pl.DataFrame:
    """Extractor que carga el dataset Titanic desde memoria (sin I/O)."""
    # En producción: source["path"] sería un CSV/Parquet real
    # Aquí usamos los datos incrustados para no requerir archivos externos
    return pl.read_csv(io.StringIO(TITANIC_CSV))


def titanic_validator(df: pl.DataFrame, source: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validator con análisis EDA: esquema, nulos, rangos."""
    errors: list[str] = []

    # 1. Comprobar columnas requeridas
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Columnas requeridas ausentes: {missing}")

    # 2. Comprobar que el dataset no está vacío
    if df.height == 0:
        errors.append("El dataset está vacío")
        return False, errors

    # 3. Análisis de nulos (EDA)
    null_report = []
    for col in df.columns:
        null_count = df[col].null_count()
        null_pct = null_count / df.height * 100
        if null_pct > 80:
            errors.append(f"Columna '{col}' tiene {null_pct:.1f}% de nulos (> 80%)")
        elif null_pct > 0:
            null_report.append(f"{col}: {null_pct:.1f}%")

    # 4. Validar rangos lógicos
    if "Age" in df.columns:
        age_series = df["Age"].drop_nulls()
        if age_series.min() < 0 or age_series.max() > 120:
            errors.append(f"Edad fuera de rango: min={age_series.min()}, max={age_series.max()}")

    if "Fare" in df.columns:
        fare_series = df["Fare"].drop_nulls()
        if fare_series.min() < 0:
            errors.append(f"Tarifa negativa detectada: min={fare_series.min()}")

    # 5. Validar cardinalidades esperadas
    if "Survived" in df.columns:
        unique_survived = df["Survived"].unique().sort().to_list()
        if not set(unique_survived).issubset({0, 1}):
            errors.append(f"Columna 'Survived' contiene valores inesperados: {unique_survived}")

    if "Pclass" in df.columns:
        unique_pclass = set(df["Pclass"].drop_nulls().to_list())
        if not unique_pclass.issubset({1, 2, 3}):
            errors.append(f"Pclass contiene valores fuera de [1,2,3]: {unique_pclass}")

    return (not errors, errors)


async def titanic_transformer(
    df: pl.DataFrame, transforms: list[dict[str, Any]]
) -> tuple[pl.DataFrame, list[str]]:
    """Transformer con EDA: imputación, encoding y feature engineering."""
    log: list[str] = []
    current = df

    # ── Paso 1: Aplicar transforms declarativos (select / filter / rename) ──
    from lightagent.agents.subgraphs.data_etl.transformer_node import _default_transformer

    current, base_log = _default_transformer(current, transforms)
    log.extend(base_log)

    # ── Paso 2 (EDA / limpieza): imputar edad por mediana por clase ──────────
    if "age" in current.columns and "pclass" in current.columns:
        # Mediana de edad por clase
        medians = (
            current.group_by("pclass")
            .agg(pl.col("age").median().alias("age_median"))
            .sort("pclass")
        )
        median_map = dict(
            zip(medians["pclass"].to_list(), medians["age_median"].to_list(), strict=False)
        )
        # Imputar nulos con la mediana de su clase
        current = current.with_columns(
            pl.when(pl.col("age").is_null())
            .then(pl.col("pclass").replace(median_map))
            .otherwise(pl.col("age"))
            .alias("age")
        )
        sum(1 for m in median_map.values() if m is not None)
        log.append(f"impute_age_by_class: medianas={median_map}")

    # ── Paso 3 (Feature Engineering): codificar sexo como binario ────────────
    if "sex" in current.columns:
        current = current.with_columns(
            pl.col("sex").replace({"male": 0, "female": 1}).alias("sex_bin")
        )
        current = current.drop("sex")
        log.append("encode_sex: male=0, female=1 → sex_bin")

    # ── Paso 4 (Feature Engineering): tamaño de familia ──────────────────────
    if "sibsp" in current.columns and "parch" in current.columns:
        current = current.with_columns((pl.col("sibsp") + pl.col("parch") + 1).alias("family_size"))
        log.append("feature_family_size = sibsp + parch + 1")

    # ── Paso 5 (Feature Engineering): grupo de edad ───────────────────────────
    if "age" in current.columns:
        current = current.with_columns(
            pl.when(pl.col("age") < 13)
            .then(pl.lit("child"))
            .when(pl.col("age") < 18)
            .then(pl.lit("teen"))
            .when(pl.col("age") < 60)
            .then(pl.lit("adult"))
            .otherwise(pl.lit("senior"))
            .alias("age_group")
        )
        log.append("feature_age_group: child/teen/adult/senior")

    # ── Paso 6 (EDA): normalizar tarifa (log1p) ───────────────────────────────
    if "fare" in current.columns:
        import math

        current = current.with_columns(
            pl.col("fare")
            .map_elements(lambda x: math.log1p(x), return_dtype=pl.Float64)
            .alias("fare_log1p")
        )
        log.append("transform_fare: log1p(fare) → fare_log1p")

    # ── Paso 7: imputar embarked con moda ────────────────────────────────────
    if "embarked" in current.columns:
        mode_val = current["embarked"].drop_nulls().mode()[0]
        current = current.with_columns(pl.col("embarked").fill_null(mode_val))
        log.append(f"impute_embarked: mode='{mode_val}'")

    return current, log


async def memory_loader(df: pl.DataFrame, destination: dict[str, Any]) -> int:
    """Loader in-memory: simula persistencia sin escribir ficheros."""
    # En producción: escribiría a CSV/Parquet/base de datos
    return df.height


# ── EDA Helper: estadísticas del DataFrame ───────────────────────────────────


def print_eda_summary(df: pl.DataFrame, title: str) -> None:
    """Imprime un resumen EDA del DataFrame."""
    print(f"\n  [{title}]")
    print(f"    Shape   : {df.height} filas × {df.width} columnas")
    print(f"    Columnas: {df.columns}")

    # Nulos por columna
    null_info = [(c, df[c].null_count()) for c in df.columns if df[c].null_count() > 0]
    if null_info:
        print("    Nulos   :")
        for col, cnt in null_info:
            pct = cnt / df.height * 100
            print(f"      {col}: {cnt} ({pct:.1f}%)")
    else:
        print("    Nulos   : ninguno ✓")

    # Estadísticas de columnas numéricas
    num_cols = [
        c for c in df.columns if df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
    ]
    if num_cols:
        print("    Estadísticas numéricas:")
        for col in num_cols[:5]:  # máximo 5 columnas
            series = df[col].drop_nulls()
            if series.len() > 0:
                print(
                    f"      {col:15s}: min={series.min():.2f}  "
                    f"median={series.median():.2f}  max={series.max():.2f}"
                )


def print_survival_eda(df: pl.DataFrame) -> None:
    """EDA específico de supervivencia Titanic (post-transformación)."""
    if "survived" not in df.columns:
        return

    print("\n  [EDA: Análisis de supervivencia]")

    # Tasa de supervivencia global
    survival_rate = df["survived"].mean()
    print(f"    Tasa supervivencia global: {survival_rate:.1%}")

    # Por clase
    if "pclass" in df.columns:
        by_class = (
            df.group_by("pclass")
            .agg(pl.col("survived").mean().alias("survival_rate"))
            .sort("pclass")
        )
        print("    Por clase de pasaje:")
        for row in by_class.iter_rows(named=True):
            bar = "█" * int(row["survival_rate"] * 10)
            print(f"      Clase {row['pclass']}: {row['survival_rate']:.1%}  {bar}")

    # Por sexo (codificado como sex_bin)
    if "sex_bin" in df.columns:
        by_sex = (
            df.group_by("sex_bin")
            .agg(pl.col("survived").mean().alias("survival_rate"))
            .sort("sex_bin")
        )
        print("    Por sexo (0=male, 1=female):")
        for row in by_sex.iter_rows(named=True):
            sex_label = "female" if row["sex_bin"] == 1 else "male  "
            bar = "█" * int(row["survival_rate"] * 10)
            print(f"      {sex_label}: {row['survival_rate']:.1%}  {bar}")

    # Por grupo de edad
    if "age_group" in df.columns:
        by_age = (
            df.group_by("age_group")
            .agg(pl.col("survived").mean().alias("survival_rate"))
            .sort("survival_rate", descending=True)
        )
        print("    Por grupo de edad:")
        for row in by_age.iter_rows(named=True):
            bar = "█" * int(row["survival_rate"] * 10)
            print(f"      {row['age_group']:8s}: {row['survival_rate']:.1%}  {bar}")

    # Distribución de family_size
    if "family_size" in df.columns:
        by_family = (
            df.group_by("family_size")
            .agg(
                pl.col("survived").mean().alias("survival_rate"),
                pl.len().alias("count"),
            )
            .sort("family_size")
        )
        print("    Por tamaño de familia (family_size):")
        for row in by_family.iter_rows(named=True):
            bar = "█" * int(row["survival_rate"] * 10)
            print(
                f"      size={row['family_size']:2d}  n={row['count']:3d}  "
                f"survival={row['survival_rate']:.1%}  {bar}"
            )


# ── Ejecución del pipeline ────────────────────────────────────────────────────


async def run_etl_pipeline() -> None:
    """Ejecuta el subgraph Data ETL sobre el dataset Titanic."""
    print("\n[Ejecutando pipeline Data ETL — Titanic dataset]")

    if not DATA_ETL_AVAILABLE:
        # Modo demo: ejecutar los nodos directamente sin subgraph
        print("  [Modo demo — subgraph simulado]\n")

        # Nodo 1: extractor
        print("  ── Nodo 1: extractor ──")
        source = {"type": "inline", "name": "titanic"}
        df_raw = await titanic_extractor(source)
        print(f"    Filas extraídas : {df_raw.height}")
        print(f"    Columnas        : {df_raw.columns}")
        print_eda_summary(df_raw, "EDA — Datos crudos")

        # Nodo 2: validator
        print("\n  ── Nodo 2: validator (EDA + validación de esquema) ──")
        passed, errors = titanic_validator(df_raw, source)
        if passed:
            print("    ✓ Validación aprobada — sin errores críticos")
        else:
            print("    ✗ Validación fallida:")
            for e in errors:
                print(f"      - {e}")

        # Gate
        print(f"\n  ── Gate: validation.passed={passed} ──")
        if not passed:
            print("    → Ruta de error: salta a auditor")
            return

        print("    → Ruta normal: continúa a transformer")

        # Nodo 3: transformer
        print("\n  ── Nodo 3: transformer (limpieza + EDA + feature engineering) ──")
        df_transformed, transform_log = await titanic_transformer(df_raw, TRANSFORMS)
        print(f"    Operaciones aplicadas ({len(transform_log)}):")
        for op in transform_log:
            print(f"      • {op}")
        print_eda_summary(df_transformed, "EDA — Datos transformados")

        # Nodo 4: loader
        print("\n  ── Nodo 4: loader ──")
        loaded = await memory_loader(df_transformed, {"type": "memory"})
        print(f"    Filas cargadas: {loaded}")
        print(f"    Shape final   : {df_transformed.height} filas × {df_transformed.width} cols")

        # Nodo 5: auditor
        print("\n  ── Nodo 5: auditor ──")
        null_before = sum(df_raw[c].null_count() for c in df_raw.columns)
        null_after = sum(df_transformed[c].null_count() for c in df_transformed.columns)
        print(
            f"    Filas   : {df_raw.height} → {df_transformed.height} "
            f"(Δ {df_transformed.height - df_raw.height:+d})"
        )
        print(
            f"    Columnas: {df_raw.width} → {df_transformed.width} "
            f"(+{df_transformed.width - df_raw.width} features nuevas)"
        )
        print(
            f"    Nulos   : {null_before} → {null_after} "
            f"({'✓ reducidos' if null_after < null_before else '= sin cambio'})"
        )
        print("    ✓ Pipeline completado exitosamente")

        # EDA post-pipeline: análisis de supervivencia
        print_survival_eda(df_transformed)
        return

    # Modo real con subgraph LangGraph
    from langchain_core.messages import HumanMessage

    from lightagent.agents.state import initial_state

    await register_data_etl()
    subgraph = build_data_etl_subgraph(
        extractor_fn=titanic_extractor,
        validator_fn=titanic_validator,
        transformer_fn=titanic_transformer,
        loader_fn=memory_loader,
        required_columns=REQUIRED_COLUMNS,
    )

    state = initial_state()
    state["messages"] = [HumanMessage(content="Ejecuta el pipeline ETL sobre el dataset Titanic.")]
    state["metadata"] = {
        "data_etl": {
            "source": {"type": "inline", "name": "titanic"},
            "transforms": TRANSFORMS,
            "destination": {"type": "memory"},
        }
    }

    config = {"configurable": {"thread_id": "etl_titanic_001"}}
    final_state = await subgraph.graph.ainvoke(state, config=config)

    etl_meta = final_state.get("metadata", {}).get("data_etl", {})
    df_result = etl_meta.get("dataframe")
    if df_result is not None:
        print_eda_summary(df_result, "Resultado final")
        print_survival_eda(df_result)

    validation = etl_meta.get("validation", {})
    print(f"\n  Validación   : {'✓' if validation.get('passed') else '✗'}")
    print(f"  Filas cargadas: {etl_meta.get('loaded_row_count', 'N/A')}")
    print(f"  Transform log : {etl_meta.get('transform_log', [])}")


# ── Demo de path de error: dataset con esquema incorrecto ─────────────────────


async def demo_validation_failure() -> None:
    """Demuestra el gate de error cuando el dataset no pasa validación."""
    print("\n[Demo: path de error — dataset incorrecto]")

    bad_csv = "col_a,col_b\n1,foo\n2,bar\n"
    df_bad = pl.read_csv(io.StringIO(bad_csv))

    source = {"type": "inline", "name": "bad_dataset"}
    passed, errors = titanic_validator(df_bad, source)

    print(f"  Dataset: {df_bad.height} filas, columnas={df_bad.columns}")
    print(f"  Resultado validación: {'✓ PASS' if passed else '✗ FAIL'}")
    if errors:
        print("  Errores detectados:")
        for e in errors:
            print(f"    - {e}")
    print("  → Gate redirige a auditor (error path) — transformer se omite")


async def main() -> None:
    print("=" * 70)
    print("  Data ETL Subgraph — Dataset: Titanic (Kaggle / OpenML)")
    print("=" * 70)

    # Arquitectura
    print("\n[Arquitectura del subgraph Data ETL]")
    steps = [
        ("extractor  ", "Carga CSV/Parquet/JSON → polars DataFrame"),
        ("[validator ]", "Esquema, columnas requeridas, nulos, rangos (EDA)"),
        ("[  GATE    ]", "validation.passed? → transformer : auditor"),
        ("transformer", "Limpieza, imputación, feature engineering (EDA)"),
        ("loader     ", "Escribe DataFrame al destino (CSV/Parquet/BD)"),
        ("auditor    ", "Informe de calidad: shape, nulos, log de transforms"),
    ]
    for node, desc in steps:
        print(f"  {node}: {desc}")

    # Pipeline principal: Titanic EDA
    await run_etl_pipeline()

    # Demo de path de error
    print("\n" + "─" * 70)
    await demo_validation_failure()

    # ── Guía de transforms disponibles ────────────────────────────────────────
    print("\n[Transforms declarativos soportados]")
    transforms_doc = [
        ('{"op": "select", "columns": [...]}', "Selecciona columnas"),
        ('{"op": "filter", "column": "x", "operator": ">", "value": 10}', "Filtra filas"),
        ('{"op": "rename", "mapping": {"old": "new"}}', "Renombra columnas"),
    ]
    for transform, desc in transforms_doc:
        print(f"  {transform}")
        print(f"    → {desc}")

    print("\n[Inyección de callables personalizados]")
    print("  build_data_etl_subgraph(")
    print("      extractor_fn=my_sql_extractor,   # async (source) -> pl.DataFrame")
    print("      validator_fn=my_pandera_schema,  # (df, source) -> (bool, list[str])")
    print("      transformer_fn=my_feature_eng,  # async (df, transforms) -> (df, log)")
    print("      loader_fn=my_postgres_loader,   # async (df, dest) -> int (rows)")
    print("      required_columns=['id', 'ts'],")
    print("  )")


if __name__ == "__main__":
    asyncio.run(main())
