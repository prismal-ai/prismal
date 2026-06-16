"""Shared fixtures for the eval-harness tests.

The async compiled graph is memoized in a module global (``_async_graph``) bound
to the event loop it was first built in. Because pytest-asyncio gives each async
test its own loop, a graph built in one test's loop becomes unusable (stale
``AsyncSqliteSaver`` connection) in the next. Resetting the singleton before each
test makes every real-graph eval test build a fresh graph in its own loop.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_async_graph() -> Iterator[None]:
    """Reset the memoized async graph so each test builds it in its own loop."""
    import prismal.agents.graph as graph_module

    graph_module._async_graph = None
    try:
        yield
    finally:
        graph_module._async_graph = None
