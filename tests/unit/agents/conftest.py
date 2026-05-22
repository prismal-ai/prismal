"""Shared import guards for agent unit tests.

``langchain_litellm`` is only available inside the project venv.  When the
test runner uses the system Python (e.g. Anaconda) the import chain
``prismal.agents.__init__`` → ``AgentFactory`` → ``providers.registry``
→ ``langchain_litellm`` would otherwise abort collection.

Injecting a lightweight stub into ``sys.modules`` before any test module is
imported prevents that failure while keeping the agent tools tests independent
of the LiteLLM provider internals.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

#: LLM-provider env vars whose presence in the developer's local ``.env``
#: would otherwise leak into unit tests and trip the
#: :meth:`Settings._resolve_llm_provider` conflict warning (which
#: ``filterwarnings=["error", ...]`` converts into a test failure).
_ENV_ISOLATION_KEYS: tuple[str, ...] = (
    "PRISMAL_LLM_PROVIDER",
    "PRISMAL_DEFAULT_MODEL",
    "PRISMAL_MODEL",
    "PRISMAL_FALLBACK_MODEL",
    "PRISMAL_OLLAMA_BASE_URL",
    "OLLAMA_API_BASE",
)


@pytest.fixture(autouse=True)
def _isolate_llm_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip LLM provider env vars and disable .env loading for each test."""
    for key in _ENV_ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)

    try:
        from prismal.core.config import Settings, get_settings
    except Exception:  # pragma: no cover — early import failures
        return

    new_config = dict(Settings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", new_config)
    get_settings.cache_clear()


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
