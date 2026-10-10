"""AST guard: the AG-UI translator never imports an HTTP library (DD-PRT-011, SPEC-PRT-GRD-001).

The translator is pure; the host (``prismal-server``) owns the SSE / WebSocket wire.

Pre-existing exemption: ``prismal/events/webhook_dispatcher.py`` predates Phase PRT and posts
webhooks with ``httpx`` by design. It is deliberately **not** scanned, and
``prismal/events/__init__.py`` must not import it eagerly (it does not today).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_EVENTS = Path(__file__).resolve().parents[3] / "prismal" / "events"
_GUARDED = ("agui.py", "__init__.py")
_EXEMPT = frozenset({"webhook_dispatcher.py"})
_FORBIDDEN = ("httpx", "aiohttp", "requests", "urllib", "http.client", "http", "websockets")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
    return found


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in _FORBIDDEN)


@pytest.mark.parametrize("filename", _GUARDED)
def test_guarded_module_imports_no_http_library(filename: str) -> None:
    offenders = [m for m in _imported_modules(_EVENTS / filename) if _is_forbidden(m)]
    assert offenders == [], f"{filename} imports HTTP libraries: {offenders}"


def test_init_does_not_import_webhook_dispatcher_eagerly() -> None:
    modules = _imported_modules(_EVENTS / "__init__.py")
    assert not any(m.endswith("webhook_dispatcher") for m in modules)


def test_guard_detects_offenders(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import httpx\nfrom urllib.parse import urlparse\nimport json\n")
    assert [m for m in _imported_modules(bad) if _is_forbidden(m)] == ["httpx", "urllib.parse"]


def test_exemption_is_the_only_http_user_in_events() -> None:
    users = {
        p.name for p in _EVENTS.glob("*.py") if any(_is_forbidden(m) for m in _imported_modules(p))
    }
    assert users <= _EXEMPT
