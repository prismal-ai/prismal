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
    # Return None so the retry policy falls through to the notify-and-reset branch
    manager.get_job.return_value = None
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



@pytest.mark.asyncio
async def test_run_job_calls_graph_and_updates_last_run(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() invokes the LangGraph graph and calls update_last_run on success."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

    with patch(
        "lightagent.agents.graph.get_async_compiled_graph",
        new=AsyncMock(return_value=mock_graph),
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
    mock_graph = AsyncMock()
    captured_content: list[str] = []

    original_ainvoke = AsyncMock(return_value={})

    async def capturing_ainvoke(state: dict, **kwargs: object) -> dict:
        """Capture the HumanMessage content passed to ainvoke."""
        msgs = state.get("messages", [])
        for msg in msgs:
            content = getattr(msg, "content", None)
            if content is not None:
                captured_content.append(content)
        return {}

    mock_graph.ainvoke = capturing_ainvoke

    with patch(
        "lightagent.agents.graph.get_async_compiled_graph",
        new=AsyncMock(return_value=mock_graph),
    ):
        await executor._run_job("job-x", "Fetch daily report")

    assert "Fetch daily report" in captured_content


# ---------------------------------------------------------------------------
# CronExecutor._run_job — failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_does_not_raise_on_graph_error(
    executor: CronExecutor, mock_manager: MagicMock
) -> None:
    """_run_job() swallows exceptions so APScheduler keeps running future ticks."""
    with patch(
        "lightagent.agents.graph.get_async_compiled_graph",
        new=AsyncMock(side_effect=RuntimeError("LLM unavailable")),
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
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(side_effect=ValueError("Graph error"))

    with patch(
        "lightagent.agents.graph.get_async_compiled_graph",
        new=AsyncMock(return_value=mock_graph),
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
        manager.add("hist-test", "* * * * *", "some task")

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(return_value=mock_graph),
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
        manager.add("fail-hist", "* * * * *", "task")

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await executor._run_job("fail-hist", "task")

        records = manager.get_run_history("fail-hist")
        assert len(records) == 1
        assert records[0].outcome == "failure"
        assert "boom" in (records[0].error or "")


# ---------------------------------------------------------------------------
# TestCronExecutorNotifier — notifier integration
# ---------------------------------------------------------------------------


class TestCronExecutorNotifier:
    """Tests that CronExecutor calls the notifier on job failure."""

    @pytest.fixture()
    def manager(self, tmp_path: Path) -> CronManager:
        """Return a real CronManager backed by a temporary SQLite database."""
        return CronManager(db_path=tmp_path / "cron_test.db")

    @pytest.mark.asyncio
    async def test_notifier_called_on_failure(
        self,
        manager: CronManager,
    ) -> None:
        """notify_failure is called when _run_job raises."""
        from unittest.mock import AsyncMock, MagicMock

        mock_notifier = MagicMock()
        mock_notifier.notify_failure = AsyncMock()

        executor = CronExecutor(manager=manager, notifier=mock_notifier)
        executor._scheduler = MagicMock()
        manager.add("notify-test", "* * * * *", "task")

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(side_effect=RuntimeError("crash")),
        ):
            await executor._run_job("notify-test", "task")

        mock_notifier.notify_failure.assert_called_once()
        call_kwargs = mock_notifier.notify_failure.call_args.kwargs
        assert call_kwargs["job_name"] == "notify-test"
        assert "crash" in call_kwargs["error"]
        assert call_kwargs["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_notifier_not_called_on_success(
        self,
        manager: CronManager,
    ) -> None:
        """notify_failure is NOT called when _run_job succeeds."""
        from unittest.mock import AsyncMock, MagicMock

        from langchain_core.messages import AIMessage

        mock_notifier = MagicMock()
        mock_notifier.notify_failure = AsyncMock()
        mock_notifier.notify_success = AsyncMock()  # must be AsyncMock — _run_job awaits it

        executor = CronExecutor(manager=manager, notifier=mock_notifier)
        executor._scheduler = MagicMock()
        manager.add("success-notify", "* * * * *", "task")

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [AIMessage(content="done")]}
        )

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(return_value=mock_graph),
        ):
            await executor._run_job("success-notify", "task")

        mock_notifier.notify_failure.assert_not_called()


