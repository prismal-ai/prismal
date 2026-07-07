"""Loop hardening demo with injected fakes (Phase LH).

1. ``ContextCompactor`` (LH1) trims a 200-message history down to a verbatim tail.
2. ``resolve_phase`` + ``CompositeToolProvider`` phase narrowing (LH2) shrinks
   the tool catalogue between a "planning" and an "executing" call.

Run::

    python examples/loop_hardening.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.context_compaction import ContextCompactor
from prismal.agents.extension.providers import CompositeToolProvider, FakeToolProvider
from prismal.agents.loop_phase import resolve_phase
from prismal.agents.state import create_initial_state
from prismal.core.config import Settings


async def _demo_context_compaction() -> None:
    print("── LH1: ContextCompactor ──\n")
    settings = Settings(context_compaction_max_messages=60, context_compaction_keep_recent=10)
    compactor = ContextCompactor(settings=settings)

    history = [HumanMessage(content=f"turn {i}", id=f"id{i}") for i in range(200)]
    should, reason = compactor.should_compact(history)
    print(f"  200 messages, max=60 -> should_compact={should} ({reason})")

    result = await compactor.compact(history)
    print(
        f"  compacted={result.compacted} strategy={result.strategy.value} dropped={result.messages_dropped}"
    )
    update = compactor.to_state_update(result)
    print(f"  state update carries {len(update['messages'])} RemoveMessage entries\n")


def _demo_tool_gating() -> None:
    print("── LH2: dynamic tool gating by phase ──\n")
    state = create_initial_state(session_id="demo")
    state["task_plan"] = ["scaffold", "implement", "test"]
    state["pending_tasks"] = ["scaffold", "implement", "test"]
    planning_phase = resolve_phase(state)

    state["completed_tasks"] = ["scaffold"]
    state["pending_tasks"] = ["implement", "test"]
    executing_phase = resolve_phase(state)

    print(f"  before any task started -> phase={planning_phase!r}")
    print(f"  after one task completes -> phase={executing_phase!r}")

    provider = FakeToolProvider({"coder": []}, default=[])
    composite = CompositeToolProvider(
        [provider], phase_capability_map={"coder": {"planning": ["general"]}}
    )
    composite.get_tools(
        agent_name="coder", capabilities=["general", "code_execution"], phase="planning"
    )
    composite.get_tools(
        agent_name="coder", capabilities=["general", "code_execution"], phase="executing"
    )
    print("  (see docs/loop-hardening.md for the full narrowing contract)\n")


async def main() -> None:
    await _demo_context_compaction()
    _demo_tool_gating()


if __name__ == "__main__":
    asyncio.run(main())
