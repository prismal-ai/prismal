"""Shared import guards for agent unit tests.

``langchain_litellm`` is only available inside the project venv.  When the
test runner uses the system Python (e.g. Anaconda) the import chain
``lightagent.agents.__init__`` → ``AgentFactory`` → ``providers.registry``
→ ``langchain_litellm`` would otherwise abort collection.

Injecting a lightweight stub into ``sys.modules`` before any test module is
imported prevents that failure while keeping the agent tools tests independent
of the LiteLLM provider internals.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# ── langchain_litellm stub ─────────────────────────────────────────────────
# Only inject if not already available (venv case has real package).
if "langchain_litellm" not in sys.modules:
    _llm_stub = MagicMock()
    _llm_stub.ChatLiteLLM = MagicMock
    sys.modules["langchain_litellm"] = _llm_stub

# ── APScheduler stub ──────────────────────────────────────────────────────
if "apscheduler" not in sys.modules:
    sys.modules["apscheduler"] = MagicMock()
    sys.modules["apscheduler.schedulers"] = MagicMock()
    sys.modules["apscheduler.schedulers.asyncio"] = MagicMock()
    sys.modules["apscheduler.triggers"] = MagicMock()
    sys.modules["apscheduler.triggers.cron"] = MagicMock()
    sys.modules["apscheduler.triggers.date"] = MagicMock()
    sys.modules["apscheduler.jobstores"] = MagicMock()
    sys.modules["apscheduler.jobstores.base"] = MagicMock()
    sys.modules["apscheduler.jobstores.base"].JobLookupError = Exception