# ---------------------------------------------------------------------------
# TestRetryLogic — exponential backoff retry on failure
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Tests for CronExecutor retry-on-failure behaviour."""

    @pytest.fixture()
    def manager(self, tmp_path: Path) -> CronManager:
        """Return a real CronManager backed by a temporary SQLite database."""
        return CronManager(db_path=tmp_path / "cron_test.db")

    @pytest.mark.asyncio
    async def test_retry_scheduled_on_first_failure(
        self,
        manager: CronManager,
    ) -> None:
        """A one-shot retry is scheduled when retries remain after failure."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_notifier = MagicMock()
        mock_notifier.notify_failure = AsyncMock()
        executor = CronExecutor(manager=manager, notifier=mock_notifier)
        executor._scheduler = MagicMock()

        manager.add("retry-job", "* * * * *", "task", max_retries=2)

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(side_effect=RuntimeError("fail")),
        ):
            await executor._run_job("retry-job", "task")

        # retry_count incremented
        job = manager.get_job("retry-job")
        assert job is not None
        assert job.retry_count == 1
        # A retry job was scheduled in APScheduler
        executor._scheduler.add_job.assert_called_once()
        call_args = executor._scheduler.add_job.call_args
        assert call_args.kwargs.get("id") == "retry-job_retry_1"
        # Notifier NOT called yet
        mock_notifier.notify_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_notifier_called_on_final_retry_failure(
        self,
        manager: CronManager,
    ) -> None:
        """Notifier fires and retry_count resets after exhausting all retries."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_notifier = MagicMock()
        mock_notifier.notify_failure = AsyncMock()
        executor = CronExecutor(manager=manager, notifier=mock_notifier)
        executor._scheduler = MagicMock()

        manager.add("final-job", "* * * * *", "task", max_retries=1)
        # Simulate already at max retries
        manager.set_retry_count("final-job", 1)

        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(side_effect=RuntimeError("final")),
        ):
            await executor._run_job("final-job", "task")

        # retry_count reset to 0
        job = manager.get_job("final-job")
        assert job is not None
        assert job.retry_count == 0
        # No new retry scheduled
        executor._scheduler.add_job.assert_not_called()
        # Notifier WAS called
        mock_notifier.notify_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_count_reset_on_success(
        self,
        manager: CronManager,
    ) -> None:
        """retry_count is reset to 0 on a successful run."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from langchain_core.messages import AIMessage

        executor = CronExecutor(manager=manager)
        executor._scheduler = MagicMock()
        manager.add("success-job", "* * * * *", "task", max_retries=3)
        manager.set_retry_count("success-job", 2)  # simulate mid-retry

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [AIMessage(content="done")]}
        )
        with patch(
            "lightagent.agents.graph.get_async_compiled_graph",
            new=AsyncMock(return_value=mock_graph),
        ):
            await executor._run_job("success-job", "task")

        job = manager.get_job("success-job")
        assert job is not None
        assert job.retry_count == 0


# ---------------------------------------------------------------------------
# HeartbeatDelivery routing — output_channel / output_target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_job_with_output_routing_calls_delivery(
    tmp_path: Path,
) -> None:
    """When a job has output_channel set, HeartbeatDelivery.send() is called."""
    from lightagent.scheduler.cron_manager import CronManager
    from lightagent.scheduler.executor import CronExecutor

    manager = CronManager(db_path=tmp_path / "test.db")
    manager.add(
        "notify-job",
        "* * * * *",
        "Check news",
        output_channel="telegram",
        output_target="99887766",
    )

    mock_delivery = AsyncMock()

    with (
        patch(
            "lightagent.agents.graph.get_async_compiled_graph"
        ) as mock_graph_fn,
        patch(
            "lightagent.scheduler.heartbeat_delivery.HeartbeatDelivery",
            return_value=mock_delivery,
        ),
    ):
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "messages": [MagicMock(content="Here is your report")]
            }
        )
        mock_graph_fn.return_value = mock_graph

        executor = CronExecutor(manager=manager)
        await executor._run_job("notify-job", "Check news")

    mock_delivery.send.assert_awaited_once_with(
        "telegram", "99887766", "Here is your report"
    )


@pytest.mark.asyncio
async def test_job_without_output_routing_does_not_call_delivery(
    tmp_path: Path,
) -> None:
    """When a job has no output_channel, HeartbeatDelivery.send() is NOT called."""
    from lightagent.scheduler.cron_manager import CronManager
    from lightagent.scheduler.executor import CronExecutor

    manager = CronManager(db_path=tmp_path / "test.db")
    manager.add("silent-job", "* * * * *", "Silent task")

    mock_delivery = AsyncMock()

    with (
        patch(
            "lightagent.agents.graph.get_async_compiled_graph"
        ) as mock_graph_fn,
        patch(
            "lightagent.scheduler.heartbeat_delivery.HeartbeatDelivery",
            return_value=mock_delivery,
        ),
    ):
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={"messages": [MagicMock(content="done")]}
        )
        mock_graph_fn.return_value = mock_graph

        executor = CronExecutor(manager=manager)
        await executor._run_job("silent-job", "Silent task")

    mock_delivery.send.assert_not_awaited()


# ── Timezone-aware scheduler init (Phase 28) ──────────────────────────────────


