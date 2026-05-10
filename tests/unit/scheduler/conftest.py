"""Shared fixtures and import guards for scheduler unit tests.

APScheduler is only available inside the project venv. When the test runner
uses the system Python (e.g. Anaconda) the ``apscheduler`` import chain
inside ``lightagent.scheduler.__init__`` would otherwise abort collection.
Injecting lightweight stubs into ``sys.modules`` before any test module is
imported prevents that failure while keeping the scheduler tests independent
of APScheduler internals.
"""

from __future__ import annotations

import importlib.util
import sys
from unittest.mock import MagicMock


def _package_installed(name: str) -> bool:
    """True if ``name`` is importable in the current environment.

    Plain ``name in sys.modules`` is wrong here: a package only enters
    sys.modules once something imports it, so at conftest-load time prefect
    and apscheduler are *installed* but absent from sys.modules. The old
    guard fell into the stub branch and tests received MagicMocks.

    ``find_spec`` is the right check — but it can raise
    ``ValueError: <name>.__spec__ is not set`` when another conftest in the
    same xdist worker has already stubbed the module. In that case, fall
    back to sys.modules: anything present (real or stub) means "don't
    re-stub".
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return name in sys.modules


# ── APScheduler stub ──────────────────────────────────────────────────────────
# Only inject when the real package is not installed.
if not _package_installed("apscheduler"):
    _aps = MagicMock()
    sys.modules["apscheduler"] = _aps
    sys.modules["apscheduler.schedulers"] = MagicMock()
    sys.modules["apscheduler.schedulers.asyncio"] = MagicMock()
    sys.modules["apscheduler.triggers"] = MagicMock()
    sys.modules["apscheduler.triggers.cron"] = MagicMock()
    sys.modules["apscheduler.triggers.date"] = MagicMock()
    sys.modules["apscheduler.jobstores"] = MagicMock()
    sys.modules["apscheduler.jobstores.base"] = MagicMock()
    sys.modules["apscheduler.jobstores.base"].JobLookupError = Exception

# ── Prefect stub ──────────────────────────────────────────────────────────────
if not _package_installed("prefect"):
    sys.modules["prefect"] = MagicMock()
    sys.modules["prefect.deployments"] = MagicMock()

# ── Agent graph stub ──────────────────────────────────────────────────────────
# executor._run_job lazily imports lightagent.agents.graph.get_async_compiled_graph.
# Stub only the graph module to avoid the langchain_litellm dependency chain that
# lives in lightagent.agents.__init__ → AgentFactory → providers.registry.
# We keep lightagent.agents as a proper package (real __path__) so that other
# scheduler tests can still import lightagent.agents.tools (which only needs
# langchain_core, which IS available in the system Python).
import types
from pathlib import Path as _Path

if "lightagent.agents" not in sys.modules:
    _agents_real = _Path(__file__).parent.parent.parent.parent / "lightagent" / "agents"
    _agents_pkg = types.ModuleType("lightagent.agents")
    _agents_pkg.__path__ = [str(_agents_real)]  # type: ignore[attr-defined]
    _agents_pkg.__package__ = "lightagent.agents"
    sys.modules["lightagent.agents"] = _agents_pkg

if "lightagent.agents.graph" not in sys.modules:
    from unittest.mock import AsyncMock, MagicMock

    _agents_graph = types.ModuleType("lightagent.agents.graph")
    _agents_graph.get_async_compiled_graph = AsyncMock()  # type: ignore[attr-defined]
    _agents_graph.get_compiled_graph = MagicMock()  # type: ignore[attr-defined]
    _agents_graph.list_session_ids = MagicMock(return_value=[])  # type: ignore[attr-defined]
    sys.modules["lightagent.agents.graph"] = _agents_graph
