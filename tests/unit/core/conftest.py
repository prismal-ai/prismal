"""Pytest fixtures shared across core unit tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

#: Environment variables that the developer's local ``.env`` may populate
#: and that cause :class:`~lightagent.core.config.Settings` construction to
#: either override test expectations or emit warnings that pytest's
#: ``filterwarnings = ["error", ...]`` turns into test failures.  The
#: autouse fixture below hides them for the duration of every test so
#: unit tests are reproducible regardless of the developer's .env state.
_ENV_ISOLATION_KEYS: tuple[str, ...] = (
    "LIGHTAGENT_LLM_PROVIDER",
    "LIGHTAGENT_DEFAULT_MODEL",
    "LIGHTAGENT_MODEL",
    "LIGHTAGENT_FALLBACK_MODEL",
    "LIGHTAGENT_OLLAMA_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolate_llm_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Insulate core tests from the developer's local ``.env`` file.

    Clears the LLM-provider-related env vars AND patches
    ``Settings.model_config`` to disable ``.env`` loading for the test.
    ``monkeypatch`` restores both on teardown.
    """
    for key in _ENV_ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)

    from lightagent.core.config import Settings

    new_config = dict(Settings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", new_config)


@pytest.fixture(autouse=True)
def clear_lru_caches() -> Iterator[None]:
    """Clear lru_cache singletons between tests to prevent state leakage."""
    from lightagent.core.config import get_settings
    from lightagent.core.database import _get_engine

    get_settings.cache_clear()
    _get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    _get_engine.cache_clear()
