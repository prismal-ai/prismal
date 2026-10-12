"""Guard: prismal never reaches the vulnerable surface of unpatched deps.

chromadb 1.5.9 (GHSA-xph7-9rjv-w5fr / GHSA-2wm9-hf6c-p5cr / GHSA-36p7-vc44-83pf,
CVE-2026-45829/45830/45831/45833) and nltk 3.10.3 (GHSA-8mgp-746c-j5xp,
CVE-2026-81726) have no upstream fix. Their ignores in ``.trivyignore`` /
``ci.yml`` / ``.pre-commit-config.yaml`` rest on two surface claims that this
module turns into enforced invariants:

* ChromaDB is used **embedded only** (``Chroma(persist_directory=...)``): no
  ``HttpClient`` / ``AsyncHttpClient`` / ``CloudClient``, no Chroma server
  modules, no ``chroma run`` subprocess, no ``client=`` / ``host=`` on a
  ``Chroma(...)`` call. Every advisory above is a Chroma *server* flaw.
* prismal calls none of the nltk model-artifact APIs that bypass ``pathsec``
  (``TransitionParser``, ``AveragedPerceptron``, ``PerceptronTagger``
  ``save_to_json``, ``save_maxent_params``); nltk only arrives transitively
  via llmlingua (``[compression]`` extra).

If one of these tests fails, the corresponding ignore is no longer justified:
re-triage the advisory instead of extending an allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_PRISMAL_DIR = Path(__file__).resolve().parents[3] / "prismal"

_CHROMA_SERVER_NAMES = frozenset({"HttpClient", "AsyncHttpClient", "CloudClient"})
_CHROMA_SERVER_MODULES = ("chromadb.server", "chromadb.app", "chromadb.cli")
_CHROMA_REMOTE_KWARGS = frozenset({"client", "host"})

_NLTK_VULNERABLE_NAMES = frozenset(
    {"TransitionParser", "AveragedPerceptron", "PerceptronTagger", "save_maxent_params"}
)
_NLTK_VULNERABLE_MODULES = ("nltk.parse.transitionparser", "nltk.tag.perceptron")


def _matches_module(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def chroma_violations(source: str) -> list[str]:
    """Return the remote/server ChromaDB usages found in *source*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                f"import {a.name}"
                for a in node.names
                if _matches_module(a.name, _CHROMA_SERVER_MODULES)
            ]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _matches_module(node.module, _CHROMA_SERVER_MODULES):
                found.append(f"from {node.module} import ...")
            elif node.module.split(".")[0] == "chromadb":
                found += [
                    f"from {node.module} import {a.name}"
                    for a in node.names
                    if a.name in _CHROMA_SERVER_NAMES
                ]
        elif isinstance(node, ast.Attribute) and node.attr in _CHROMA_SERVER_NAMES:
            if isinstance(node.value, ast.Name) and node.value.id == "chromadb":
                found.append(f"chromadb.{node.attr}")
        elif isinstance(node, ast.Call) and _call_name(node.func) == "Chroma":
            found += [
                f"Chroma({kw.arg}=...)" for kw in node.keywords if kw.arg in _CHROMA_REMOTE_KWARGS
            ]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "chroma run" in node.value:
                found.append("'chroma run' literal")
        elif isinstance(node, (ast.List, ast.Tuple)):
            words = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if any(a == "chroma" and b == "run" for a, b in zip(words, words[1:], strict=False)):
                found.append("['chroma', 'run'] argv")
    return found


def nltk_violations(source: str) -> list[str]:
    """Return the references to nltk's pathsec-bypassing model APIs in *source*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                f"import {a.name}"
                for a in node.names
                if _matches_module(a.name, _NLTK_VULNERABLE_MODULES)
            ]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] != "nltk":
                continue
            if _matches_module(node.module, _NLTK_VULNERABLE_MODULES):
                found.append(f"from {node.module} import ...")
            found += [
                f"from {node.module} import {a.name}"
                for a in node.names
                if a.name in _NLTK_VULNERABLE_NAMES
            ]
        elif isinstance(node, ast.Attribute) and node.attr in _NLTK_VULNERABLE_NAMES:
            found.append(f"<expr>.{node.attr}")
    return found


def _scan_tree(scanner: Any) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _PRISMAL_DIR.rglob("*.py"):
        hits = scanner(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(_PRISMAL_DIR))] = hits
    return offenders


# ── Scanner self-tests (prove the guards can fail) ──────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import chromadb\nc = chromadb.HttpClient(host='x')",
        "from chromadb import AsyncHttpClient",
        "from chromadb import CloudClient as C",
        "import chromadb.server.fastapi",
        "from chromadb.app import app",
        "from langchain_community.vectorstores import Chroma\nChroma(client=c)",
        "vs.Chroma(collection_name='a', host='db')",
        "os.system('chroma run --path /data')",
        "subprocess.run(['chroma', 'run', '--path', '/data'])",
    ],
)
def test_chroma_scanner_flags_remote_usage(source: str) -> None:
    assert chroma_violations(source)


def test_chroma_scanner_accepts_embedded_usage() -> None:
    source = (
        "from langchain_community.vectorstores import Chroma\n"
        "Chroma(collection_name='a', embedding_function=e, persist_directory='/p')\n"
        "import chromadb\nchromadb.PersistentClient(path='/p')\n"
    )
    assert chroma_violations(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from nltk.parse.transitionparser import TransitionParser",
        "import nltk.tag.perceptron",
        "from nltk.tag import PerceptronTagger",
        "from nltk.classify.maxent import save_maxent_params",
        "import nltk\nnltk.parse.TransitionParser('arc-eager').train(d, '/tmp/m')",
        "tagger.model.AveragedPerceptron().load(path)",
    ],
)
def test_nltk_scanner_flags_vulnerable_apis(source: str) -> None:
    assert nltk_violations(source)


def test_nltk_scanner_accepts_tokenizer_usage() -> None:
    source = "import nltk\nfrom nltk.tokenize import sent_tokenize\nnltk.word_tokenize(t)\n"
    assert nltk_violations(source) == []


# ── The invariants over prismal/** ──────────────────────────────────────────


def test_prismal_uses_chromadb_embedded_only() -> None:
    offenders = _scan_tree(chroma_violations)
    assert not offenders, f"remote/server ChromaDB surface used: {offenders}"


def test_prismal_never_calls_nltk_pathsec_bypassing_apis() -> None:
    offenders = _scan_tree(nltk_violations)
    assert not offenders, f"vulnerable nltk model-artifact API used: {offenders}"


def test_chroma_adapter_builds_an_embedded_persistent_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runtime check of the same invariant on the only Chroma constructor."""
    from prismal.rag.stores import chroma as chroma_mod

    captured: dict[str, Any] = {}

    class _RecordingChroma:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(chroma_mod, "Chroma", _RecordingChroma)
    monkeypatch.setattr(chroma_mod.EmbeddingsFactory, "create", lambda settings=None: object())

    settings = SimpleNamespace(chroma_path=str(tmp_path / "chroma"))
    chroma_mod.ChromaVectorStore(collection_name="c", settings=settings)  # type: ignore[arg-type]

    assert captured["persist_directory"] == settings.chroma_path
    assert not (_CHROMA_REMOTE_KWARGS | {"client_settings"}) & captured.keys()
