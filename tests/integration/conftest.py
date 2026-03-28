"""Shared import guards for integration tests.

APScheduler, prefect, and the LangGraph agent graph are only available inside
the project venv.  When tests run on the system Python (e.g. Anaconda) these
stubs prevent collection errors while still allowing the integration tests to
exercise the real scheduler/manager/executor logic.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ── APScheduler stub ──────────────────────────────────────────────────────────
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

# ── langchain_litellm stub ────────────────────────────────────────────────────
if "langchain_litellm" not in sys.modules:
    _llm_stub = MagicMock()
    _llm_stub.ChatLiteLLM = MagicMock
    sys.modules["langchain_litellm"] = _llm_stub

# ── Agent graph stub ──────────────────────────────────────────────────────────
# executor._run_job lazily imports lightagent.agents.graph.get_async_compiled_graph.
# Stub the graph module to avoid the langchain_litellm dependency chain.
_agents_real = Path(__file__).parent.parent.parent / "lightagent" / "agents"

if "lightagent.agents" not in sys.modules:
    _agents_pkg = types.ModuleType("lightagent.agents")
    _agents_pkg.__path__ = [str(_agents_real)]  # type: ignore[attr-defined]
    _agents_pkg.__package__ = "lightagent.agents"
    sys.modules["lightagent.agents"] = _agents_pkg

if "lightagent.agents.graph" not in sys.modules:
    _agents_graph = types.ModuleType("lightagent.agents.graph")
    _agents_graph.get_async_compiled_graph = AsyncMock()  # type: ignore[attr-defined]
    _agents_graph.get_compiled_graph = MagicMock()  # type: ignore[attr-defined]
    _agents_graph.list_session_ids = MagicMock(return_value=[])  # type: ignore[attr-defined]
    sys.modules["lightagent.agents.graph"] = _agents_graph