def test_executor_scheduler_receives_timezone() -> None:
    """CronExecutor passes a timezone to AsyncIOScheduler on construction."""
    from zoneinfo import ZoneInfo

    from lightagent.scheduler.executor import CronExecutor

    with patch(
        "lightagent.scheduler.executor.AsyncIOScheduler"
    ) as mock_scheduler_cls:
        mock_scheduler_cls.return_value = MagicMock()
        ex = CronExecutor()
        # AsyncIOScheduler must be called with a timezone kwarg
        call_kwargs = mock_scheduler_cls.call_args.kwargs
        assert "timezone" in call_kwargs
        assert isinstance(call_kwargs["timezone"], ZoneInfo)
        _ = ex  # suppress unused-variable warning


def test_executor_scheduler_timezone_is_utc_by_default() -> None:
    """When no env vars are set, the scheduler timezone resolves to UTC."""
    import os
    from zoneinfo import ZoneInfo

    from lightagent.scheduler.datetime_service import DateTimeService
    from lightagent.scheduler.executor import CronExecutor

    DateTimeService._instance = None
    env = {"LIGHTAGENT_TIMEZONE": "", "LIGHTAGENT_CRON_TIMEZONE": ""}
    with (
        patch.dict(os.environ, env, clear=False),
        patch("lightagent.scheduler.executor.AsyncIOScheduler") as mock_scheduler_cls,
        patch("tzlocal.get_localzone", return_value=ZoneInfo("UTC")),
    ):
        mock_scheduler_cls.return_value = MagicMock()
        DateTimeService._instance = None
        _ex = CronExecutor()
        tz = mock_scheduler_cls.call_args.kwargs["timezone"]
        assert tz == ZoneInfo("UTC")
    DateTimeService._instance = None


# ── _register_job timezone (Phase 28) ────────────────────────────────────────


def test_register_job_passes_timezone_to_cron_trigger(mock_manager: MagicMock) -> None:
    """_register_job passes the job's timezone to CronTrigger.from_crontab."""
    from lightagent.scheduler.executor import CronExecutor

    job = CronJob(name="tz-job", schedule="0 9 * * *", task="t", timezone="America/Caracas")
    ex = CronExecutor(manager=mock_manager)

    with patch(
        "lightagent.scheduler.executor.CronTrigger"
    ) as mock_trigger_cls:
        mock_trigger_cls.from_crontab.return_value = MagicMock()
        ex._register_job(job)
        mock_trigger_cls.from_crontab.assert_called_once()
        call_kwargs = mock_trigger_cls.from_crontab.call_args.kwargs
        assert "timezone" in call_kwargs
        assert str(call_kwargs["timezone"]) == "America/Caracas"


def test_register_once_job_with_future_date_uses_aware_run_date(
    mock_manager: MagicMock,
) -> None:
    """_register_job attaches timezone to run_date for 'once:' jobs (BUG-001 fix)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from lightagent.scheduler.executor import CronExecutor

    future = datetime.now() + timedelta(hours=1)  # noqa: DTZ005
    schedule = f"once:{future.strftime('%Y-%m-%dT%H:%M:%S')}"
    job = CronJob(name="once-tz", schedule=schedule, task="t", timezone="America/Caracas")

    ex = CronExecutor(manager=mock_manager)

    # Patch DateTrigger inside the executor module so we can inspect the call args
    with (
        patch("apscheduler.triggers.date.DateTrigger") as mock_date_trigger_cls,
        patch.object(ex._scheduler, "add_job"),
    ):
        mock_date_trigger_cls.return_value = MagicMock()
        ex._register_job(job)
        # DateTrigger must be instantiated with an aware run_date in Caracas TZ
        mock_date_trigger_cls.assert_called_once()
        run_date_arg = mock_date_trigger_cls.call_args.kwargs["run_date"]
        assert run_date_arg.tzinfo == ZoneInfo("America/Caracas")


def test_register_expired_once_job_is_skipped_and_removed(
    mock_manager: MagicMock,
) -> None:
    """Expired 'once:' jobs are skipped and removed from the DB (BUG-001 fix)."""
    from datetime import datetime, timedelta

    from lightagent.scheduler.executor import CronExecutor

    past = datetime.now() - timedelta(hours=1)  # noqa: DTZ005
    schedule = f"once:{past.strftime('%Y-%m-%dT%H:%M:%S')}"
    job = CronJob(name="expired-once", schedule=schedule, task="t")

    ex = CronExecutor(manager=mock_manager)
    mock_manager.remove.return_value = None

    with patch.object(ex._scheduler, "add_job") as mock_add_job:
        ex._register_job(job)
        mock_add_job.assert_not_called()
        mock_manager.remove.assert_called_once_with("expired-once")
