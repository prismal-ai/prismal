"""SubAgentSpawner — async task launcher with concurrency and timeout control.

Inspired by the picoClaw ``spawn`` tool concept: launch sub-agents as
independent async tasks, enforce a concurrency limit, and cancel tasks
that exceed their timeout.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from prismal.core.config import Settings, get_settings
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = get_logger("lightagent.agents.spawner")


class SubAgentSpawner:
    """Async sub-agent task launcher with concurrency and timeout enforcement.

    Attributes:
        max_concurrent: Maximum number of simultaneously active tasks.
        default_timeout: Default task timeout in seconds.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the spawner.

        Args:
            settings: Application settings instance. If None, the global
                cached settings are used via ``get_settings()``.
        """
        _settings = settings or get_settings()
        self.max_concurrent: int = _settings.max_concurrent_agents
        self.default_timeout: float = float(_settings.agent_timeout_seconds)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self.max_concurrent)
        # Any: tasks have heterogeneous return types depending on the coroutine
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def active_count(self) -> int:
        """Return the number of currently active (non-done) tasks."""
        return sum(1 for t in self._active_tasks.values() if not t.done())

    async def spawn(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        task_id: str,
        timeout: float | None = None,
    ) -> asyncio.Task[Any]:  # Any: return type is determined by the caller's coroutine
        """Launch a coroutine as an async task with concurrency and timeout control.

        Acquires the semaphore before launching. Releases it when the task
        finishes, times out, is cancelled, or raises an unexpected error.

        Args:
            coro_factory: Zero-argument callable that returns a coroutine.
            task_id: Unique identifier for this task.
            timeout: Task timeout in seconds. ``None`` uses ``default_timeout``.

        Returns:
            The launched :class:`asyncio.Task`.
        """
        _timeout = timeout if timeout is not None else self.default_timeout

        await self._semaphore.acquire()
        logger.info("spawning_sub_agent", task_id=task_id, timeout=_timeout)

        async def _wrapped() -> object:
            try:
                return await asyncio.wait_for(coro_factory(), timeout=_timeout)
            except TimeoutError:
                logger.warning("sub_agent_timeout", task_id=task_id, timeout=_timeout)
                raise
            except asyncio.CancelledError:
                logger.info("sub_agent_cancelled", task_id=task_id)
                raise
            except Exception as exc:
                logger.error("sub_agent_error", task_id=task_id, error=str(exc))
                raise
            finally:
                self._semaphore.release()
                self._active_tasks.pop(task_id, None)

        # Any: return type matches the spawn() signature above
        task: asyncio.Task[Any] = asyncio.create_task(_wrapped(), name=task_id)
        self._active_tasks[task_id] = task
        return task

    def cancel_all(self) -> int:
        """Cancel all active tasks.

        Returns:
            The number of tasks that were cancelled (i.e. were not already done).
        """
        cancelled = 0
        for task_id, task in list(self._active_tasks.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
                logger.info("sub_agent_force_cancelled", task_id=task_id)
        return cancelled

    def cleanup(self) -> None:
        """Remove completed tasks from the internal registry."""
        done_ids = [tid for tid, t in self._active_tasks.items() if t.done()]
        for tid in done_ids:
            del self._active_tasks[tid]


__all__ = ["SubAgentSpawner"]
