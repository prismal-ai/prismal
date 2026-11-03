"""Closing the async graph's checkpointer (process shutdown).

``build_checkpointer`` opens an ``aiosqlite`` connection whose worker thread is
*non-daemon*; nothing ever closed it, so any process that built the async graph
(e.g. ``python -m prismal.eval run --live-api``) never exited after its work.
``aclose_async_compiled_graph()`` closes every checkpointer it built and resets
the singleton.
"""

from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
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


async def test_close_returns_when_only_the_singleton_holds_the_checkpointer(
    tmp_path: Path,
) -> None:
    """Regression: no caller keeps the graph, so the singleton owns the last reference.

    Dropping the singleton before closing let ``aiosqlite.Connection.__del__`` stop the
    worker thread first; the later ``close()`` then awaited a stop future the exited
    worker never resolved, and shutdown hung forever.
    """
    saver = await build_checkpointer(f"sqlite:///{tmp_path / 'ckpt.db'}")
    # The Thread object does not reference the connection, so holding it keeps the
    # singleton as the only owner of the checkpointer (the scenario under test).
    worker = saver.conn._thread
    graph_module._async_graph = saver
    del saver
    gc.collect()

    await asyncio.wait_for(aclose_async_compiled_graph(), timeout=5)

    assert graph_module._async_graph is None
    worker.join(timeout=5)  # the worker exits right after resolving the stop future
    assert not worker.is_alive()


_EXIT_SCRIPT = """
import asyncio
from prismal.agents.graph import aclose_async_compiled_graph, get_async_compiled_graph

async def main() -> None:
    await get_async_compiled_graph()  # result deliberately not kept
    await asyncio.wait_for(aclose_async_compiled_graph(), timeout=20)

asyncio.run(main())
print("exited-main")
"""


def test_process_exits_after_closing_an_unheld_async_graph(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PRISMAL_LLM_PROVIDER": "",
        "PRISMAL_DB_URL": f"sqlite:///{tmp_path / 'ckpt.db'}",
    }
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and inline script
        [sys.executable, "-W", "ignore", "-c", _EXIT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "exited-main" in proc.stdout
