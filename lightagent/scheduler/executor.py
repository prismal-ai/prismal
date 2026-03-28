"""APScheduler-backed cron execution engine for LightAgent.

``CronExecutor`` bridges the :class:`~lightagent.scheduler.cron_manager.CronManager`
(which persists job definitions in SQLite) and the live runtime: it loads all
``active`` jobs on :meth:`CronExecutor.start`, registers them with an
:class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler`, and invokes the
LangGraph agent graph on each scheduled tick.

Example::

    from lightagent.scheduler.executor import CronExecutor

    executor = CronExecutor()
    await executor.start()
    # ... application runs ...
    await executor.stop()
"""

from __future__ import annotations

import asyncio

import structlog
from apscheduler.jobstores.base import JobLookupError as APJobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lightagent.scheduler.cron_manager import CronJob, CronManager
from lightagent.scheduler.datetime_service import DateTimeService
from lightagent.scheduler.notifier import CronNotifier

logger = structlog.get_logger("lightagent.scheduler.executor")

# Module-level singleton tracking the currently running executor instance.
_running_executor: CronExecutor | None = None


def get_running_executor() -> CronExecutor | None:
    """Return the currently running CronExecutor instance, or None if not started.

    Returns:
        The active :class:`CronExecutor` instance, or ``None`` when no executor
        has been started.
    """
    return _running_executor


