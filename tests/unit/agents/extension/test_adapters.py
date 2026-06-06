"""Tests for LangChainRunnableAdapter (X5, SPEC-EXT-005)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from prismal.agents.extension.adapters import LangChainRunnableAdapter
from prismal.agents.state import create_initial_state
from prismal.core.exceptions import LangChainAdapterError


def _state(text: str = "hi"):
    s = create_initial_state(session_id="sess-adapter")
    s["messages"] = [HumanMessage(content=text)]
    return s


class TestInputMapping:
    async def test_messages_mapping(self) -> None:
        runnable = RunnableLambda(lambda msgs: AIMessage(content=f"echo:{msgs[-1].content}"))
        adapter = LangChainRunnableAdapter(runnable, input_mapping="messages")
        out = await adapter.ainvoke(_state("hola"))
        assert out["messages"][0].content == "echo:hola"

    async def test_input_dict_mapping(self) -> None:
        runnable = RunnableLambda(lambda d: d["input"].upper())
        adapter = LangChainRunnableAdapter(runnable, input_mapping="input_dict")
        out = await adapter.ainvoke(_state("hi"))
        assert out["messages"][0].content == "HI"

    async def test_auto_mapping_defaults_to_messages_for_plain_runnable(self) -> None:
        runnable = RunnableLambda(lambda msgs: AIMessage(content=str(len(msgs))))
        adapter = LangChainRunnableAdapter(runnable, input_mapping="auto")
        out = await adapter.ainvoke(_state("hi"))
        assert out["messages"][0].content == "1"


class TestOutputMapping:
    async def test_str_output_becomes_ai_message(self) -> None:
        adapter = LangChainRunnableAdapter(
            RunnableLambda(lambda msgs: "plain string"), input_mapping="messages"
        )
        out = await adapter.ainvoke(_state())
        assert isinstance(out["messages"][0], AIMessage)
        assert out["messages"][0].content == "plain string"

    async def test_dict_output_with_output_key(self) -> None:
        adapter = LangChainRunnableAdapter(
            RunnableLambda(lambda msgs: {"result": "answer", "noise": 1}),
            input_mapping="messages",
            output_key="result",
        )
        out = await adapter.ainvoke(_state())
        assert out["messages"][0].content == "answer"

    async def test_dict_output_default_output_key(self) -> None:
        adapter = LangChainRunnableAdapter(
            RunnableLambda(lambda msgs: {"output": "default-key"}),
            input_mapping="messages",
        )
        out = await adapter.ainvoke(_state())
        assert out["messages"][0].content == "default-key"


class TestAsNode:
    async def test_as_node_returns_prismal_node(self) -> None:
        adapter = LangChainRunnableAdapter(
            RunnableLambda(lambda msgs: AIMessage(content="ok")), input_mapping="messages"
        )
        node = adapter.as_node(name="legacy_research", capabilities=["research"])
        assert node.__prismal_node__.name == "legacy_research"
        assert node.__prismal_node__.capabilities == ("research",)
        out = await node(_state())
        assert out["messages"][0].content == "ok"


class TestErrors:
    def test_non_runnable_raises_adapter_error(self) -> None:
        with pytest.raises(LangChainAdapterError):
            LangChainRunnableAdapter(object())  # type: ignore[arg-type]

    def test_invalid_input_mapping_raises(self) -> None:
        with pytest.raises(LangChainAdapterError):
            LangChainRunnableAdapter(
                RunnableLambda(lambda x: x),
                input_mapping="bogus",  # type: ignore[arg-type]
            )

    async def test_runnable_failure_wrapped_as_adapter_error(self) -> None:
        def boom(_msgs):
            raise ValueError("runnable exploded")

        adapter = LangChainRunnableAdapter(RunnableLambda(boom), input_mapping="messages")
        with pytest.raises(LangChainAdapterError):
            await adapter.ainvoke(_state())


class TestAutoInputDict:
    async def test_auto_uses_input_dict_when_input_keys_present(self) -> None:
        # A Runnable exposing ``input_keys`` (AgentExecutor-like) → input_dict mapping.
        class _ExecutorLike(RunnableLambda):
            input_keys = ["input"]  # noqa: RUF012

        runnable = _ExecutorLike(lambda d: d["input"].upper())
        adapter = LangChainRunnableAdapter(runnable, input_mapping="auto")
        out = await adapter.ainvoke(_state("hey"))
        assert out["messages"][0].content == "HEY"
