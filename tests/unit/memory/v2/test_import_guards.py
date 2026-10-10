"""AST guards for agentic memory (Phase MEM — SPEC-MEM-DEL-001, MEM1-09, MEM6-08).

- Only ``prismal/memory/v2/forget.py`` may *call* ``.purge(`` in ``prismal/**``
  (adapter *definitions* of ``purge`` are fine).
- Graph SDKs (graphiti / neo4j / kuzu / falkordb) are imported only under
  ``prismal/memory/v2/graph/``.
- No provider SDK imports anywhere in ``prismal/memory/v2/`` (Rule #4).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "prismal"
MEMORY_V2 = PKG / "memory" / "v2"
FORGET = MEMORY_V2 / "forget.py"
GRAPH_DIR = MEMORY_V2 / "graph"

GRAPH_SDKS = frozenset({"graphiti_core", "graphiti", "neo4j", "kuzu", "falkordb"})
PROVIDER_SDKS = frozenset(
    {
        "anthropic",
        "openai",
        "google",
        "ollama",
        "litellm",
        "mistralai",
        "cohere",
        "groq",
        "langchain_openai",
        "langchain_anthropic",
        "langchain_google_genai",
        "langchain_ollama",
        "whisper",
        "elevenlabs",
        "pyttsx3",
        "open_clip",
    }
)


def purge_calls(source: str) -> list[int]:
    """Line numbers of every ``<expr>.purge(...)`` call in ``source``."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "purge"
    ]


def imported_roots(source: str) -> set[str]:
    """Top-level module names imported by ``source`` (absolute imports only)."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


# ── the checkers themselves catch violations ────────────────────────────────


def test_purge_call_detector_flags_calls_not_definitions() -> None:
    src = (
        "class B:\n"
        "    async def purge(self, ids):\n"
        "        return 0\n"
        "async def f(b):\n"
        "    await b.purge(['x'])\n"
        "    b.purge_nodes(['x'])\n"
    )
    assert purge_calls(src) == [5]


def test_import_detector_finds_sdk_roots() -> None:
    src = (
        "import neo4j.graph\nfrom openai import OpenAI\nfrom . import x\nfrom prismal.a import b\n"
    )
    assert imported_roots(src) == {"neo4j", "openai", "prismal"}


# ── repository guards ────────────────────────────────────────────────────────


def test_only_forget_policy_calls_purge() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in _py_files(PKG)
        if path != FORGET
        for line in purge_calls(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f".purge( called outside ForgetPolicy: {offenders}"


def test_forget_policy_does_call_purge() -> None:
    assert purge_calls(FORGET.read_text(encoding="utf-8")), "guard would be vacuous"


def test_graph_sdks_only_under_memory_v2_graph() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}: {sorted(hits)}"
        for path in _py_files(PKG)
        if not path.is_relative_to(GRAPH_DIR)
        and (hits := imported_roots(path.read_text(encoding="utf-8")) & GRAPH_SDKS)
    ]
    assert offenders == []


@pytest.mark.parametrize("path", _py_files(MEMORY_V2), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_provider_sdk_in_memory_v2(path: Path) -> None:
    hits = imported_roots(path.read_text(encoding="utf-8")) & PROVIDER_SDKS
    assert not hits, f"provider SDK import in {path.relative_to(ROOT)}: {sorted(hits)}"
