"""Architecture test (Phase W): the core must not read config from ``os.environ``.

Phase W (``config-source-injection``) inverts configuration into a hexagonal
port. The core consumes an injected
:class:`~prismal.core.config_source.ConfigSourcePort`; it no longer reads
``PRISMAL_*`` (or the well-known unprefixed provider keys) directly from
``os.environ`` / ``os.getenv``. This test AST-walks every module under
``prismal/**`` and fails on any **literal** config-key read
(``os.getenv("PRISMAL_…")``, ``os.environ.get("ANTHROPIC_API_KEY")``,
``os.environ["PRISMAL_…"]``), at any nesting level.

Dynamic reads (``os.environ.get(name)`` where the key is a variable — e.g.
``mcp/connection.py::resolve_secret`` or the sandbox host-env forwarding) are
NOT config-key reads and are not flagged.

Allowed exemptions (mirrors Phase Y's ``test_no_mcp_skills_imports.py``):

- ``core/config_source.py`` — ``EnvConfigSource`` is the one sanctioned reader
  of ``os.environ`` / ``.env`` (DD-CSI-003).
- ``providers/registry.py`` — the single LiteLLM ``os.environ.setdefault``
  write-bridge, fed only from injected ``Settings`` (DD-CSI-005).
- ``skills/`` — built-in skills are plugins, not core config; out of scope
  (PLAN §5.2). They receive secrets via skill/tool context in a future phase.
- ``mcp/servers/`` — standalone MCP server subprocesses (``python -m``); they
  run out-of-process and legitimately read their own environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import prismal

_CONFIG_KEY_PREFIX = "PRISMAL_"
_UNPREFIXED_CONFIG_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    }
)

_EXEMPT_FILES = {
    Path("core") / "config_source.py",
    Path("providers") / "registry.py",
}
_EXEMPT_DIRS = (
    Path("skills"),
    Path("mcp") / "servers",
)


def _is_config_key(value: object) -> bool:
    return isinstance(value, str) and (
        value.startswith(_CONFIG_KEY_PREFIX) or value in _UNPREFIXED_CONFIG_KEYS
    )


def _literal_config_key_reads(tree: ast.AST) -> list[str]:
    """Return literal config keys read via os.getenv / os.environ.get / os.environ[...]."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        # os.getenv("KEY") / os.environ.get("KEY")
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            is_getenv = isinstance(func, ast.Attribute) and func.attr == "getenv"
            is_environ_get = (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
            )
            if (
                (is_getenv or is_environ_get)
                and isinstance(node.args[0], ast.Constant)
                and _is_config_key(node.args[0].value)
            ):
                offenders.append(str(node.args[0].value))
        # os.environ["KEY"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and _is_config_key(node.slice.value)
        ):
            offenders.append(str(node.slice.value))
    return offenders


def _is_exempt(relative: Path) -> bool:
    if relative in _EXEMPT_FILES:
        return True
    return any(relative == d or d in relative.parents for d in _EXEMPT_DIRS)


def test_core_does_not_read_config_from_os_environ() -> None:
    root = Path(next(iter(prismal.__path__)))
    offenders: dict[str, list[str]] = {}

    for py_file in sorted(root.rglob("*.py")):
        relative = py_file.relative_to(root)
        if _is_exempt(relative):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if found := _literal_config_key_reads(tree):
            offenders[str(relative)] = found

    assert not offenders, (
        "Direct config os.environ reads found in the core (Phase W forbids them; "
        "read from Settings / the injected ConfigSourcePort instead):\n"
        + "\n".join(f"  {mod}: {keys}" for mod, keys in offenders.items())
    )
