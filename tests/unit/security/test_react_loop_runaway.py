"""react_loop runaway integration (Phase H — H5-02 / SPEC-HRD-RUN-001)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.tool_registry import react_loop
from prismal.core.config import Settings
from prismal.security.runaway import RunawayGuard


class _Tool:
    name = "search"

    async def ainvoke(self, _args: object) -> str:
        return "same result every time"


class _LoopingLLM:
    """Always requests the same tool call — never finalizes on its own."""

    def __init__(self) -> None:
        self.calls = 0
        self.model = "fake/model"

    async def ainvoke(self, _messages: object) -> AIMessage:
        self.calls += 1
        msg = AIMessage(content="working")
        msg.tool_calls = [{"name": "search", "args": {"q": "x"}, "id": f"tc{self.calls}"}]  # type: ignore[attr-defined]
        return msg


async def test_stagnation_stops_loop_with_partial() -> None:
    guard = RunawayGuard(
        settings=Settings(
            hardening_enabled=True,
            hardening_runaway_max_steps=0,
            hardening_runaway_stagnation_window=3,
        )
    )
    llm = _LoopingLLM()

    result = await react_loop(
        llm,
        [_Tool()],
        [HumanMessage(content="go")],
        agent_name="researcher",
        runaway_guard=guard,
        max_iterations=50,
    )

    # The guard cuts the loop off long before max_iterations.
    assert llm.calls <= 4
    assert isinstance(result, AIMessage)
    assert "stopped" in str(result.content).lower() or "runaway" in str(result.content).lower()


async def test_step_cap_stops_loop() -> None:
    guard = RunawayGuard(
        settings=Settings(
            hardening_enabled=True,
            hardening_runaway_max_steps=3,
            hardening_runaway_stagnation_window=0,
        )
    )
    llm = _LoopingLLM()

    await react_loop(
        llm,
        [_Tool()],
        [HumanMessage(content="go")],
        agent_name="researcher",
        runaway_guard=guard,
        max_iterations=50,
    )
    assert llm.calls <= 4  # step cap of 3 bounds it


async def test_no_runaway_guard_is_unchanged() -> None:
    llm = _LoopingLLM()
    await react_loop(llm, [_Tool()], [HumanMessage(content="go")], max_iterations=5)
    # Without a guard, the loop runs to max_iterations (+1 final synthesis call).
    assert llm.calls == 6
