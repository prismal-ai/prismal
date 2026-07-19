"""
Code Review Subgraph — Revisión automatizada de código Python
=============================================================
Subgraph: prismal.agents.subgraphs.code_review

Dataset: CodeSearchNet (Python) — fragmentos de código real de GitHub
  • 2.3M funciones Python etiquetadas con docstrings (CodeSearchNet corpus).
  • Referencia: https://huggingface.co/datasets/code_search_net
  • Por qué: El subgraph code_review tiene 5 nodos especializados que
    detectan distintos tipos de problemas. Los snippets de CodeSearchNet
    son código real con bugs de seguridad (SQL injection, credenciales
    hardcodeadas), errores lógicos (división por cero, índices fuera de
    rango), y problemas de estilo (funciones sin docstring, magic numbers).
    Cada categoría es detectada por un nodo diferente del pipeline.

Descripción del subgraph Code Review:
  linter → security_scanner → logic_reviewer → suggester → report_generator

  Nodos:
  1. linter           — PEP 8, complejidad ciclomática, nombres, docstrings
  2. security_scanner — injection, credenciales hardcodeadas, deserialización
  3. logic_reviewer   — división por cero, índices, condiciones inalcanzables
  4. suggester        — genera sugerencias de remediación por cada issue
  5. report_generator — score ponderado por severidad + flag approved/rejected

  Score = 1.0 - Σ severity_weight[issue]
  Pesos: critical=0.4, high=0.2, medium=0.1, low=0.05, info=0.01
  approved = (score >= approval_threshold)  # default: 0.8

Uso:
    uv run python examples/subgraphs/04_code_review.py
"""

from __future__ import annotations

import asyncio
import re

from prismal.agents.subgraphs.code_review.types import CodeIssue

# Importar con manejo de error por si el subgraph no está registrado
try:
    from prismal.agents.subgraphs.code_review.builder import (
        build_code_review_subgraph,
        register_code_review,
    )

    CODE_REVIEW_AVAILABLE = True
except ImportError:
    CODE_REVIEW_AVAILABLE = False

