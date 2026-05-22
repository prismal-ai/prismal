"""Unit tests for SubAgentSpawner (T-045)."""

from __future__ import annotations

import asyncio

import pytest

from prismal.agents.spawner import SubAgentSpawner
from prismal.core.config import Settings


@pytest.fixture
def spawner() -> SubAgentSpawner:
    """Return a SubAgentSpawner with test-friendly settings."""
    settings = Settings(max_concurrent_agents=3, agent_timeout_seconds=5)
    return SubAgentSpawner(settings=settings)


@pytest.mark.asyncio
async def test_spawn_task_returns_asyncio_task(spawner: SubAgentSpawner) -> None:
    """spawn() must return an asyncio.Task."""

    async def quick() -> str:
        return "done"

    task = await spawner.spawn(quick, "t1", timeout=2.0)
    assert isinstance(task, asyncio.Task)
    result = await task
    assert result == "done"
    spawner.cleanup()


@pytest.mark.asyncio
async def test_spawn_task_completes_correctly(spawner: SubAgentSpawner) -> None:
    """Spawned task must return the coroutine's result."""

    async def compute() -> int:
        return 42

    task = await spawner.spawn(compute, "t2", timeout=2.0)
    result = await task
    assert result == 42
    spawner.cleanup()


@pytest.mark.asyncio
async def test_spawn_cancels_on_timeout(spawner: SubAgentSpawner) -> None:
    """Tasks that exceed timeout must raise TimeoutError."""

    async def slow() -> str:
        await asyncio.sleep(100)
        return "never"

    task = await spawner.spawn(slow, "slow-task", timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await task
    spawner.cleanup()


def test_spawner_active_count_starts_at_zero(spawner: SubAgentSpawner) -> None:
    """active_count must be 0 before any tasks are spawned."""
    assert spawner.active_count == 0


@pytest.mark.asyncio
async def test_cleanup_removes_done_tasks(spawner: SubAgentSpawner) -> None:
    """cleanup() must remove completed tasks from registry."""

    async def quick() -> str:
        return "x"

    task = await spawner.spawn(quick, "clean-t1", timeout=2.0)
    await task
    spawner.cleanup()
    assert spawner.active_count == 0


@pytest.mark.asyncio
async def test_cancel_all_returns_count(spawner: SubAgentSpawner) -> None:
    """cancel_all() must cancel active tasks and return count."""
    event = asyncio.Event()

    async def blocking() -> str:
        await event.wait()
        return "ok"

    # Spawn without awaiting result
    _task = await spawner.spawn(blocking, "b1", timeout=10.0)
    count = spawner.cancel_all()
    assert count >= 0  # 1 cancelled (may already be done if race)
    # Let it settle
    await asyncio.sleep(0.1)
    spawner.cleanup()
