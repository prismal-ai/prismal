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

import structlog
from apscheduler.jobstores.base import JobLookupError as APJobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lightagent.scheduler.cron_manager import CronJob, CronManager

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

    def __init__(self, manager: CronManager | None = None) -> None:
        """Initialise the executor with an optional CronManager.

        Args:
            manager: Optional :class:`CronManager`.  When ``None`` a default
                instance pointing at ``data/db/cron_jobs.db`` is created.
        """
        self._manager: CronManager = manager or CronManager()
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler()

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

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _register_job(self, job: CronJob) -> None:
        """Register a single CronJob with the APScheduler instance.

        Uses :meth:`~apscheduler.triggers.cron.CronTrigger.from_crontab` to
        parse the standard 5-field cron expression stored on the job.

        Args:
            job: The :class:`~lightagent.scheduler.cron_manager.CronJob` to
                register.
        """
        self._scheduler.add_job(
            self._run_job,
            CronTrigger.from_crontab(job.schedule),
            id=job.name,
            args=[job.name, job.task],
            replace_existing=True,
        )
        logger.debug(
            "cron_executor_job_registered", name=job.name, schedule=job.schedule
        )

    async def _run_job(self, name: str, task: str) -> None:
        """Execute a cron job by invoking the LangGraph agent graph.

        This is the function APScheduler calls on each scheduled tick.  It:

        1. Calls :func:`~lightagent.agents.graph.get_async_compiled_graph` to
           obtain (or reuse) the compiled LangGraph graph.
        2. Invokes the graph via ``ainvoke`` with the job ``task`` as the user
           message and ``cron-{name}`` as the thread id.
        3. Calls :meth:`~lightagent.scheduler.cron_manager.CronManager.update_last_run`
           to record the successful execution timestamp.

        Errors are logged but never re-raised so that APScheduler continues
        scheduling future runs.

        Args:
            name: The cron job name (used for the thread id and logging).
            task: The human-readable task description sent to the agent graph.
        """
        logger.info("cron_job_starting", name=name, task=task)
        try:
            from datetime import UTC, datetime

            from langchain_core.messages import HumanMessage

            from lightagent.agents.graph import get_async_compiled_graph

            thread_id = f"cron-{name}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            graph = await get_async_compiled_graph()
            await graph.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={"configurable": {"thread_id": thread_id}},
            )
            self._manager.update_last_run(name)
            logger.info("cron_job_completed", name=name)
        except Exception as exc:
            logger.error(
                "cron_job_failed",
                name=name,
                error=str(exc),
            )


__all__ = ["CronExecutor", "get_running_executor"]
