"""Guard: prismal/security imports no LLM-provider SDK (Rule #4 — GRD3-09).

Rule #4 — provider-specific SDKs (anthropic, openai, google.generativeai,
ollama, litellm, whisper, elevenlabs, ...) must live only under
``prismal/providers/``. ``nemoguardrails`` and ``guardrails`` (guardrails-ai)
are deliberate exceptions — guardrails *orchestration* SDKs, not LLM provider
clients (DD-GRD-004) — but even they must be lazily/deferred-imported (inside
a function body, never at module top level) so the base install never
requires the optional ``[guardrails-ai]`` extra.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SECURITY_DIR = Path(__file__).resolve().parents[3] / "prismal" / "security"
_FORBIDDEN = {
    "anthropic",
    "openai",
    "google",
    "ollama",
    "litellm",
    "whisper",
    "elevenlabs",
    "open_clip",
    "pyttsx3",
}
_GUARDRAILS_SDKS = {"nemoguardrails", "guardrails"}


def _imported_roots(node: ast.AST) -> set[str]:
    roots: set[str] = set()
    if isinstance(node, ast.Import):
        roots.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        roots.add(node.module.split(".")[0])
    return roots


def test_security_package_imports_no_provider_sdk() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _SECURITY_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            roots |= _imported_roots(node)
        bad = roots & _FORBIDDEN
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"provider SDK imported under prismal/security: {offenders}"


def test_guardrails_sdks_are_never_imported_at_module_top_level() -> None:
    """nemoguardrails/guardrails imports must be deferred (inside a function body)."""
    offenders: dict[str, set[str]] = {}
    for path in _SECURITY_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level_roots: set[str] = set()
        for node in tree.body:  # only direct module-level statements
            top_level_roots |= _imported_roots(node)
        bad = top_level_roots & _GUARDRAILS_SDKS
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"nemoguardrails/guardrails imported at module top level: {offenders}"
