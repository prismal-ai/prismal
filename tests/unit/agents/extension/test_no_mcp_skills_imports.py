"""Architecture test: the agent core must not import prismal.mcp / prismal.skills.

Fase Y (RF-TPI-008) inverts the dependency between the agent layer and the
MCP/Skills integration subsystems. This test AST-walks every module under
``prismal/agents/**`` and fails on any ``import prismal.mcp`` /
``import prismal.skills`` (or ``from prismal.mcp/skills import …``), at any
nesting level — deferred imports inside functions count too.

Allowed exemptions (host-facing integration code, DD-TPI-003):

- ``extension/providers.py`` — the adapters that *wrap* MCP/Skills; they are
  what the host composes and injects.
- ``skill_manager.py`` — the skill-administration agent; its purpose is to
  manage the skills subsystem itself (install/activate/deactivate), not to
  resolve tools. Out of Fase Y scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal.agents

_FORBIDDEN_PREFIXES = ("prismal.mcp", "prismal.skills")

_EXEMPT = {
    Path("extension") / "providers.py",
    Path("skill_manager.py"),
}


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


def test_agents_core_does_not_import_mcp_or_skills() -> None:
    agents_root = Path(next(iter(prismal.agents.__path__)))
    offenders: dict[str, list[str]] = {}

    for py_file in sorted(agents_root.rglob("*.py")):
        relative = py_file.relative_to(agents_root)
        if relative in _EXEMPT:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if found := _forbidden_imports(tree):
            offenders[str(relative)] = found

    assert not offenders, (
        "prismal/agents/** must not import prismal.mcp / prismal.skills "
        f"(RF-TPI-008). Offenders: {offenders}"
    )
