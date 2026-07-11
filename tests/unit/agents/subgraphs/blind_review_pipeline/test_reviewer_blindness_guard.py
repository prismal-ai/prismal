"""Architecture test: reviewer_node.py must never read state["messages"] (Phase BRP3-04).

This is the load-bearing proof of RF-BRP-04 (blindness). It AST-walks
``reviewer_node.py`` and fails CI if any ``state["messages"]`` subscript or
``state.get("messages")`` call appears anywhere in the module — mirroring
``tests/unit/agents/extension/test_no_mcp_skills_imports.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal.agents.subgraphs.blind_review_pipeline as _pkg

_MODULE = Path(next(iter(_pkg.__path__))) / "reviewer_node.py"


def _reads_messages(tree: ast.AST) -> list[str]:
    """Return offending source snippets that read a ``messages`` field off ``state``."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        # state["messages"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "state"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "messages"
        ):
            offenders.append('state["messages"]')
        # state.get("messages")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "state"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "messages"
        ):
            offenders.append('state.get("messages")')
    return offenders


def test_reviewer_module_never_reads_messages() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_MODULE))

    offenders = _reads_messages(tree)

    assert not offenders, (
        "reviewer_node.py must never read state['messages'] (RF-BRP-04 blindness). "
        f"Offenders: {offenders}"
    )
