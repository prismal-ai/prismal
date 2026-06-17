"""Guard: prismal/identity imports no LLM-provider SDK (Phase IDN — ID7-07).

Rule #4 — provider-specific SDKs (anthropic, openai, google.generativeai,
ollama, whisper, …) must live only under ``prismal/providers/``. The identity
layer uses only general-purpose crypto/HTTP libraries (cryptography, httpx),
never a model-provider SDK.
"""

from __future__ import annotations

import ast
from pathlib import Path

_IDENTITY_DIR = Path(__file__).resolve().parents[3] / "prismal" / "identity"
_FORBIDDEN = {
    "anthropic",
    "openai",
    "google",
    "google.generativeai",
    "ollama",
    "litellm",
    "whisper",
    "elevenlabs",
    "open_clip",
    "pyttsx3",
}


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_identity_package_imports_no_provider_sdk() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _IDENTITY_DIR.glob("*.py"):
        roots = _imported_roots(path.read_text(encoding="utf-8"))
        bad = roots & _FORBIDDEN
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"provider SDK imported under prismal/identity: {offenders}"
