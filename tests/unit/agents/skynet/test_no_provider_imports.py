"""Architecture test: Skynet modules must not import providers at module level.

DD-SKY-006 / RF-SKY-11: every Skynet component is callable-injected so the
whole layer runs without an LLM backend.  Provider wiring is allowed only as a
*lazy* (function-level) import in the default backends — a module-level
``import prismal.providers...`` would defeat that and load provider machinery
on package import.

Also enforces that no Skynet module imports ``prismal.mcp`` / ``prismal.skills``
at any nesting level (critical rule 9; tools reach the worker only through the
injected ``ToolProviderPort``).  Mirrors the Kokoro guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal.agents.skynet
import prismal.agents.subgraphs.skynet

_PACKAGES = (
    Path(prismal.agents.skynet.__file__).parent,
    Path(prismal.agents.subgraphs.skynet.__file__).parent,
)

_PROVIDER_PREFIX = "prismal.providers"
_FORBIDDEN_ANYWHERE = ("prismal.mcp", "prismal.skills")


def _module_files() -> list[Path]:
    files: list[Path] = []
    for package in _PACKAGES:
        files.extend(sorted(package.rglob("*.py")))
    return files


def _module_level_imports(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:  # module level only — function-level lazy imports allowed
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _all_imports(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_no_module_level_provider_imports() -> None:
    """No Skynet module imports prismal.providers at module level."""
    offenders: list[str] = []
    for file in _module_files():
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for name in _module_level_imports(tree):
            if name.startswith(_PROVIDER_PREFIX):
                offenders.append(f"{file}: {name}")
    assert not offenders, f"Module-level provider imports found: {offenders}"


def test_no_mcp_or_skills_imports_anywhere() -> None:
    """No Skynet module imports prismal.mcp / prismal.skills at any level."""
    offenders: list[str] = []
    for file in _module_files():
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for name in _all_imports(tree):
            if name.startswith(_FORBIDDEN_ANYWHERE):
                offenders.append(f"{file}: {name}")
    assert not offenders, f"Forbidden mcp/skills imports found: {offenders}"
