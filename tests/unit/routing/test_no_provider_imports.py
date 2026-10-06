"""Import-graph guards for adaptive routing (Phase AMR — SPEC-AMR-INT-001, AMR6-09).

* ``prismal/routing`` imports no LLM-provider SDK and no ``httpx`` (Rule #4):
  provider batch APIs are reached only through ``providers/batch_adapters.py``.
* Policies are pure: ``routing/policies`` never imports ``prismal.providers``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3] / "prismal"
_PROVIDER_SDKS = {
    "anthropic",
    "openai",
    "google",
    "ollama",
    "litellm",
    "httpx",
    "langchain_litellm",
    "whisper",
    "elevenlabs",
    "open_clip",
    "pyttsx3",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def test_routing_imports_no_provider_sdk() -> None:
    offenders = [
        f"{p.relative_to(_ROOT)}: {m}"
        for p in (_ROOT / "routing").rglob("*.py")
        for m in _imports(p)
        if m.split(".")[0] in _PROVIDER_SDKS
    ]
    assert not offenders, offenders


def test_policies_never_import_providers() -> None:
    offenders = [
        f"{p.relative_to(_ROOT)}: {m}"
        for p in (_ROOT / "routing" / "policies").rglob("*.py")
        for m in _imports(p)
        if m == "prismal.providers" or m.startswith("prismal.providers.")
        if m != "prismal.providers.cost"  # pricing only (batch assignment price_fn)
    ]
    assert not offenders, offenders


def test_guard_detects_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_text("import litellm\n\ndef f():\n    from httpx import AsyncClient\n")
    assert _imports(sample) == ["litellm", "httpx"]
