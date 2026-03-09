"""Unit tests for lightagent.scheduler.executor (T-250).

All tests mock APScheduler's scheduler and the LangGraph graph so that no
real timing, LLM calls, or file I/O occur.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.scheduler.cron_manager import CronJob, CronManager
from lightagent.scheduler.executor import CronExecutor


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_job(name: str = "test-job", status: str = "active") -> CronJob:
    """Return a minimal CronJob with the given name and status."""
    return CronJob(name=name, schedule="0 9 * * *", task="Run something", status=status)


def _make_paused_job(name: str = "paused-job") -> CronJob:
    """Return a paused CronJob."""
    return CronJob(name=name, schedule="0 10 * * *", task="Paused task", status="paused")  # type: ignore[call-arg]


@pytest.fixture()
def mock_manager(tmp_path: Path) -> MagicMock:
    """Return a mock CronManager with sensible defaults."""
    manager = MagicMock(spec=CronManager)
    manager.list_jobs.return_value = []
    manager.update_last_run.return_value = None
    return manager


@pytest.fixture()
def mock_scheduler() -> MagicMock:
    """Return a mock AsyncIOScheduler."""
    scheduler = MagicMock()
    scheduler.running = True
    return scheduler


@pytest.fixture()
def executor(mock_manager: MagicMock, mock_scheduler: MagicMock) -> CronExecutor:
    """Return a CronExecutor with mocked manager and scheduler."""
    ex = CronExecutor(manager=mock_manager)
    ex._scheduler = mock_scheduler
    return ex


# ---------------------------------------------------------------------------
# CronExecutor.__init__
# ---------------------------------------------------------------------------


def test_init_default_manager() -> None:
    """CronExecutor creates a default CronManager when none is provided."""
    with patch("lightagent.scheduler.executor.CronManager") as mock_cls:
        mock_cls.return_value = MagicMock(spec=CronManager)
        ex = CronExecutor()
    mock_cls.assert_called_once_with()
    assert ex._manager is mock_cls.return_value


def test_init_custom_manager(mock_manager: MagicMock) -> None:
    """CronExecutor stores the provided CronManager instance."""
    ex = CronExecutor(manager=mock_manager)
    assert ex._manager is mock_manager


# ---------------------------------------------------------------------------
# CronExecutor.start — registers active jobs only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_active_jobs(
    executor: CronExecutor, mock_manager: MagicMock, mock_scheduler: MagicMock
) -> None:
    """start() registers all active jobs with APScheduler."""
    job1 = _make_job("job-a")
    job2 = _make_job("job-b")
    mock_manager.list_jobs.return_value = [job1, job2]

    await executor.start()

    assert mock_scheduler.add_job.call_count == 2
    ids_registered = {
        call.kwargs.get("id") or call.args[2]
        for call in mock_scheduler.add_job.call_args_list
    }
    assert "job-a" in ids_registered
    assert "job-b" in ids_registered
    mock_scheduler.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_skips_paused_jobs(
    executor: CronExecutor, mock_manager: MagicMock, mock_scheduler: MagicMock
) -> None:
    """start() does NOT register paused jobs."""
    active = _make_job("active-job")
    paused = _make_paused_job("paused-job")
    mock_manager.list_jobs.return_value = [active, paused]

    await executor.start()

    assert mock_scheduler.add_job.call_count == 1
    registered_id = mock_scheduler.add_job.call_args.kwargs.get(
        "id"
    ) or mock_scheduler.add_job.call_args.args[2]
    assert registered_id == "active-job"
    mock_scheduler.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_no_jobs(
    executor: CronExecutor, mock_manager: MagicMock, mock_scheduler: MagicMock
) -> None:
    """start() with no jobs still starts the scheduler."""
    mock_manager.list_jobs.return_value = []

    await executor.start()

    mock_scheduler.add_job.assert_not_called()
    mock_scheduler.start.assert_called_once()


# ---------------------------------------------------------------------------
# CronExecutor.stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_shuts_down_running_scheduler(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """stop() calls shutdown(wait=False) when the scheduler is running."""
    mock_scheduler.running = True

    await executor.stop()

    mock_scheduler.shutdown.assert_called_once_with(wait=False)


@pytest.mark.asyncio
async def test_stop_skips_shutdown_when_not_running(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """stop() is a no-op when the scheduler is not running."""
    mock_scheduler.running = False

    await executor.stop()

    mock_scheduler.shutdown.assert_not_called()


# ---------------------------------------------------------------------------
# CronExecutor.add_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_job_registers_with_scheduler(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """add_job() calls scheduler.add_job with correct arguments."""
    job = _make_job("new-job")

    await executor.add_job(job)

    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args.kwargs
    assert call_kwargs["id"] == "new-job"
    assert call_kwargs["replace_existing"] is True
    assert call_kwargs["args"] == ["new-job", "Run something"]


# ---------------------------------------------------------------------------
# CronExecutor.remove_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_job_calls_scheduler(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """remove_job() calls scheduler.remove_job with the job name."""
    await executor.remove_job("some-job")

    mock_scheduler.remove_job.assert_called_once_with("some-job")


@pytest.mark.asyncio
async def test_remove_job_no_op_when_not_found(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """remove_job() does not raise when the job is not found."""
    from apscheduler.jobstores.base import JobLookupError as APJobLookupError

    mock_scheduler.remove_job.side_effect = APJobLookupError("nonexistent")

    # Must not raise
    await executor.remove_job("nonexistent")

    mock_scheduler.remove_job.assert_called_once_with("nonexistent")


# ---------------------------------------------------------------------------
# CronExecutor.pause_job / resume_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_job(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """pause_job() calls scheduler.pause_job with the job name."""
    await executor.pause_job("my-job")

    mock_scheduler.pause_job.assert_called_once_with("my-job")


@pytest.mark.asyncio
async def test_resume_job(
    executor: CronExecutor, mock_scheduler: MagicMock
) -> None:
    """resume_job() calls scheduler.resume_job with the job name."""
    await executor.resume_job("my-job")

    mock_scheduler.resume_job.assert_called_once_with("my-job")


@pytest.mark.asyncio
async def test_pause_job_no_op_when_not_found(mock_manager: MagicMock) -> None:
    """pause_job() logs a warning and does not raise when job is not in scheduler."""
    from apscheduler.jobstores.base import JobLookupError as APJobLookupError

    executor = CronExecutor(manager=mock_manager)
    executor._scheduler = MagicMock()
    executor._scheduler.pause_job.side_effect = APJobLookupError("missing-job")
    # Should not raise
    await executor.pause_job("missing-job")


@pytest.mark.asyncio
async def test_resume_job_no_op_when_not_found(mock_manager: MagicMock) -> None:
    """resume_job() logs a warning and does not raise when job is not in scheduler."""
    from apscheduler.jobstores.base import JobLookupError as APJobLookupError

    executor = CronExecutor(manager=mock_manager)
    executor._scheduler = MagicMock()
    executor._scheduler.resume_job.side_effect = APJobLookupError("missing-job")
    # Should not raise
    await executor.resume_job("missing-job")


# ---------------------------------------------------------------------------
# CronExecutor._run_job — success path
# ---------------------------------------------------------------------------


def _make_graph_module_mock(mock_graph: AsyncMock) -> MagicMock:
    """Build a mock for lightagent.agents.graph with get_async_compiled_graph."""
    graph_mod = MagicMock()
    graph_mod.get_async_compiled_graph = AsyncMock(return_value=mock_graph)
    return graph_mod


def _make_messages_module_mock(capture: list[str] | None = None) -> MagicMock:
    """Build a mock for langchain_core.messages with HumanMessage."""
    msgs_mod = MagicMock()

    def _hm(content: str) -> MagicMock:
        if capture is not None:
            capture.append(content)
        return MagicMock()

    msgs_mod.HumanMessage = _hm
    return msgs_mod


@pytest.mark.asyncio
async def test_run_job_calls_graph_and_updates_last_run(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() invokes the LangGraph graph and calls update_last_run on success."""
    import sys

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

    graph_mod = _make_graph_module_mock(mock_graph)
    msgs_mod = _make_messages_module_mock()

    with (
        patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
        patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
    ):
        await executor._run_job("daily-brief", "Summarise news")

    mock_graph.ainvoke.assert_called_once()
    call_args = mock_graph.ainvoke.call_args
    config = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["config"]
    thread_id = config["configurable"]["thread_id"]
    # Thread ID format: cron-{name}-{YYYYmmddHHMMSS}
    assert thread_id.startswith("cron-daily-brief-")
    assert len(thread_id) == len("cron-daily-brief-") + 14  # 14 digits for timestamp

    # update_last_run is now called with (name, finished_at)
    assert mock_manager.update_last_run.call_count == 1
    assert mock_manager.update_last_run.call_args.args[0] == "daily-brief"


