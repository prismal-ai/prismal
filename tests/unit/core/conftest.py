"""Pytest fixtures shared across core unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_lru_caches() -> None:
    """Clear lru_cache singletons between tests to prevent state leakage."""
    from lightagent.core.config import get_settings
    from lightagent.core.database import _get_engine

    get_settings.cache_clear()
    _get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    _get_engine.cache_clear()
