"""Unpatched dependency advisories stay unreachable from prismal code.

chromadb 1.5.9 and nltk 3.10.3 are the latest releases and are still affected
(no upstream fix, OSV ``last_affected``). The Dependabot alerts are dismissed
as tolerable risk *because* prismal never reaches the vulnerable surface; this
guard fails if a change would make that justification false:

- chromadb GHSA-xph7-9rjv-w5fr / GHSA-2wm9-hf6c-p5cr / GHSA-36p7-vc44-83pf
  (CVE-2026-45830/45831/45833) are bugs of the Chroma *server* (RBAC,
  multi-tenant authz, ``trust_remote_code`` model repo). prismal must only use
  embedded Chroma — no HTTP client, no server settings.
- nltk GHSA-8mgp-746c-j5xp (CVE-2026-81726) is a path sandbox bypass in model
  artifact APIs. nltk only arrives through ``llmlingua`` (``[compression]``);
  prismal must not import it.

Re-evaluate (and drop the ignores) as soon as either project ships a fix.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "prismal"

# Names that would connect to (or configure) a Chroma server.
_CHROMA_SERVER_NAMES = frozenset(
    {
        "HttpClient",
        "AsyncHttpClient",
        "CloudClient",
        "client_settings",
        "chroma_server_host",
        "chroma_server_http_port",
        "chroma_client_auth_provider",
    }
)


def _modules() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(ROOT.rglob("*.py"))]


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _identifiers(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.split(".")[-1])
    return names


def test_prismal_never_imports_nltk() -> None:
    offenders = [
        str(p.relative_to(ROOT)) for p, tree in _modules() if "nltk" in _imported_roots(tree)
    ]
    assert offenders == []


def test_chroma_is_only_used_embedded() -> None:
    offenders = {
        str(p.relative_to(ROOT)): sorted(hits)
        for p, tree in _modules()
        if (hits := _identifiers(tree) & _CHROMA_SERVER_NAMES)
    }
    assert offenders == {}


def test_the_chroma_adapter_persists_to_a_local_directory() -> None:
    tree = ast.parse((ROOT / "rag" / "stores" / "chroma.py").read_text(encoding="utf-8"))
    chroma_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Chroma"
    ]
    assert chroma_calls, "the adapter no longer builds Chroma(...): re-check the advisories"
    for call in chroma_calls:
        assert "persist_directory" in {kw.arg for kw in call.keywords}