@pytest.mark.asyncio
async def test_run_job_uses_task_as_message(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() passes the task string as the HumanMessage content."""
    import sys

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={})
    captured_content: list[str] = []

    graph_mod = _make_graph_module_mock(mock_graph)
    msgs_mod = _make_messages_module_mock(capture=captured_content)

    with (
        patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
        patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
    ):
        await executor._run_job("job-x", "Fetch daily report")

    assert captured_content == ["Fetch daily report"]


# ---------------------------------------------------------------------------
# CronExecutor._run_job — failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_does_not_raise_on_graph_error(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() swallows exceptions so APScheduler keeps running future ticks."""
    import sys

    graph_mod = MagicMock()
    graph_mod.get_async_compiled_graph = AsyncMock(
        side_effect=RuntimeError("LLM unavailable")
    )
    msgs_mod = _make_messages_module_mock()

    with (
        patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
        patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
    ):
        # Must NOT raise
        await executor._run_job("failing-job", "Do something")

    # update_last_run must NOT be called on failure
    mock_manager.update_last_run.assert_not_called()


@pytest.mark.asyncio
async def test_run_job_does_not_update_last_run_on_ainvoke_error(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() skips update_last_run when ainvoke raises."""
    import sys

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(side_effect=ValueError("Graph error"))
    graph_mod = _make_graph_module_mock(mock_graph)
    msgs_mod = _make_messages_module_mock()

    with (
        patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
        patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
    ):
        await executor._run_job("broken-job", "Run task")

    mock_manager.update_last_run.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunJobHistory — _run_job writes CronRunRecord rows
# ---------------------------------------------------------------------------


class TestRunJobHistory:
    """Tests that _run_job records execution history via CronManager."""

    @pytest.fixture()
    def manager(self, tmp_path: Path) -> CronManager:
        """Return a real CronManager backed by a temporary SQLite database."""
        return CronManager(db_path=tmp_path / "cron_test.db")

    @pytest.fixture()
    def executor(self, manager: CronManager) -> CronExecutor:
        """Return a CronExecutor with a real CronManager and mocked scheduler."""
        ex = CronExecutor(manager=manager)
        ex._scheduler = MagicMock()
        return ex

    @pytest.mark.asyncio
    async def test_run_job_records_success(
        self,
        executor: CronExecutor,
        manager: CronManager,
    ) -> None:
        """Successful run records a 'success' CronRunRecord."""
        import sys

        manager.add("hist-test", "* * * * *", "some task")

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})
        graph_mod = _make_graph_module_mock(mock_graph)
        msgs_mod = _make_messages_module_mock()

        with (
            patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
            patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
        ):
            await executor._run_job("hist-test", "some task")

        records = manager.get_run_history("hist-test")
        assert len(records) == 1
        assert records[0].outcome == "success"
        assert records[0].duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_job_records_failure(
        self,
        executor: CronExecutor,
        manager: CronManager,
    ) -> None:
        """Failed run records a 'failure' CronRunRecord with error text."""
        import sys

        manager.add("fail-hist", "* * * * *", "task")

        graph_mod = MagicMock()
        graph_mod.get_async_compiled_graph = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        msgs_mod = _make_messages_module_mock()

        with (
            patch.dict(sys.modules, {"lightagent.agents.graph": graph_mod}),
            patch.dict(sys.modules, {"langchain_core.messages": msgs_mod}),
        ):
            await executor._run_job("fail-hist", "task")

        records = manager.get_run_history("fail-hist")
        assert len(records) == 1
        assert records[0].outcome == "failure"
        assert "boom" in (records[0].error or "")