class CronExecutor:
    """APScheduler-backed executor that runs LightAgent cron jobs on schedule.

    On :meth:`start` the executor reads every ``active`` job from
    :class:`~lightagent.scheduler.cron_manager.CronManager`, registers each
    with APScheduler's :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler`
    using a :class:`~apscheduler.triggers.cron.CronTrigger`, then starts the
    scheduler.  On each tick APScheduler calls :meth:`_run_job` which invokes
    the LangGraph agent graph with the job's task as the user message.

    Args:
        manager: Optional :class:`CronManager` instance.  Defaults to a new
            ``CronManager()`` backed by the standard SQLite database.
    """

    def __init__(
        self,
        manager: CronManager | None = None,
        notifier: CronNotifier | None = None,
    ) -> None:
        """Initialise the executor with an optional CronManager and CronNotifier.

        Args:
            manager: Optional :class:`CronManager`.  When ``None`` a default
                instance pointing at ``data/db/cron_jobs.db`` is created.
            notifier: Optional :class:`CronNotifier`.  When ``None`` a default
                instance is created from settings (sends to configured targets).
        """
        self._manager: CronManager = manager or CronManager()
        self._notifier: CronNotifier = notifier or CronNotifier()
        self._dts: DateTimeService = DateTimeService.get()
        scheduler_tz = self._dts.get_scheduler_tz()
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone=scheduler_tz)
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Load active jobs from CronManager and start the APScheduler scheduler.

        All jobs whose ``status`` is ``"active"`` are registered with
        APScheduler via a :class:`~apscheduler.triggers.cron.CronTrigger`
        derived from the job's cron expression.  Paused jobs are skipped.

        The underlying :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler`
        is started after all jobs have been registered.
        """
        jobs = self._manager.list_jobs()
        active_jobs = [j for j in jobs if j.status == "active"]

        logger.info(
            "cron_executor_loading_jobs",
            total=len(jobs),
            active=len(active_jobs),
        )

        for job in active_jobs:
            self._register_job(job)

        self._loop = asyncio.get_running_loop()
        self._scheduler.start()
        global _running_executor
        _running_executor = self
        logger.info("cron_executor_started")

    async def stop(self) -> None:
        """Gracefully shut down the APScheduler scheduler.

        Calls :meth:`~apscheduler.schedulers.asyncio.AsyncIOScheduler.shutdown`
        with ``wait=False`` so the event loop is not blocked while in-flight
        jobs complete.
        """
        global _running_executor
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("cron_executor_stopped")
        _running_executor = None

    # ── Job management ────────────────────────────────────────────────────────

    async def add_job(self, job: CronJob) -> None:
        """Add a job to the running scheduler.

        If a job with the same name already exists in the scheduler it is
        replaced (``replace_existing=True``).

        Args:
            job: The :class:`~lightagent.scheduler.cron_manager.CronJob` to
                schedule.
        """
        self._register_job(job)
        logger.info("cron_executor_job_added", name=job.name, schedule=job.schedule)

    async def remove_job(self, name: str) -> None:
        """Remove a job from the running scheduler.

        If the job is not found in the scheduler (e.g. it was never registered
        or already removed) this method returns without raising an error.

        Args:
            name: The job name (APScheduler job id) to remove.
        """
        try:
            self._scheduler.remove_job(name)
            logger.info("cron_executor_job_removed", name=name)
        except APJobLookupError:
            logger.debug("cron_executor_job_not_found_on_remove", name=name)

    async def pause_job(self, name: str) -> None:
        """Pause a job in the running scheduler.

        Args:
            name: The job name (APScheduler job id) to pause.
        """
        try:
            self._scheduler.pause_job(name)
            logger.info("cron_executor_job_paused", name=name)
        except APJobLookupError:
            logger.warning("cron_executor_pause_job_not_found", name=name)

    async def resume_job(self, name: str) -> None:
        """Resume a previously paused job in the running scheduler.

        Args:
            name: The job name (APScheduler job id) to resume.
        """
        try:
            self._scheduler.resume_job(name)
            logger.info("cron_executor_job_resumed", name=name)
        except APJobLookupError:
            logger.warning("cron_executor_resume_job_not_found", name=name)

    def schedule_coroutine(self, coro: object) -> None:
        """Submit a coroutine to the executor's event loop from any thread.

        Uses :func:`asyncio.run_coroutine_threadsafe` so that cron tools
        (which run as sync callables inside a thread-pool executor) can
        safely hand off async work to the running event loop without needing
        a reference to the loop themselves.

        If the executor has not been started yet (loop is ``None``) the
        coroutine is discarded and a warning is logged.

        Args:
            coro: The coroutine to schedule (e.g. ``executor.add_job(job)``).
        """
        if self._loop is None or not self._loop.is_running():
            logger.warning("cron_executor_schedule_coroutine_no_loop")
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _register_job(self, job: CronJob) -> None:
        """Register a single CronJob with the APScheduler instance.

        One-time jobs whose schedule starts with ``"once:"`` use an
        APScheduler ``date`` trigger; recurring jobs use
        :meth:`~apscheduler.triggers.cron.CronTrigger.from_crontab`.

        Both trigger types receive an explicit timezone so APScheduler never
        silently inherits the OS locale (Phase 28 / CLAUDE.md rule).

        Args:
            job: The :class:`~lightagent.scheduler.cron_manager.CronJob` to
                register.
        """
        # Resolve the operative timezone for this job: per-job → system chain.
        job_tz = (
            self._dts.resolve_timezone(job.timezone)
            if job.timezone
            else self._dts.get_scheduler_tz()
        )

        if job.schedule.startswith("once:"):
            from datetime import datetime

            from apscheduler.triggers.date import DateTrigger

            # fromisoformat handles both '2026-03-09 20:51:06' (space) and
            # '2026-03-09T20:51:06' (T) regardless of how the value was stored.
            naive_run_date = datetime.fromisoformat(job.schedule[len("once:"):])
            # Attach the job timezone so APScheduler fires at the right instant.
            # BUG-001 fix: attach timezone before comparing — using UTC-aware now()
            # prevents wrong expiry decisions when the OS clock is not in UTC.
            run_date = naive_run_date.replace(tzinfo=job_tz)
            if run_date <= self._dts.now(job_tz):
                logger.warning(
                    "cron_executor_skipping_expired_once_job",
                    name=job.name,
                    run_date=str(run_date),
                )
                import contextlib

                with contextlib.suppress(Exception):
                    self._manager.remove(job.name)
                return
            trigger = DateTrigger(run_date=run_date)
        else:
            trigger = CronTrigger.from_crontab(job.schedule, timezone=job_tz)

        self._scheduler.add_job(
            self._run_job,
            trigger,
            id=job.name,
            args=[job.name, job.task],
            replace_existing=True,
        )
        logger.debug(
            "cron_executor_job_registered", name=job.name, schedule=job.schedule
        )

    async def _run_job(self, name: str, task: str) -> None:
        """Execute a cron job and apply the retry policy on failure.

        On success: records history, resets ``retry_count`` to 0.

        On failure:

        - If ``retry_count < max_retries``: increments ``retry_count``,
          schedules a one-shot APScheduler ``date`` trigger after
          ``retry_delay_seconds * 2^(retry_count-1)`` seconds, and does NOT
          send a notification yet.
        - If retries are exhausted (or ``max_retries == 0``): resets
          ``retry_count`` to 0, records the run as failed, and fires the
          :class:`~lightagent.scheduler.notifier.CronNotifier`.

        Errors are never re-raised so APScheduler continues scheduling
        future runs.

        Args:
            name: The cron job name (used for the thread id and logging).
            task: The human-readable task description sent to the agent graph.
        """
        from datetime import UTC, datetime, timedelta

        from langchain_core.messages import HumanMessage

        from lightagent.agents.graph import get_async_compiled_graph
        from lightagent.scheduler.heartbeat_delivery import HeartbeatDelivery

        logger.info("cron_job_starting", name=name, task=task)
        started_at = datetime.now(UTC).replace(tzinfo=None)

        try:
            thread_id = f"cron-{name}-{started_at.strftime('%Y%m%d%H%M%S')}"
            graph = await get_async_compiled_graph()
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={"configurable": {"thread_id": thread_id}},
            )
            finished_at = datetime.now(UTC).replace(tzinfo=None)

            # Extract last AI message text as the run output
            output: str | None = None
            messages = result.get("messages", [])
            if messages:
                last = messages[-1]
                output = getattr(last, "content", None)
                if isinstance(output, list):
                    output = next(
                        (
                            b.get("text")
                            for b in output
                            if isinstance(b, dict) and "text" in b
                        ),
                        str(output),
                    )

            self._manager.update_last_run(name, finished_at)
            self._manager.set_retry_count(name, 0)
            self._manager.add_run_record(
                job_name=name,
                started_at=started_at,
                finished_at=finished_at,
                outcome="success",
                output=str(output) if output is not None else None,
            )
            logger.info("cron_job_completed", name=name)

            # Send success notification (no-op when no channels are configured)
            await self._notifier.notify_success(job_name=name, output=output)

            # Proactive delivery: send output to the job's configured channel
            completed_job = self._manager.get_job(name)
            if (
                completed_job is not None
                and completed_job.output_channel
                and completed_job.output_target
            ):
                try:
                    await HeartbeatDelivery().send(
                        completed_job.output_channel,
                        completed_job.output_target,
                        str(output)
                        if output is not None
                        else f"Job '{name}' completed.",
                    )
                except Exception as delivery_exc:
                    logger.warning(
                        "cron_job_delivery_failed",
                        name=name,
                        error=str(delivery_exc),
                    )

            # Auto-remove one-shot jobs after they fire
            if completed_job is not None and completed_job.schedule.startswith("once:"):
                import contextlib

                with contextlib.suppress(Exception):
                    self._manager.remove(name)

        except Exception as exc:
            finished_at = datetime.now(UTC).replace(tzinfo=None)
            error_msg = str(exc)
            duration = (finished_at - started_at).total_seconds()
            logger.error("cron_job_failed", name=name, error=error_msg)

            # Always record the failed run
            self._manager.add_run_record(
                job_name=name,
                started_at=started_at,
                finished_at=finished_at,
                outcome="failure",
                error=error_msg,
            )

            # Apply retry policy
            job = self._manager.get_job(name)
            if job is not None and job.retry_count < job.max_retries:
                new_count = job.retry_count + 1
                delay = job.retry_delay_seconds * (2 ** (new_count - 1))
                self._manager.set_retry_count(name, new_count)
                retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                self._scheduler.add_job(
                    self._run_job,
                    trigger="date",
                    run_date=retry_at,
                    id=f"{name}_retry_{new_count}",
                    args=[name, task],
                    replace_existing=True,
                )
                logger.info(
                    "cron_job_retry_scheduled",
                    name=name,
                    attempt=new_count,
                    max_retries=job.max_retries,
                    delay_seconds=delay,
                )
            else:
                # Retries exhausted (or max_retries == 0) — notify and reset
                self._manager.set_retry_count(name, 0)
                await self._notifier.notify_failure(
                    job_name=name,
                    error=error_msg,
                    duration_seconds=duration,
                )


__all__ = ["CronExecutor", "get_running_executor"]