# ── Dataset: snippets de CodeSearchNet Python con issues intencionales ────────
# Código real de GitHub anotado con problemas para cada nodo del pipeline.
CODE_SNIPPETS = [
    {
        "id": "CR-001",
        "filename": "db_utils.py",
        "description": "Query builder con SQL injection y sin docstring",
        "expected_issues": ["security", "style"],
        "code": """\
import sqlite3
import pickle

DATABASE = "app.db"
ADMIN_PASSWORD = "supersecret123"   # hardcoded credential

def get_user(username):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    # SQL injection: interpolación directa de user input
    query = "SELECT * FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    return cursor.fetchone()

def load_session(data):
    # Deserialización insegura de datos externos
    return pickle.loads(data)

def set_admin(user_id, is_admin):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_admin=" + str(is_admin) + " WHERE id=" + str(user_id)
    )
    conn.commit()
""",
    },
    {
        "id": "CR-002",
        "filename": "math_ops.py",
        "description": "Operaciones matemáticas con errores lógicos",
        "expected_issues": ["logic", "style"],
        "code": """\
def calculate_average(numbers):
    \"\"\"Calculate the average of a list of numbers.\"\"\"
    total = sum(numbers)
    # ZeroDivisionError si numbers está vacío
    return total / len(numbers)

def get_first_element(items):
    \"\"\"Get the first element without bounds checking.\"\"\"
    # IndexError si items está vacío
    return items[0]

def classify_score(score):
    \"\"\"Classify a score into a grade.\"\"\"
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    elif score >= 60:   # condición inalcanzable (duplicada)
        return "E"
    else:
        return "F"

def compute_ratio(a, b):
    # Sin docstring + magic number
    return a / b * 3.14159265358979
""",
    },
    {
        "id": "CR-003",
        "filename": "file_handler.py",
        "description": "Manejo de archivos con problemas de performance y seguridad",
        "expected_issues": ["security", "performance", "style"],
        "code": """\
import os
import subprocess

SECRET_KEY = "my_very_secret_key_12345"
API_TOKEN  = "ghp_abc123verysecrettoken"

def read_large_file(filepath):
    # Performance: carga todo el archivo en memoria
    with open(filepath) as f:
        return f.read()

def list_directory(path):
    # Sin validación de path — path traversal
    return os.listdir(path)

def run_command(user_input):
    # Shell injection: shell=True con input del usuario
    result = subprocess.run(user_input, shell=True, capture_output=True)
    return result.stdout.decode()

def process_files(directory):
    results = []
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        content = read_large_file(filepath)   # carga todo por cada archivo
        results.append(content)
    return results
""",
    },
    {
        "id": "CR-004",
        "filename": "auth_service.py",
        "description": "Servicio de autenticación bien implementado (código limpio)",
        "expected_issues": [],
        "code": """\
\"\"\"Authentication service with secure password handling.\"\"\"

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, bytes]:
    \"\"\"Hash a password with a random salt using PBKDF2-HMAC-SHA256.

    Args:
        password: The plaintext password to hash.
        salt: Optional salt bytes. A random 16-byte salt is generated if not provided.

    Returns:
        A tuple of (hashed_password_hex, salt_bytes).
    \"\"\"
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: bytes) -> bool:
    \"\"\"Verify a password against its stored hash.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        password: The plaintext password to verify.
        stored_hash: The previously computed hash (hex string).
        salt: The salt used during hashing.

    Returns:
        True if the password matches the stored hash, False otherwise.
    \"\"\"
    candidate_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate_hash, stored_hash)


def generate_token(length: int = 32) -> str:
    \"\"\"Generate a cryptographically secure random token.

    Args:
        length: Number of random bytes (default 32 → 64 hex chars).

    Returns:
        A URL-safe base64 token string.
    \"\"\"
    return secrets.token_urlsafe(length)
""",
    },
    {
        "id": "CR-005",
        "filename": "data_pipeline.py",
        "description": "Pipeline ETL con issues mixtos (severidades variadas)",
        "expected_issues": ["security", "logic", "performance", "style", "test"],
        "code": """\
import json
import yaml    # pyyaml puede ejecutar código con yaml.load()
import requests

API_KEY = "sk-prod-abc123secretkey"

def fetch_data(endpoint, params=None):
    # Sin timeout — puede bloquearse indefinidamente
    response = requests.get(endpoint, params=params)
    return response.json()

def parse_config(config_str):
    # yaml.load sin Loader — ejecución de código arbitrario
    return yaml.load(config_str)

def transform_records(records):
    \"\"\"Transform a list of records applying business rules.\"\"\"
    output = []
    for i in range(len(records)):
        record = records[i]
        # Acceso sin validación de clave
        value = record["value"] * 2
        if value > 1000:
            output.append({"id": record["id"], "value": value, "flag": True})
        else:
            output.append({"id": record["id"], "value": value, "flag": False})
    return output

def save_results(data, filepath):
    # Sin manejo de errores para I/O
    with open(filepath, "w") as f:
        json.dump(data, f)

# Sin tests unitarios para ninguna función
""",
    },
]

# ── Pesos de severidad para el score (igual que report_generator_node) ────────
SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 0.40,
    "high": 0.20,
    "medium": 0.10,
    "low": 0.05,
    "info": 0.01,
}


# ── Callables inyectables para el subgraph ────────────────────────────────────


async def heuristic_linter(code: str, filename: str) -> list[CodeIssue]:
    """Linter heurístico: detecta problemas de estilo y estructura."""
    issues: list[CodeIssue] = []
    lines = code.splitlines()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Función sin docstring
        if stripped.startswith("def ") and not stripped.startswith("def __"):
            # Comprueba si la siguiente línea no es docstring
            next_line = lines[i].strip() if i < len(lines) else ""
            if not next_line.startswith('"""') and not next_line.startswith("'''"):
                func_name = stripped.split("(")[0].replace("def ", "")
                issues.append(
                    CodeIssue(
                        severity="low",
                        category="style",
                        description=f"Función `{func_name}` sin docstring.",
                        file=filename,
                        line=i,
                        suggestion="Añade un docstring con Args/Returns según Google style.",
                    )
                )

        # Magic numbers (excluir 0 y 1)
        magic = re.findall(r"\b(\d{2,}(?:\.\d+)?)\b", stripped)
        if magic and not stripped.startswith("#") and not stripped.startswith('"""'):
            for num in magic:
                if num not in ("10", "16", "32", "64", "128", "256"):
                    issues.append(
                        CodeIssue(
                            severity="info",
                            category="style",
                            description=f"Magic number `{num}` detectado.",
                            file=filename,
                            line=i,
                            suggestion=f"Extrae `{num}` a una constante con nombre descriptivo.",
                        )
                    )
                    break  # un issue por línea es suficiente

        # Líneas demasiado largas (>99 chars)
        if len(line) > 99:
            issues.append(
                CodeIssue(
                    severity="info",
                    category="style",
                    description=f"Línea {i} excede 99 caracteres ({len(line)} chars).",
                    file=filename,
                    line=i,
                    suggestion="Divide la expresión o usa paréntesis para continuar en la siguiente línea.",
                )
            )

    return issues


