"""Shared pytest fixtures for prismal tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Use asyncio as the anyio backend."""
    return "asyncio"


#: Provider/model env vars a developer's local ``.env`` typically sets. Under
#: ``filterwarnings = error`` they make ``Settings`` construction either warn
#: (``_resolve_llm_provider``) or override test expectations. They also leak into
#: ``os.environ`` indirectly because LiteLLM calls ``load_dotenv()`` on import.
#: The autouse fixture clears them so unit tests are reproducible regardless of
#: the developer's ``.env`` state (mirrors ``tests/unit/core/conftest.py``).
_ENV_ISOLATION_KEYS: tuple[str, ...] = (
    "PRISMAL_LLM_PROVIDER",
    "PRISMAL_DEFAULT_MODEL",
    "PRISMAL_MODEL",
    "PRISMAL_FALLBACK_MODEL",
    "PRISMAL_OLLAMA_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolate_dotenv_from_tests(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the developer's local ``.env`` from every test (Phase W).

    Before Phase W, test conftests disabled ``.env`` by patching
    ``Settings.model_config["env_file"] = None``. Phase W moved all env / ``.env``
    reading out of ``Settings`` and into the injected
    :class:`~prismal.core.config_source.ConfigSourcePort`, so that patch is now a
    no-op. We restore reproducibility two ways:

    1. Inject an :class:`~prismal.core.config_source.EnvConfigSource` that reads
       ``os.environ`` only (``dotenv_path=None``) — so ``monkeypatch.setenv``
       still works while the on-disk ``.env`` stays invisible to the core.
    2. Clear the provider/model keys that LiteLLM's ``load_dotenv()`` leaks into
       ``os.environ`` from the developer's ``.env``.

    Tests that inject their own source override (1) for their body; teardown
    resets the global to ``None``.
    """
    from prismal.core.config_source import EnvConfigSource, set_config_source

    for key in _ENV_ISOLATION_KEYS:
        monkeypatch.delenv(key, raising=False)
    set_config_source(EnvConfigSource(dotenv_path=None))
    try:
        yield
    finally:
        set_config_source(None)
