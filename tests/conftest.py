"""Shared pytest fixtures for lightagent-agents tests."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Use asyncio as the anyio backend."""
    return "asyncio"
