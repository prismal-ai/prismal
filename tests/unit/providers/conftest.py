"""Pytest fixtures shared across provider unit tests.

Isolates every provider test from the developer's local ``.env`` —
Pydantic Settings reads ``os.environ`` regardless of ``_env_file=None``,
so any ``LIGHTAGENT_LLM_PROVIDER=ollama`` in the dev environment would
otherwise leak into tests and trigger the provider-resolver validator's
conflict warning (which ``filterwarnings=["error", ...]`` turns into a
test failure).
"""

from __future__ import annotations

import pytest

_ENV_ISOLATION_KEYS: tuple[str, ...] = (
    "LIGHTAGENT_LLM_PROVIDER",
    "LIGHTAGENT_DEFAULT_MODEL",
    "LIGHTAGENT_MODEL",
    "LIGHTAGENT_FALLBACK_MODEL",
    "LIGHTAGENT_OLLAMA_BASE_URL",
    "OLLAMA_API_BASE",
)


@pytest.fixture(autouse=True)
def _isolate_llm_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip LLM provider env vars and disable .env loading for each test."""
    for key in _ENV_ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)

    from lightagent.core.config import Settings

    new_config = dict(Settings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", new_config)
