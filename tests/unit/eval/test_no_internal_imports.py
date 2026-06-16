"""Architecture test: the eval harness imports only public entry + ports (V6-03).

``prismal/eval/**`` is a sibling of the runtime — it must depend only on the
public graph entry point and the public ports, never on ``prismal.agents.*``
internals (supervisor, state, subgraphs, …) and never on ``prismal.mcp`` /
``prismal.skills``. This AST-walks every eval module (deferred imports included).

Allowed ``prismal.agents.*`` surfaces:

- ``prismal.agents.graph`` — the public graph entry (``get_async_compiled_graph``).
- ``prismal.agents.extension`` — the public extension API + ports.
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal.eval

_FORBIDDEN_EXACT = ("prismal.mcp", "prismal.skills")
_ALLOWED_AGENTS = ("prismal.agents.graph", "prismal.agents.extension")


def _is_forbidden(module: str) -> bool:
    if module.startswith(_FORBIDDEN_EXACT):
        return True
    if module.startswith("prismal.agents"):
        return not module.startswith(_ALLOWED_AGENTS)
    return False


def _forbidden_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if _is_forbidden(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module and _is_forbidden(node.module):
            found.append(node.module)
    return found


def test_eval_imports_only_public_entry_and_ports() -> None:
    eval_root = Path(next(iter(prismal.eval.__path__)))
    offenders: dict[str, list[str]] = {}

    for py_file in sorted(eval_root.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if found := _forbidden_imports(tree):
            offenders[str(py_file.relative_to(eval_root))] = found

    assert not offenders, (
        "prismal/eval/** must import only the public graph entry "
        "(prismal.agents.graph / prismal.agents.extension) and ports — never "
        f"agents internals, mcp, or skills (V6-03). Offenders: {offenders}"
    )
