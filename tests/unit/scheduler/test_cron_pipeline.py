"""Integration tests for the cron execution pipeline (T-256).

These tests exercise the end-to-end link between ``CronExecutor._run_job()``
and a *real* ``CronManager`` backed by an isolated SQLite database in
``tmp_path``.  No LLM calls or APScheduler timing are involved.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from lightagent.scheduler.cron_manager import CronManager
from lightagent.scheduler.executor import CronExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GRAPH_PATCH = "lightagent.scheduler.executor.get_async_compiled_graph"


def _make_compiled_graph_mock(*, fail: bool = False) -> AsyncMock:
    """Return an ``AsyncMock`` for ``get_async_compiled_graph``.

    The mock returns a graph object whose ``ainvoke`` either succeeds or
    raises depending on *fail*.

    Args:
        fail: When ``True`` the ``ainvoke`` coroutine raises ``RuntimeError``.

    Returns:
        An :class:`~unittest.mock.AsyncMock` that can be used as the
        ``get_async_compiled_graph`` replacement.
    """
    mock_graph = AsyncMock()
    if fail:
        mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph error"))
    else:
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [MagicMock(content="done")]}
        )
    return AsyncMock(return_value=mock_graph)


# ---------------------------------------------------------------------------
# _run_job + CronManager integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_updates_last_run_in_db(tmp_path: Path) -> None:
    """``_run_job()`` calls ``update_last_run()`` so the timestamp persists."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("test-job", "0 9 * * *", "Do something")

    executor = CronExecutor(manager=manager)

    with patch(_GRAPH_PATCH, _make_compiled_graph_mock()):
        await executor._run_job("test-job", "Do something")

    job = manager.get_job("test-job")
    assert job is not None
    assert job.last_run is not None, "last_run must be set after a successful run"


@pytest.mark.asyncio
async def test_run_job_does_not_update_last_run_on_failure(tmp_path: Path) -> None:
    """``_run_job()`` does not update ``last_run`` when the graph raises."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("fail-job", "0 9 * * *", "Do something")

    executor = CronExecutor(manager=manager)

    with patch(_GRAPH_PATCH, _make_compiled_graph_mock(fail=True)):
        # Must not raise — APScheduler expects errors to be swallowed
        await executor._run_job("fail-job", "Do something")

    job = manager.get_job("fail-job")
    assert job is not None
    assert job.last_run is None, "last_run must remain None after a failed run"


@pytest.mark.asyncio
async def test_run_job_updates_only_the_targeted_job(tmp_path: Path) -> None:
    """``_run_job()`` only updates ``last_run`` for the executed job."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("job-a", "0 9 * * *", "Task A")
    manager.add("job-b", "0 10 * * *", "Task B")

    executor = CronExecutor(manager=manager)

    with patch(_GRAPH_PATCH, _make_compiled_graph_mock()):
        await executor._run_job("job-a", "Task A")

    job_a = manager.get_job("job-a")
    job_b = manager.get_job("job-b")
    assert job_a is not None
    assert job_a.last_run is not None
    assert job_b is not None
    assert job_b.last_run is None


@pytest.mark.asyncio
async def test_run_job_multiple_times_updates_last_run_each_time(
    tmp_path: Path,
) -> None:
    """Repeated ``_run_job()`` calls keep updating ``last_run`` in the DB."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("repeat-job", "* * * * *", "Run frequently")

    executor = CronExecutor(manager=manager)

    with patch(_GRAPH_PATCH, _make_compiled_graph_mock()):
        await executor._run_job("repeat-job", "Run frequently")
        job_after_first = manager.get_job("repeat-job")
        assert job_after_first is not None
        first_last_run = job_after_first.last_run

        # Brief pause so the second timestamp differs (SQLite stores seconds)
        time.sleep(1.1)

        await executor._run_job("repeat-job", "Run frequently")

    job_after_second = manager.get_job("repeat-job")
    assert job_after_second is not None
    assert job_after_second.last_run is not None
    assert job_after_second.last_run >= first_last_run  # type: ignore[operator]


# ---------------------------------------------------------------------------
# start() + CronManager integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_jobs_and_skips_paused(tmp_path: Path) -> None:
    """``start()`` registers only active jobs; paused jobs are skipped."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("active-job", "0 9 * * *", "Active task")
    manager.add("paused-job", "0 10 * * *", "Paused task")
    manager.pause("paused-job")

    executor = CronExecutor(manager=manager)

    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch(
        "lightagent.scheduler.executor.AsyncIOScheduler",
        return_value=mock_scheduler,
    ):
        executor._scheduler = mock_scheduler
        await executor.start()

    # Only the active job should be registered
    assert mock_scheduler.add_job.call_count == 1
    call_kwargs = mock_scheduler.add_job.call_args.kwargs
    assert call_kwargs["id"] == "active-job"


@pytest.mark.asyncio
async def test_start_with_real_manager_empty_db(tmp_path: Path) -> None:
    """``start()`` works correctly with a real CronManager that has no jobs."""
    manager = CronManager(db_path=tmp_path / "empty.db")

    executor = CronExecutor(manager=manager)
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch(
        "lightagent.scheduler.executor.AsyncIOScheduler",
        return_value=mock_scheduler,
    ):
        executor._scheduler = mock_scheduler
        await executor.start()

    mock_scheduler.add_job.assert_not_called()
    mock_scheduler.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_registers_all_active_jobs_from_real_manager(
    tmp_path: Path,
) -> None:
    """``start()`` registers all active jobs from the SQLite-backed CronManager."""
    manager = CronManager(db_path=tmp_path / "cron.db")
    manager.add("job-1", "0 8 * * *", "Morning task")
    manager.add("job-2", "0 12 * * *", "Noon task")
    manager.add("job-3", "0 18 * * *", "Evening task")
    manager.pause("job-2")  # only job-1 and job-3 should be registered

    executor = CronExecutor(manager=manager)
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch(
        "lightagent.scheduler.executor.AsyncIOScheduler",
        return_value=mock_scheduler,
    ):
        executor._scheduler = mock_scheduler
        await executor.start()

    assert mock_scheduler.add_job.call_count == 2
    registered_ids = {
        call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list
    }
    assert registered_ids == {"job-1", "job-3"}
