"""Trajectory token capture from the run's LLM calls (TKE1-08 follow-up).

Internal LLM calls (supervisor routing) and rebuilt direct answers carry no
``usage_metadata`` in the ``updates`` stream, so a message-only count records 0
tokens on a live run. ``capture_trajectory`` therefore also observes every LLM
call of the run through a LangChain callback and prefers those totals.

These tests use a real (tiny) compiled LangGraph so callback propagation into
nodes that call the model *without* forwarding ``config`` is exercised for real.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from prismal.eval.trajectory import capture_trajectory
from prismal.eval.types import Assertion, AssertionType, EvalCase
from prismal.langgraph import END, START, AgentState, StateGraph


class _UsageChat(BaseChatModel):
    """A chat model double whose replies carry ``usage_metadata``."""

    prompt: int = 100
    completion: int = 20
    cache_read: int = 0

    @property
    def _llm_type(self) -> str:
        return "usage-fake"

    def _generate(self, messages: list[BaseMessage], *_: Any, **__: Any) -> ChatResult:
        details = {"cache_read": self.cache_read} if self.cache_read else {}
        message = AIMessage(
            content="route:rag_agent",
            usage_metadata={
                "input_tokens": self.prompt,
                "output_tokens": self.completion,
                "total_tokens": self.prompt + self.completion,
                "input_token_details": details,  # type: ignore[typeddict-item]
            },
            response_metadata={"model_name": "usage-fake"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _graph(llm: BaseChatModel, *, calls: int = 1) -> Any:
    """supervisor → END; the node calls *llm* without forwarding ``config``
    and returns a rebuilt answer that carries no usage (the live-run shape)."""

    async def supervisor(_state: AgentState) -> dict[str, Any]:
        for _ in range(calls):
            await llm.ainvoke("route this")
        return {"messages": [AIMessage(content="The hard cap aborts the run.")]}

    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor)
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", END)
    return builder.compile()


def _case() -> EvalCase:
    return EvalCase(
        id="u1",
        input="What does the budget hard cap do?",
        assertions=[Assertion(type=AssertionType.EXACT, expected="x")],
    )


async def test_internal_llm_calls_are_counted() -> None:
    """Tokens of LLM calls that never reach the stream are captured."""
    traj = await capture_trajectory(_graph(_UsageChat(), calls=2), _case())

    assert traj.terminated is True
    assert traj.final_answer == "The hard cap aborts the run."
    assert traj.tokens == 240
    assert traj.prompt_tokens == 200
    assert traj.llm_calls == 2
    assert traj.usage_source == "callbacks"


async def test_cache_read_tokens_are_counted() -> None:
    traj = await capture_trajectory(_graph(_UsageChat(cache_read=60)), _case())

    assert traj.cache_read_tokens == 60


async def test_no_llm_call_falls_back_to_message_usage() -> None:
    """With no observed LLM call the message-based counts are kept unchanged."""

    class _Replay:
        async def astream(self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"):
            message = AIMessage(
                content="answer",
                usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            )
            yield {"rag_agent": {"messages": [message]}}

    traj = await capture_trajectory(_Replay(), _case())

    assert traj.tokens == 10
    assert traj.llm_calls == 1
    assert traj.usage_source == "messages"


async def test_caller_callbacks_are_preserved() -> None:
    """A caller-supplied callback still sees the run (the capture handler is added)."""
    seen: list[str] = []

    class _Spy(BaseCallbackHandler):
        def on_llm_end(self, *_: Any, **__: Any) -> None:
            seen.append("end")

    config = {"callbacks": [_Spy()]}
    traj = await capture_trajectory(_graph(_UsageChat()), _case(), config=config)

    assert seen == ["end"]
    assert traj.tokens == 120
    assert config == {"callbacks": [config["callbacks"][0]]}  # caller config not mutated
