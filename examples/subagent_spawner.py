"""SubAgentSpawner demo: fan out fake workers with concurrency + timeouts.

Spawns deterministic fake worker coroutines (no LLM, no network) through the
spawner and shows its three control features: the concurrency semaphore
(``max_concurrent_agents``), per-task timeouts, and ``cancel_all()``. Uses an
explicit ``Settings`` instance so the demo is self-contained.

Run::

    python examples/subagent_spawner.py
"""

from __future__ import annotations

import asyncio

from prismal.agents.spawner import SubAgentSpawner
from prismal.core.config import Settings


async def fake_worker(name: str, duration: float) -> str:
    """Deterministic stand-in for a sub-agent run (sleeps, then reports)."""
    await asyncio.sleep(duration)
    return f"{name}: analysed its shard in {duration:.2f}s"


async def demo_fan_out(spawner: SubAgentSpawner) -> None:
    """Spawn four workers under a two-slot semaphore and collect results."""
    print(f"-- Fan-out (max_concurrent={spawner.max_concurrent}) --")
    tasks = []
    for i in range(1, 5):
        # spawn() awaits the semaphore: workers 3 and 4 only launch once an
        # earlier worker frees a slot.
        task = await spawner.spawn(
            coro_factory=lambda i=i: fake_worker(f"worker-{i}", 0.05 * i),
            task_id=f"worker-{i}",
        )
        tasks.append(task)
        print(f"  spawned worker-{i} (active={spawner.active_count})")

    results = await asyncio.gather(*tasks)
    for result in results:
        print(f"  result: {result}")


async def demo_timeout(spawner: SubAgentSpawner) -> None:
    """A worker that overruns its timeout is cancelled with TimeoutError."""
    print("\n-- Timeout enforcement --")
    task = await spawner.spawn(
        coro_factory=lambda: fake_worker("slow-worker", 5.0),
        task_id="slow-worker",
        timeout=0.1,
    )
    try:
        await task
    except TimeoutError:
        print("  slow-worker exceeded its 0.1s timeout and was cancelled")


async def demo_cancel_all(spawner: SubAgentSpawner) -> None:
    """cancel_all() force-cancels every task that is still running."""
    print("\n-- cancel_all --")
    tasks = [
        await spawner.spawn(
            coro_factory=lambda i=i: fake_worker(f"long-{i}", 30.0),
            task_id=f"long-{i}",
            timeout=60.0,
        )
        for i in (1, 2)
    ]
    cancelled = spawner.cancel_all()
    print(f"  cancelled {cancelled} running task(s)")

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    for task, outcome in zip(tasks, outcomes, strict=True):
        print(f"  {task.get_name()}: {type(outcome).__name__}")


async def main() -> None:
    """Run the three spawner demos with an explicit Settings instance."""
    settings = Settings(max_concurrent_agents=2, agent_timeout_seconds=300)
    spawner = SubAgentSpawner(settings=settings)

    await demo_fan_out(spawner)
    await demo_timeout(spawner)
    await demo_cancel_all(spawner)

    spawner.cleanup()
    print(f"\nDone. Active tasks after cleanup: {spawner.active_count}")


if __name__ == "__main__":
    asyncio.run(main())
