"""Tests for the provider prompt-cache policy (Phase TKE — SPEC-TKE-CCH-001/002)."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from prismal.core.config import Settings
from prismal.providers.prompt_cache import (
    CacheApplied,
    CacheFamily,
    CachePolicy,
    apply_cache_policy,
    detect_cache_family,
    load_cache_policy,
)
from prismal.utils.stable_json import stable_dumps

REPO_ROOT = Path(__file__).resolve().parents[3]
EPHEMERAL = {"type": "ephemeral"}
EPHEMERAL_1H = {"type": "ephemeral", "ttl": "1h"}

# --------------------------------------------------------------------------- helpers

BIG_SYSTEM = "You are a meticulous assistant. " * 400  # ~3200 tokens estimated


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": "search", "description": "web search", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "calc", "description": "calculator", "parameters": {}},
        },
    ]


def _messages() -> list[BaseMessage]:
    return [
        SystemMessage(content=BIG_SYSTEM),
        HumanMessage(content="first question"),
        AIMessage(content="first answer"),
        HumanMessage(content="second question"),
    ]


def _policy(**kw: Any) -> CachePolicy:
    base: dict[str, Any] = {"min_prefix_tokens": {}}
    base.update(kw)
    return CachePolicy(**base)


def _apply(
    messages: list[BaseMessage] | None = None,
    tools: list[dict[str, Any]] | None = None,
    *,
    model: str = "claude-sonnet-4-5",
    policy: CachePolicy | None = None,
    tenant: str | None = None,
) -> CacheApplied:
    return apply_cache_policy(
        _messages() if messages is None else messages,
        _tools() if tools is None else tools,
        model=model,
        agent="coder",
        policy=policy or _policy(),
        tenant=tenant,
    )


def _has_cc(msg: BaseMessage) -> bool:
    content = msg.content
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and "cache_control" in b for b in content)


# --------------------------------------------------------------------------- family detection


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-sonnet-4-5", CacheFamily.ANTHROPIC_EXPLICIT),
        ("Claude-3-Haiku", CacheFamily.ANTHROPIC_EXPLICIT),
        ("anthropic/claude-opus-4", CacheFamily.ANTHROPIC_EXPLICIT),
        ("openrouter/anthropic/claude-sonnet-4-5", CacheFamily.ANTHROPIC_EXPLICIT),
        ("bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0", CacheFamily.ANTHROPIC_EXPLICIT),
        ("vertex_ai/claude-3-5-sonnet@20240620", CacheFamily.ANTHROPIC_EXPLICIT),
        ("gpt-4o", CacheFamily.OPENAI_AUTO),
        ("o3-mini", CacheFamily.OPENAI_AUTO),
        ("o1", CacheFamily.OPENAI_AUTO),
        ("o4-mini", CacheFamily.OPENAI_AUTO),
        ("openai/gpt-4.1", CacheFamily.OPENAI_AUTO),
        ("openrouter/openai/gpt-4o", CacheFamily.OPENAI_AUTO),
        ("azure/my-deployment", CacheFamily.OPENAI_AUTO),
        ("gemini-2.5-pro", CacheFamily.GEMINI_IMPLICIT),
        ("gemini/gemini-2.0-flash", CacheFamily.GEMINI_IMPLICIT),
        ("vertex_ai/gemini-1.5-pro", CacheFamily.GEMINI_IMPLICIT),
        ("openrouter/google/gemini-2.5-flash", CacheFamily.GEMINI_IMPLICIT),
        ("deepseek-chat", CacheFamily.DEEPSEEK_AUTO),
        ("deepseek/deepseek-reasoner", CacheFamily.DEEPSEEK_AUTO),
        ("openrouter/deepseek/deepseek-chat", CacheFamily.DEEPSEEK_AUTO),
        ("ollama/llama3", CacheFamily.NONE),
        ("mistral/mistral-large", CacheFamily.NONE),
        ("", CacheFamily.NONE),
    ],
)
def test_detect_cache_family(model: str, family: CacheFamily) -> None:
    assert detect_cache_family(model) is family


# --------------------------------------------------------------------------- policy loading


def test_load_cache_policy_from_real_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    policy = load_cache_policy(Settings())
    assert policy.ttl == "5m"
    assert policy.max_breakpoints == 4
    assert policy.lookback_blocks == 20
    assert policy.openai_retention is None
    assert policy.scope == "tenant"
    assert policy.min_prefix_tokens["anthropic_explicit"] == 1024
    assert policy.min_prefix_tokens["claude-haiku"] == 2048
    assert policy.min_prefix_tokens["deepseek_auto"] == 64


def test_load_cache_policy_from_tmp_yaml(tmp_path: Path) -> None:
    path = tmp_path / "pc.yaml"
    path.write_text(
        "ttl: '1h'\nmax_breakpoints: 2\nlookback_blocks: 5\nopenai_retention: '24h'\n"
        "min_prefix_tokens:\n  openai_auto: 10\n",
        encoding="utf-8",
    )
    policy = load_cache_policy(Settings(prompt_cache_policy_path=str(path)))
    # YAML ttl is used when the setting was not explicitly provided.
    assert policy.ttl == "1h"
    assert policy.max_breakpoints == 2
    assert policy.lookback_blocks == 5
    assert policy.openai_retention == "24h"
    assert dict(policy.min_prefix_tokens) == {"openai_auto": 10}


def test_load_cache_policy_missing_file_defaults(tmp_path: Path) -> None:
    policy = load_cache_policy(
        Settings(prompt_cache_policy_path=str(tmp_path / "does-not-exist.yaml"))
    )
    assert policy == CachePolicy(scope="tenant")


def test_load_cache_policy_invalid_yaml_defaults(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("ttl: [unclosed\n  - : :", encoding="utf-8")
    policy = load_cache_policy(Settings(prompt_cache_policy_path=str(path)))
    assert policy.max_breakpoints == 4
    assert policy.ttl == "5m"


def test_load_cache_policy_settings_override(tmp_path: Path) -> None:
    path = tmp_path / "pc.yaml"
    path.write_text("ttl: '5m'\n", encoding="utf-8")
    policy = load_cache_policy(
        Settings(
            prompt_cache_policy_path=str(path), prompt_cache_ttl="1h", prompt_cache_scope="global"
        )
    )
    assert policy.ttl == "1h"
    assert policy.scope == "global"


def test_min_prefix_for_model_key_wins() -> None:
    policy = _policy(min_prefix_tokens={"anthropic_explicit": 1024, "claude-haiku": 2048})
    fam = CacheFamily.ANTHROPIC_EXPLICIT
    assert policy.min_prefix_for("anthropic/claude-haiku-4-5", fam) == 2048
    assert policy.min_prefix_for("claude-sonnet-4-5", fam) == 1024
    assert policy.min_prefix_for("gpt-4o", CacheFamily.OPENAI_AUTO) == 0


# --------------------------------------------------------------------------- Anthropic placement


def test_anthropic_places_three_breakpoints() -> None:
    result = _apply()
    assert result.family is CacheFamily.ANTHROPIC_EXPLICIT
    assert result.breakpoints == 3
    assert result.request_kwargs == {}
    # last tool only
    assert result.tools[-1]["cache_control"] == EPHEMERAL
    assert "cache_control" not in result.tools[0]
    # system converted to one text block
    sys_content = result.messages[0].content
    assert sys_content == [{"type": "text", "text": BIG_SYSTEM, "cache_control": EPHEMERAL}]
    # last stable message = the AI message before the final human
    assert result.messages[2].content == [
        {"type": "text", "text": "first answer", "cache_control": EPHEMERAL}
    ]
    assert not _has_cc(result.messages[1])
    assert result.messages[3].content == "second question"
    assert isinstance(result.messages[2], AIMessage)


def test_anthropic_system_list_content_marks_last_text_block() -> None:
    msgs = _messages()
    msgs[0] = SystemMessage(
        content=[{"type": "text", "text": BIG_SYSTEM}, {"type": "text", "text": "tail"}]
    )
    result = _apply(msgs)
    content = result.messages[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": BIG_SYSTEM}
    assert content[1] == {"type": "text", "text": "tail", "cache_control": EPHEMERAL}


def test_anthropic_ttl_1h_shape() -> None:
    result = _apply(policy=_policy(ttl="1h"))
    assert result.tools[-1]["cache_control"] == EPHEMERAL_1H
    sys_content = result.messages[0].content
    assert isinstance(sys_content, list)
    assert sys_content[-1]["cache_control"] == EPHEMERAL_1H


def test_anthropic_no_message_breakpoint_without_prior_message() -> None:
    msgs: list[BaseMessage] = [SystemMessage(content=BIG_SYSTEM), HumanMessage(content="hi")]
    result = _apply(msgs)
    assert result.breakpoints == 2
    assert result.messages[1].content == "hi"


def test_max_breakpoints_one_keeps_tool_only() -> None:
    result = _apply(policy=_policy(max_breakpoints=1))
    assert result.breakpoints == 1
    assert result.tools[-1]["cache_control"] == EPHEMERAL
    assert not _has_cc(result.messages[0])
    assert not _has_cc(result.messages[2])


def test_max_breakpoints_two_keeps_tool_and_system() -> None:
    result = _apply(policy=_policy(max_breakpoints=2))
    assert result.breakpoints == 2
    assert result.tools[-1]["cache_control"] == EPHEMERAL
    assert _has_cc(result.messages[0])
    assert not _has_cc(result.messages[2])


def test_lookback_moves_message_breakpoint_forward() -> None:
    msgs: list[BaseMessage] = [
        SystemMessage(content=BIG_SYSTEM),
        AIMessage(content="old"),
        HumanMessage(content="final"),
        AIMessage(content="t1"),
        AIMessage(content="t2"),
        AIMessage(content="t3"),
        AIMessage(content="t4"),
    ]
    # candidate index 1 is 6 messages behind the tail; lookback 3 => index 4.
    result = _apply(msgs, policy=_policy(lookback_blocks=3))
    assert result.breakpoints == 3
    assert not _has_cc(result.messages[1])
    assert _has_cc(result.messages[4])


def test_lookback_skips_over_final_human() -> None:
    msgs: list[BaseMessage] = [
        SystemMessage(content=BIG_SYSTEM),
        AIMessage(content="old"),
        AIMessage(content="x"),
        HumanMessage(content="final"),
        AIMessage(content="t1"),
    ]
    # target len-2 == 3 is the final human -> moved one further, never onto it.
    result = _apply(msgs, policy=_policy(lookback_blocks=2))
    assert result.messages[3].content == "final"
    assert _has_cc(result.messages[4])
    assert not _has_cc(result.messages[2])


def test_lookback_clamp_never_on_final_human() -> None:
    msgs: list[BaseMessage] = [
        SystemMessage(content=BIG_SYSTEM),
        AIMessage(content="a1"),
        HumanMessage(content="final"),
    ]
    result = _apply(msgs, policy=_policy(lookback_blocks=0))
    assert result.messages[2].content == "final"
    assert _has_cc(result.messages[1])


def test_min_prefix_suppresses_breakpoints() -> None:
    msgs: list[BaseMessage] = [
        SystemMessage(content="short"),
        AIMessage(content="a"),
        HumanMessage(content="q"),
    ]
    policy = _policy(min_prefix_tokens={"anthropic_explicit": 1024})
    result = _apply(msgs, policy=policy)
    assert result.family is CacheFamily.ANTHROPIC_EXPLICIT
    assert result.breakpoints == 0
    assert result.messages[0].content == "short"
    assert all("cache_control" not in t for t in result.tools)
    assert len(result.prefix_hash) == 64


def test_inputs_not_mutated() -> None:
    msgs = _messages()
    tools = _tools()
    msgs_before = [m.model_copy(deep=True) for m in msgs]
    tools_before = copy.deepcopy(tools)
    result = _apply(msgs, tools)
    assert result.breakpoints == 3
    assert tools == tools_before
    assert [m.content for m in msgs] == [m.content for m in msgs_before]
    assert result.tools[-1] is not tools[-1]


def test_breakpoint_counter_incremented(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, dict[str, Any] | None]] = []

    class _FakeOTel:
        def increment_counter(
            self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
        ) -> None:
            calls.append((metric, value, attributes))

    import prismal.providers.prompt_cache as mod

    monkeypatch.setattr(mod, "_otel", lambda: _FakeOTel())
    _apply()
    assert calls == [("prompt_cache_breakpoints", 3, {"family": "anthropic_explicit"})]


# --------------------------------------------------------------------------- OpenAI / others


def test_openai_sets_prompt_cache_key() -> None:
    msgs = _messages()
    tools = _tools()
    result = _apply(msgs, tools, model="gpt-4o")
    assert result.family is CacheFamily.OPENAI_AUTO
    assert result.breakpoints == 0
    assert result.request_kwargs == {"prompt_cache_key": f"prismal:coder:{result.prefix_hash[:16]}"}
    assert result.tools == tools
    assert [m.content for m in result.messages] == [m.content for m in msgs]


def test_openai_retention() -> None:
    result = _apply(model="openai/gpt-4.1", policy=_policy(openai_retention="24h"))
    assert result.request_kwargs["prompt_cache_retention"] == "24h"
    assert result.request_kwargs["prompt_cache_key"].startswith("prismal:coder:")


@pytest.mark.parametrize("model", ["gemini-2.5-pro", "deepseek-chat", "ollama/llama3"])
def test_other_families_noop(model: str) -> None:
    msgs = _messages()
    tools = _tools()
    result = _apply(msgs, tools, model=model)
    assert result.breakpoints == 0
    assert result.request_kwargs == {}
    assert result.tools == tools
    assert [m.content for m in result.messages] == [m.content for m in msgs]


# --------------------------------------------------------------------------- prefix hash


def test_prefix_hash_matches_spec_formula() -> None:
    result = _apply(tenant=None)
    expected = hashlib.sha256((stable_dumps(_tools()) + BIG_SYSTEM).encode("utf-8")).hexdigest()
    assert result.prefix_hash == expected


def test_prefix_hash_tenant_scoped() -> None:
    a = _apply(tenant="org-a", policy=_policy(scope="tenant"))
    b = _apply(tenant="org-b", policy=_policy(scope="tenant"))
    assert a.prefix_hash != b.prefix_hash


def test_prefix_hash_global_scope_ignores_tenant() -> None:
    a = _apply(tenant="org-a", policy=_policy(scope="global"))
    b = _apply(tenant="org-b", policy=_policy(scope="global"))
    assert a.prefix_hash == b.prefix_hash


def test_prefix_hash_stable_across_key_order() -> None:
    tools = _tools()
    reordered = [
        {
            "function": {
                "parameters": {},
                "description": t["function"]["description"],
                "name": t["function"]["name"],
            },
            "type": "function",
        }
        for t in tools
    ]
    a = _apply(tools=tools, model="gpt-4o")
    b = _apply(tools=reordered, model="gpt-4o")
    assert a.prefix_hash == b.prefix_hash
    assert a.request_kwargs == b.request_kwargs


# --------------------------------------------------------------------------- fail-open


class _Exploding(HumanMessage):
    def model_copy(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        raise RuntimeError("boom")


def test_fail_open_on_garbage_message() -> None:
    msgs: list[BaseMessage] = [
        SystemMessage(content=BIG_SYSTEM),
        _Exploding(content="stable"),
        HumanMessage(content="final"),
    ]
    tools = _tools()
    result = _apply(msgs, tools)
    assert result.family is CacheFamily.NONE
    assert result.breakpoints == 0
    assert result.request_kwargs == {}
    assert result.tools == tools
    assert [m.content for m in result.messages] == [m.content for m in msgs]


def test_fail_open_on_non_message_object() -> None:
    result = apply_cache_policy(
        [object()],  # type: ignore[list-item]
        _tools(),
        model="claude-sonnet-4-5",
        agent="coder",
        policy=_policy(),
    )
    assert result.family is CacheFamily.NONE
    assert result.breakpoints == 0
