"""Only ``prismal/scheduler/`` may touch ``CronExecutor``'s private APScheduler.

Callers schedule through the public API (``CronExecutor.add_callable``); a
``._scheduler`` attribute access or a ``getattr(x, "_scheduler")`` elsewhere in
``prismal/**`` couples them to the executor's internals.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[3] / "prismal"
SCHEDULER = PKG / "scheduler"


def _offences(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "_scheduler"
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr", "setattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "_scheduler"
            )
        ):
            lines.append(node.lineno)
    return lines


def test_no_private_scheduler_access_outside_scheduler_package() -> None:
    offenders = {
        str(path.relative_to(PKG)): lines
        for path in PKG.rglob("*.py")
        if not path.is_relative_to(SCHEDULER) and (lines := _offences(path))
    }
    assert offenders == {}