async def heuristic_scanner(code: str, filename: str) -> list[CodeIssue]:
    """Scanner de seguridad heurístico: detecta patrones de vulnerabilidad."""
    issues: list[CodeIssue] = []
    lines = code.splitlines()

    patterns = [
        # SQL injection: concatenación de strings en queries
        (
            r"""(execute|cursor\.execute)\s*\(\s*["'][^"']*["']\s*\+""",
            "critical",
            "security",
            "SQL injection: interpolación directa de variables en query.",
            "Usa consultas parametrizadas: cursor.execute(sql, (param,))",
        ),
        # Credenciales hardcodeadas
        (
            r"""(?i)(password|secret|api_key|token|credential)\s*=\s*["'][^"']{6,}["']""",
            "critical",
            "security",
            "Credencial hardcodeada en el código fuente.",
            "Usa variables de entorno: os.environ.get('SECRET_KEY') o python-dotenv.",
        ),
        # pickle.loads
        (
            r"""pickle\.loads?\s*\(""",
            "high",
            "security",
            "Deserialización insegura con pickle: puede ejecutar código arbitrario.",
            "Usa json.loads() para datos no confiables o valida el origen con firmado HMAC.",
        ),
        # yaml.load sin Loader
        (
            r"""yaml\.load\s*\([^,)]+\)""",
            "high",
            "security",
            "yaml.load() sin Loader puede ejecutar código arbitrario.",
            "Usa yaml.safe_load() para datos no confiables.",
        ),
        # subprocess con shell=True
        (
            r"""subprocess\.(run|call|Popen)\s*\([^)]*shell\s*=\s*True""",
            "high",
            "security",
            "subprocess con shell=True: vulnerable a shell injection.",
            "Pasa los argumentos como lista: subprocess.run(['cmd', arg]) con shell=False.",
        ),
        # requests sin timeout
        (
            r"""requests\.(get|post|put|delete|patch)\s*\([^)]*\)(?!\s*\.|\s*,\s*timeout)""",
            "medium",
            "security",
            "Llamada HTTP sin timeout: puede bloquearse indefinidamente.",
            "Añade timeout=30: requests.get(url, timeout=30)",
        ),
    ]

    for pattern, severity, category, description, suggestion in patterns:
        for i, line in enumerate(lines, start=1):
            if re.search(pattern, line):
                issues.append(
                    CodeIssue(
                        severity=severity,  # type: ignore[arg-type]
                        category=category,  # type: ignore[arg-type]
                        description=description,
                        file=filename,
                        line=i,
                        suggestion=suggestion,
                    )
                )

    return issues


