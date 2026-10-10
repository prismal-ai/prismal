"""Import-graph guards for typed decisions (Phase DEC — SPEC-DEC-SEC-002, DEC2-05, DEC6-03).

* ``laya`` / ``typesafe_sdk`` are imported only under ``prismal/decisions/adapters/``
  and only inside functions (deferred, extras ``[laya]`` / ``[jev]``).
* ``prismal/decisions`` imports no LLM-provider SDK (Rule #4).
* Security, budget, scheduler and identity policy code never import
  ``prismal.decisions`` (the only exemption is ``security/decision_bridge.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3] / "prismal"
_DECISION_SDKS = {"laya", "typesafe_sdk"}
_PROVIDER_SDKS = {
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


def _imports(path: Path) -> list[tuple[str, bool]]:
    """``(module, at_module_level)`` for every absolute import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = {id(n) for n in tree.body}
    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((a.name, id(node) in top_level) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.module, id(node) in top_level))
    return out


def test_decision_sdks_only_in_adapters_and_deferred() -> None:
    offenders: list[str] = []
    adapters = _ROOT / "decisions" / "adapters"
    for path in _ROOT.rglob("*.py"):
        for module, top in _imports(path):
            if module.split(".")[0] not in _DECISION_SDKS:
                continue
            if not path.is_relative_to(adapters) or top:
                offenders.append(f"{path.relative_to(_ROOT)}: {module} (top={top})")
    assert not offenders, offenders


def test_decisions_import_no_provider_sdk() -> None:
    offenders = {
        str(p.relative_to(_ROOT)): m
        for p in (_ROOT / "decisions").rglob("*.py")
        for m, _ in _imports(p)
        if m.split(".")[0] in _PROVIDER_SDKS
    }
    assert not offenders, offenders


def test_forbidden_layers_do_not_import_decisions() -> None:
    guarded = [
        *(_ROOT / "security").rglob("*.py"),
        *(_ROOT / "budget").rglob("*.py"),
        *(_ROOT / "scheduler").rglob("*.py"),
        _ROOT / "identity" / "policy.py",
    ]
    exempt = {_ROOT / "security" / "decision_bridge.py"}
    offenders = [
        f"{p.relative_to(_ROOT)}: {m}"
        for p in guarded
        if p not in exempt and p.exists()
        for m, _ in _imports(p)
        if m == "prismal.decisions" or m.startswith("prismal.decisions.")
    ]
    assert not offenders, offenders


def test_guard_detects_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_text("import laya\n\ndef f():\n    import typesafe_sdk\n")
    assert _imports(sample) == [("laya", True), ("typesafe_sdk", False)]
