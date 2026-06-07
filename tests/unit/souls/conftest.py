"""Pytest fixtures shared across souls unit tests.

Isolates every souls test from the developer's local ``.env`` — Pydantic
Settings reads ``os.environ`` regardless of ``_env_file=None``, so any
``PRISMAL_LLM_PROVIDER=...`` in the dev environment would otherwise leak into
tests and trigger the provider-resolver validator's conflict warning (which
``filterwarnings=["error", ...]`` turns into a test failure).  Mirrors
``tests/unit/providers/conftest.py``.
"""

from __future__ import annotations

import pytest

_ENV_ISOLATION_KEYS: tuple[str, ...] = (
    "PRISMAL_LLM_PROVIDER",
    "PRISMAL_DEFAULT_MODEL",
    "PRISMAL_MODEL",
    "PRISMAL_FALLBACK_MODEL",
    "PRISMAL_SOULS_DIR",
    "PRISMAL_KOKORO_SOULS",
    "PRISMAL_SOUL_MAX_BODY_CHARS",
)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip provider/souls env vars and disable .env loading for each test."""
    for key in _ENV_ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)

    from prismal.core.config import Settings

    new_config = dict(Settings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", new_config)
