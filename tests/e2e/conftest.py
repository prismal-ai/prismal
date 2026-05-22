"""Shared fixtures for end-to-end tests.

E2E tests exercise the *real* compiled LangGraph supervisor graph (no module
stubs), so they require the project's dependencies to be installed — they run
in CI and locally inside the project venv, not under a bare system Python.

The only isolation applied here mirrors the unit-test agent conftest: strip
LLM-provider env vars and disable ``.env`` loading so a developer's local
configuration cannot leak into the run and trip the provider-conflict warning
(which ``filterwarnings = ["error", ...]`` would turn into a failure).
"""

from __future__ import annotations

import pytest

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
