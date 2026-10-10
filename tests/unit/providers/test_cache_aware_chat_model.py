"""CacheAwareChatModel + registry wiring (Phase TKE — SPEC-TKE-CCH-003, EFF-001; ADR-WAVE-001)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM

from prismal.core.config import Settings
from prismal.providers.prompt_cache import CacheAwareChatModel, CachePolicy
from prismal.providers.registry import ProviderRegistry


class _Recorder(BaseChatModel):
    """Inner model double: records what the wrapper hands it."""

    calls: list[dict[str, Any]] = []
    model: str = "claude-sonnet-4-5"

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        msg = AIMessage(content="ok", response_metadata={"prismal_gateway": {"served": "x"}})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, None, **kwargs)


@tool
def zeta_tool(x: str) -> str:
    """Zeta does z things."""
    return x


@tool
def alpha_tool(x: str) -> str:
    """Alpha does a things."""
    return x


def _big_system() -> str:
    return "Static instruction. " * 600  # well above the 1024-token min prefix


def _wrapper(inner: _Recorder, **kw: Any) -> CacheAwareChatModel:
    return CacheAwareChatModel(inner=inner, policy=CachePolicy(), model_name=inner.model, **kw)


def test_anthropic_breakpoints_reach_inner_and_metadata_survives() -> None:
    inner = _Recorder(calls=[])
    wrapped = _wrapper(inner, agent="coder")
    out = wrapped.invoke([SystemMessage(content=_big_system()), HumanMessage(content="hi")])
    sent = inner.calls[0]["messages"][0]
    assert isinstance(sent.content, list)
    assert sent.content[-1]["cache_control"] == {"type": "ephemeral"}
    assert out.response_metadata["prismal_gateway"] == {"served": "x"}


async def test_async_path_applies_policy() -> None:
    inner = _Recorder(calls=[])
    await _wrapper(inner, agent="coder").ainvoke(
        [SystemMessage(content=_big_system()), HumanMessage(content="hi")]
    )
    assert isinstance(inner.calls[0]["messages"][0].content, list)


def test_bind_tools_sorts_by_name_and_marks_last_tool() -> None:
    inner = _Recorder(calls=[])
    bound = _wrapper(inner, agent="coder").bind_tools([zeta_tool, alpha_tool])
    bound.invoke([SystemMessage(content=_big_system()), HumanMessage(content="hi")])
    tools = inner.calls[0]["kwargs"]["tools"]
    assert [t["function"]["name"] for t in tools] == ["alpha_tool", "zeta_tool"]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_openai_family_passes_prompt_cache_key_kwarg() -> None:
    inner = _Recorder(calls=[], model="gpt-4o")
    _wrapper(inner, agent="coder").invoke(
        [SystemMessage(content=_big_system()), HumanMessage(content="hi")]
    )
    assert inner.calls[0]["kwargs"]["prompt_cache_key"].startswith("prismal:coder:")


def test_agent_resolved_from_langgraph_node_metadata() -> None:
    inner = _Recorder(calls=[], model="gpt-4o")
    _wrapper(inner).invoke(
        [SystemMessage(content=_big_system()), HumanMessage(content="hi")],
        config={"metadata": {"langgraph_node": "researcher"}},
    )
    assert inner.calls[0]["kwargs"]["prompt_cache_key"].startswith("prismal:researcher:")


def test_effort_kwargs_added_for_reasoning_models_only() -> None:
    effort = {"planner": "high"}
    inner = _Recorder(calls=[])
    _wrapper(inner, agent="planner", effort_by_agent=effort, apply_cache=False).invoke("hi")
    assert inner.calls[0]["kwargs"]["reasoning_effort"] == "high"

    gpt = _Recorder(calls=[], model="gpt-4o")
    _wrapper(gpt, agent="planner", effort_by_agent=effort, apply_cache=False).invoke("hi")
    assert "reasoning_effort" not in gpt.calls[0]["kwargs"]


def test_per_agent_max_tokens_applied() -> None:
    inner = _Recorder(calls=[], model="gpt-4o")
    _wrapper(inner, agent="coder", max_tokens_by_agent={"coder": 512}, apply_cache=False).invoke(
        "hi"
    )
    assert inner.calls[0]["kwargs"]["max_tokens"] == 512


# ── registry wiring ──────────────────────────────────────────────────────────


def _registry(**settings: Any) -> ProviderRegistry:
    return ProviderRegistry(settings=Settings(**settings))


def test_registry_master_off_returns_bare_chatlitellm() -> None:
    llm = _registry(prompt_cache_enabled=True).get_llm("claude-sonnet-4-5", agent="coder")
    assert type(llm) is ChatLiteLLM


def test_registry_wraps_when_prompt_cache_enabled() -> None:
    llm = _registry(token_efficiency_enabled=True, prompt_cache_enabled=True).get_llm(
        "claude-sonnet-4-5", agent="coder"
    )
    assert isinstance(llm, CacheAwareChatModel)
    assert type(llm.inner) is ChatLiteLLM
    assert llm.agent == "coder"


def test_registry_wraps_for_effort_only() -> None:
    llm = _registry(
        token_efficiency_enabled=True,
        llm_effort_enabled=True,
        llm_effort_profile="recommended",
    ).get_llm("claude-sonnet-4-5")
    assert isinstance(llm, CacheAwareChatModel)
    assert llm.apply_cache is False
    assert llm.effort_for("planner") == "high"
    assert llm.effort_for("supervisor") == "low"


def test_registry_explicit_effort_beats_profile() -> None:
    llm = _registry(
        token_efficiency_enabled=True,
        llm_effort_enabled=True,
        llm_effort_profile="recommended",
        llm_effort_by_agent={"planner": "medium"},
    ).get_llm("claude-sonnet-4-5")
    assert isinstance(llm, CacheAwareChatModel)
    assert llm.effort_for("planner") == "medium"


@pytest.mark.parametrize("flags", [{}, {"token_efficiency_enabled": True}])
def test_registry_contract_kwargs_identical_when_features_off(flags: dict[str, Any]) -> None:
    baseline = _registry().get_llm("claude-sonnet-4-5")
    candidate = _registry(**flags).get_llm("claude-sonnet-4-5", agent="coder")
    assert type(candidate) is ChatLiteLLM
    keys = ("model", "streaming", "temperature", "max_tokens", "request_timeout", "max_retries")
    assert {k: getattr(candidate, k) for k in keys} == {k: getattr(baseline, k) for k in keys}
    assert candidate.model_kwargs == baseline.model_kwargs


def test_cache_control_reaches_the_litellm_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through a real ChatLiteLLM (TKE0-03): blocks survive message conversion."""
    captured: dict[str, Any] = {}

    def fake_completion(self: ChatLiteLLM, **params: Any) -> dict[str, Any]:
        captured.update(params)
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(ChatLiteLLM, "completion_with_retry", fake_completion)
    llm = _registry(token_efficiency_enabled=True, prompt_cache_enabled=True).get_llm(
        "claude-sonnet-4-5", agent="coder"
    )
    llm.bind_tools([zeta_tool, alpha_tool]).invoke(
        [SystemMessage(content=_big_system()), HumanMessage(content="hi")]
    )
    system = captured["messages"][0]
    assert system["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert [t["function"]["name"] for t in captured["tools"]] == ["alpha_tool", "zeta_tool"]
    assert captured["tools"][-1]["cache_control"] == {"type": "ephemeral"}
