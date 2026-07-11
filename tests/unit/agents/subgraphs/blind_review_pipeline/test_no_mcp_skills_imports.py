"""Architecture test: the blind_review_pipeline package must not import mcp/skills (BRP6-04).

Extends the coverage of ``tests/unit/agents/extension/test_no_mcp_skills_imports.py``
with a package-scoped guard, so a regression is attributed to BRP directly. Tools
reach the pipeline only through the injected ``ToolProviderPort`` (Fase Y).
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal.agents.subgraphs.blind_review_pipeline as _pkg

_FORBIDDEN_PREFIXES = ("prismal.mcp", "prismal.skills")


def _forbidden_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                alias.name for alias in node.names if alias.name.startswith(_FORBIDDEN_PREFIXES)
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(_FORBIDDEN_PREFIXES)
        ):
            found.append(node.module)
    return found


def test_blind_review_pipeline_does_not_import_mcp_or_skills() -> None:
    pkg_root = Path(next(iter(_pkg.__path__)))
    offenders: dict[str, list[str]] = {}

    py_files = sorted(pkg_root.rglob("*.py"))
    assert py_files, "expected the blind_review_pipeline package to contain modules"

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if found := _forbidden_imports(tree):
            offenders[py_file.name] = found

    assert not offenders, (
        "agents/subgraphs/blind_review_pipeline/** must not import "
        f"prismal.mcp / prismal.skills (Fase Y). Offenders: {offenders}"
    )
