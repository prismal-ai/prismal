"""Closing the async graph's checkpointer (process shutdown).

``build_checkpointer`` opens an ``aiosqlite`` connection whose worker thread is
*non-daemon*; nothing ever closed it, so any process that built the async graph
(e.g. ``python -m prismal.eval run --live-api``) never exited after its work.
``aclose_async_compiled_graph()`` closes every checkpointer it built and resets
the singleton.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import aclose_async_compiled_graph, build_checkpointer


def _worker_threads() -> int:
    return sum(1 for t in threading.enumerate() if "_connection_worker_thread" in t.name)


async def test_close_stops_the_sqlite_worker_thread(tmp_path: Path) -> None:
    before = _worker_threads()
    saver = await build_checkpointer(f"sqlite:///{tmp_path / 'ckpt.db'}")
    assert _worker_threads() == before + 1

    await aclose_async_compiled_graph()

    assert _worker_threads() == before
    with pytest.raises(ValueError):
        await saver.conn.execute("SELECT 1")


async def test_close_resets_the_singleton_and_is_idempotent(tmp_path: Path) -> None:
    graph_module._async_graph = object()

    await aclose_async_compiled_graph()
    await aclose_async_compiled_graph()

    assert graph_module._async_graph is None