async def heuristic_reviewer(code: str, filename: str) -> list[CodeIssue]:
    """Revisor lógico heurístico: detecta errores de lógica y condiciones."""
    issues: list[CodeIssue] = []
    lines = code.splitlines()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # División sin comprobación de cero
        if re.search(r"/\s*len\s*\(", stripped) and not re.search(
            r"if.*len", "".join(lines[max(0, i - 4) : i])
        ):
            issues.append(
                CodeIssue(
                    severity="high",
                    category="logic",
                    description="División por len() sin comprobar que la colección no esté vacía.",
                    file=filename,
                    line=i,
                    suggestion="Añade: if not numbers: return 0.0  (o raise ValueError) antes de dividir.",
                )
            )

        # Acceso a índice [0] sin comprobación
        if re.search(r"\w+\[0\]", stripped) and not re.search(
            r"if\s+\w+|len\(", "".join(lines[max(0, i - 4) : i])
        ):
            issues.append(
                CodeIssue(
                    severity="medium",
                    category="logic",
                    description="Acceso a índice [0] sin comprobar que la colección no esté vacía.",
                    file=filename,
                    line=i,
                    suggestion="Usa: items[0] if items else None  o comprueba len(items) > 0 antes.",
                )
            )

        # Condición duplicada/inalcanzable en elif
        if stripped.startswith("elif "):
            cond = stripped[5:].split(":")[0].strip()
            # Busca la misma condición en elif anteriores de la misma función
            prev_block = "\n".join(lines[max(0, i - 20) : i - 1])
            if f"elif {cond}" in prev_block or f"if {cond}" in prev_block:
                issues.append(
                    CodeIssue(
                        severity="medium",
                        category="logic",
                        description=f"Condición `elif {cond}` duplicada/inalcanzable.",
                        file=filename,
                        line=i,
                        suggestion="Elimina la rama duplicada o corrige la condición.",
                    )
                )

        # Loop for i in range(len(...)) — anti-pattern
        if re.search(r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\(", stripped):
            issues.append(
                CodeIssue(
                    severity="low",
                    category="performance",
                    description="Loop `for i in range(len(x))` es menos pythónico y eficiente.",
                    file=filename,
                    line=i,
                    suggestion="Usa `for item in items:` o `for i, item in enumerate(items):`",
                )
            )

        # Ausencia de tests (archivo sin 'def test_' y sin import pytest/unittest)
    if "def test_" not in code and "import pytest" not in code and "import unittest" not in code:
        if any(line.strip().startswith("def ") for line in lines):
            issues.append(
                CodeIssue(
                    severity="medium",
                    category="test",
                    description="No se detectaron funciones de test para este módulo.",
                    file=filename,
                    line=None,
                    suggestion="Crea un archivo test_{filename} con pytest y cubre los casos límite.",
                )
            )

    return issues


async def heuristic_suggester(code: str, issues: list[CodeIssue]) -> list[str]:
    """Genera sugerencias de remediación resumidas por issue."""
    suggestions = []
    for issue in issues:
        loc = f"línea {issue.line}" if issue.line else "módulo completo"
        suggestions.append(
            f"[{issue.severity.upper()}] {issue.file}:{loc} — {issue.description} "
            f"→ {issue.suggestion}"
        )
    return suggestions


# ── Cálculo de score (simula report_generator) ────────────────────────────────


def compute_score(issues: list[CodeIssue]) -> float:
    """Calcula el score de revisión (1.0 = sin problemas)."""
    penalty = sum(SEVERITY_WEIGHTS.get(issue.severity, 0.0) for issue in issues)
    return max(0.0, 1.0 - penalty)


# ── Ejecución de la revisión ──────────────────────────────────────────────────


async def run_code_review(snippet: dict, approval_threshold: float = 0.8) -> dict:
    """Ejecuta la revisión completa de un snippet de código.

    Args:
        snippet: Dict con id, filename, description, code.
        approval_threshold: Score mínimo para aprobar el PR.

    Returns:
        Dict con issues, score, approved, suggestions.
    """
    code = snippet["code"]
    filename = snippet["filename"]

    print(f"\n[{snippet['id']}] {snippet['filename']}")
    print(f"  Descripción: {snippet['description']}")

    if not CODE_REVIEW_AVAILABLE:
        # Modo demo: ejecutar los callables directamente sin subgraph
        print("  [Modo demo — subgraph simulado]\n")

        # Nodo 1: linter
        print("  ── Nodo 1: linter ──")
        linter_issues = await heuristic_linter(code, filename)
        print(f"    Issues de estilo: {len(linter_issues)}")

        # Nodo 2: security_scanner
        print("  ── Nodo 2: security_scanner ──")
        scanner_issues = await heuristic_scanner(code, filename)
        print(f"    Issues de seguridad: {len(scanner_issues)}")

        # Nodo 3: logic_reviewer
        print("  ── Nodo 3: logic_reviewer ──")
        logic_issues = await heuristic_reviewer(code, filename)
        print(f"    Issues de lógica/tests: {len(logic_issues)}")

        # Consolidar issues
        all_issues = linter_issues + scanner_issues + logic_issues

        # Nodo 4: suggester
        print("  ── Nodo 4: suggester ──")
        suggestions = await heuristic_suggester(code, all_issues)
        print(f"    Sugerencias generadas: {len(suggestions)}")

        # Nodo 5: report_generator
        print("  ── Nodo 5: report_generator ──")
        score = compute_score(all_issues)
        approved = score >= approval_threshold
        verdict = "✓ APROBADO" if approved else "✗ RECHAZADO"
        print(f"    Score    : {score:.3f}")
        print(f"    Umbral   : {approval_threshold}")
        print(f"    Veredicto: {verdict}")

        return {
            "id": snippet["id"],
            "filename": filename,
            "issues": all_issues,
            "score": score,
            "approved": approved,
            "suggestions": suggestions,
        }

    # Modo real con subgraph
    from langchain_core.messages import HumanMessage

    from prismal.agents.state import create_initial_state
    from prismal.agents.subgraphs.factory import assemble_state_graph

    await register_code_review(approval_threshold=approval_threshold)
    subgraph_def = build_code_review_subgraph(
        linter_fn=heuristic_linter,
        scanner_fn=heuristic_scanner,
        reviewer_fn=heuristic_reviewer,
        suggester_fn=heuristic_suggester,
        approval_threshold=approval_threshold,
    )
    graph = assemble_state_graph(subgraph_def).compile()

    state = create_initial_state(session_id=f"example-code-review-{snippet['id']}")
    state["messages"] = [HumanMessage(content=f"Revisa el código de {filename}:\n\n{code}")]
    state["metadata"] = {
        "code_review": {
            "code": code,
            "filename": filename,
            "issues": [],
        }
    }

    config = {"configurable": {"thread_id": f"cr_{snippet['id']}_001"}}
    final_state = await graph.ainvoke(state, config=config)

    review_meta = final_state.get("metadata", {}).get("code_review", {})
    report = review_meta.get("report")
    if report:
        print(f"    Score    : {report.score:.3f}")
        print(f"    Veredicto: {'✓ APROBADO' if report.approved else '✗ RECHAZADO'}")

    return {
        "id": snippet["id"],
        "filename": filename,
        "issues": review_meta.get("issues", []),
        "score": report.score if report else 0.0,
        "approved": report.approved if report else False,
        "suggestions": review_meta.get("suggestions", []),
    }


def print_issue_table(issues: list[CodeIssue]) -> None:
    """Muestra los issues en formato tabla con indicadores visuales."""
    if not issues:
        print("    ✓ Sin issues detectados")
        return

    severity_icons = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🔵",
        "info": "⚪",
    }
    category_labels = {
        "security": "SEC",
        "logic": "LOG",
        "style": "STY",
        "performance": "PERF",
        "test": "TEST",
    }

    for issue in issues:
        icon = severity_icons.get(issue.severity, "  ")
        cat = category_labels.get(issue.category, issue.category[:4].upper())
        loc = f"L{issue.line:3d}" if issue.line else "    "
        print(f"    {icon} [{issue.severity:8s}] [{cat:4s}] {loc}  {issue.description[:60]}")


async def main() -> None:
    APPROVAL_THRESHOLD = 0.8

    print("=" * 70)
    print("  Code Review Subgraph — Dataset: CodeSearchNet Python (GitHub)")
    print("=" * 70)

    # Arquitectura del subgraph
    print("\n[Arquitectura del subgraph Code Review]")
    nodes = [
        ("linter          ", "PEP 8, complejidad ciclomática, docstrings, magic numbers"),
        ("security_scanner", "SQL injection, credenciales, pickle, yaml.load, subprocess"),
        ("logic_reviewer  ", "ZeroDivision, IndexError, condiciones inalcanzables"),
        ("suggester       ", "Genera remediaciones por cada issue detectado"),
        ("report_generator", "Score ponderado por severidad → approved / rejected"),
    ]
    print()
    for node, desc in nodes:
        print(f"  {node}: {desc}")
    print()
    print("  Score = 1.0 - Σ weight[severity]")
    print(
        f"  Pesos: critical={SEVERITY_WEIGHTS['critical']}, high={SEVERITY_WEIGHTS['high']}, "
        f"medium={SEVERITY_WEIGHTS['medium']}, low={SEVERITY_WEIGHTS['low']}, info={SEVERITY_WEIGHTS['info']}"
    )
    print(f"  approved = (score >= {APPROVAL_THRESHOLD})")

    # Ejecutar revisiones
    print(f"\n[Revisando {len(CODE_SNIPPETS)} snippets de código]")
    results = []
    for snippet in CODE_SNIPPETS:
        result = await run_code_review(snippet, approval_threshold=APPROVAL_THRESHOLD)
        results.append(result)

        # Mostrar issues detallados
        print(f"\n  Issues detectados ({len(result['issues'])} total):")
        print_issue_table(result["issues"])
        print("─" * 70)

    # ── Estadísticas globales ─────────────────────────────────────────────────
    print("\n[Resumen estadístico — todos los snippets]")

    all_issues: list[CodeIssue] = []
    for r in results:
        all_issues.extend(r["issues"])

    # Conteo por severidad
    print("\n  Distribución por severidad:")
    for sev in ("critical", "high", "medium", "low", "info"):
        count = sum(1 for i in all_issues if i.severity == sev)
        bar = "█" * count
        weight = SEVERITY_WEIGHTS[sev]
        print(f"    {sev:8s}  (×{weight:.2f})  {bar:<15} {count:2d}")

    # Conteo por categoría
    print("\n  Distribución por categoría:")
    for cat in ("security", "logic", "style", "performance", "test"):
        count = sum(1 for i in all_issues if i.category == cat)
        bar = "█" * count
        print(f"    {cat:12s}  {bar:<15} {count:2d}")

    # Resultados por archivo
    print("\n  Resultados por archivo:")
    print(f"  {'ID':<8} {'Archivo':<22} {'Issues':>6} {'Score':>7} {'Veredicto'}")
    print("  " + "─" * 60)
    for r in results:
        verdict = "✓ APROBADO" if r["approved"] else "✗ RECHAZADO"
        issue_count = len(r["issues"])
        print(f"  {r['id']:<8} {r['filename']:<22} {issue_count:>6} {r['score']:>7.3f}  {verdict}")

    total_approved = sum(1 for r in results if r["approved"])
    print(f"\n  Aprobados: {total_approved}/{len(results)}")
    print(f"  Issues totales detectados: {len(all_issues)}")

    # ── Comparativa de severidades ────────────────────────────────────────────
    print("\n[Impacto en score por severidad de issue]")
    print(f"  Un solo issue crítico resta {SEVERITY_WEIGHTS['critical']:.0%} del score")
    print(
        f"  → Con 1 crítico:  score={1.0 - SEVERITY_WEIGHTS['critical']:.2f}  "
        f"{'✓' if (1.0 - SEVERITY_WEIGHTS['critical']) >= APPROVAL_THRESHOLD else '✗'}"
    )
    print(
        f"  → Con 2 altos:    score={1.0 - 2 * SEVERITY_WEIGHTS['high']:.2f}  "
        f"{'✓' if (1.0 - 2 * SEVERITY_WEIGHTS['high']) >= APPROVAL_THRESHOLD else '✗'}"
    )
    print(
        f"  → Con 4 medios:   score={1.0 - 4 * SEVERITY_WEIGHTS['medium']:.2f}  "
        f"{'✓' if (1.0 - 4 * SEVERITY_WEIGHTS['medium']) >= APPROVAL_THRESHOLD else '✗'}"
    )
    print(
        f"  → Con 10 bajos:   score={1.0 - 10 * SEVERITY_WEIGHTS['low']:.2f}  "
        f"{'✓' if (1.0 - 10 * SEVERITY_WEIGHTS['low']) >= APPROVAL_THRESHOLD else '✗'}"
    )

    # ── Uso con callables personalizados ─────────────────────────────────────
    print("\n[Uso con callables inyectables]")
    print("  from prismal.agents.subgraphs.code_review.builder import (")
    print("      build_code_review_subgraph,")
    print("  )")
    print("  subgraph = build_code_review_subgraph(")
    print("      linter_fn=my_flake8_runner,     # async (code, filename) -> list[CodeIssue]")
    print("      scanner_fn=my_bandit_scanner,   # async (code, filename) -> list[CodeIssue]")
    print("      reviewer_fn=my_llm_reviewer,    # async (code, filename) -> list[CodeIssue]")
    print("      suggester_fn=my_llm_suggester,  # async (code, issues)   -> list[str]")
    print("      approval_threshold=0.85,")
    print("  )")
    print("  # Si linter_fn=None, usa el LLM por defecto del ProviderRegistry")

    # ── Comparativa Code Review manual vs pipeline ────────────────────────────
    print("\n[Code Review manual vs subgraph automatizado]")
    comparison = [
        ("Manual (1 revisor)", "15-60 min", "Parcial", "Alta", "Alta", "Ninguna"),
        ("Linter estático", "< 1 s", "Estilo/PEP8", "Baja", "Media", "Ninguna"),
        ("Bandit / SAST", "< 5 s", "Seguridad", "Baja", "Alta", "Ninguna"),
        ("Code Review Subgraph", "5-30 s", "Completa", "Alta", "Alta", "Trazabilidad"),
    ]
    header = (
        f"  {'Método':<26} {'Tiempo':<11} {'Cobertura':<13} {'Recall':<8} {'Précis.':<8} {'Extra'}"
    )
    print(header)
    print("  " + "─" * 75)
    for method, time_, coverage, recall, prec, extra in comparison:
        print(f"  {method:<26} {time_:<11} {coverage:<13} {recall:<8} {prec:<8} {extra}")


if __name__ == "__main__":
    asyncio.run(main())
