"""Shared fixtures and import guards for scheduler unit tests.

APScheduler is only available inside the project venv. When the test runner
uses the system Python (e.g. Anaconda) the ``apscheduler`` import chain
inside ``lightagent.scheduler.__init__`` would otherwise abort collection.
Injecting lightweight stubs into ``sys.modules`` before any test module is
imported prevents that failure while keeping the scheduler tests independent
of APScheduler internals.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# ── APScheduler stub ──────────────────────────────────────────────────────────
# Only inject if not already available (venv case has real package).
if "apscheduler" not in sys.modules:
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
if "prefect" not in sys.modules:
    sys.modules["prefect"] = MagicMock()
    sys.modules["prefect.deployments"] = MagicMock()

# ── Agent graph stub ──────────────────────────────────────────────────────────
# executor._run_job lazily imports lightagent.agents.graph.get_async_compiled_graph.
# Stub only the graph module to avoid the langchain_litellm dependency chain that
# lives in lightagent.agents.__init__ → AgentFactory → providers.registry.
# We keep lightagent.agents as a proper package (real __path__) so that other
# scheduler tests can still import lightagent.agents.tools (which only needs
# langchain_core, which IS available in the system Python).
import types  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

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
