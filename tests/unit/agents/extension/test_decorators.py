"""Tests for @prismal_node decorator, NodeMetadata and the node registry (X2)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.extension import (
    NodeMetadata,
    RetryPolicy,
    get_node_metadata,
    list_registered_nodes,
    prismal_node,
)
from prismal.agents.state import create_initial_state
from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP


def _state(text: str = "hello"):
    state = create_initial_state(session_id="sess-x2")
    state["messages"] = [HumanMessage(content=text)]
    return state


class TestDataclasses:
    def test_retry_policy_defaults(self) -> None:
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert rp.backoff_s == (0.1, 0.5, 1.0)
        assert TimeoutError in rp.retry_on

    def test_retry_policy_frozen(self) -> None:
        rp = RetryPolicy()
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            rp.max_attempts = 5  # type: ignore[misc]

    def test_node_metadata_frozen(self) -> None:
        meta = NodeMetadata(
            name="n",
            capabilities=(),
            security="standard",
            audit=True,
            retry=None,
            timeout_s=None,
            raise_on_error=False,
            registered_at="2026-01-01T00:00:00+00:00",
            source_module="m",
        )
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            meta.name = "x"  # type: ignore[misc]


class TestDecoratorBasics:
    async def test_default_name_is_function_name(self) -> None:
        @prismal_node()
        async def my_default_node(state):
            return {"current_agent": "my_default_node"}

        assert my_default_node.__prismal_node__.name == "my_default_node"

    async def test_custom_name(self) -> None:
        @prismal_node(name="custom_label")
        async def some_fn(state):
            return {}

        assert some_fn.__prismal_node__.name == "custom_label"

    async def test_preserves_wrapped_function(self) -> None:
        async def inner(state):
            return {}

        wrapped = prismal_node(name="wrap_test")(inner)
        assert wrapped.__wrapped__ is inner

    async def test_passthrough_returns_state_update(self) -> None:
        @prismal_node(name="echo_node", security="off", audit=False)
        async def echo_node(state):
            return {"current_agent": "echo"}

        out = await echo_node(_state())
        assert out["current_agent"] == "echo"

    async def test_metadata_captures_options(self) -> None:
        @prismal_node(
            name="opt_node",
            capabilities=["research", "general"],
            security="strict",
            audit=False,
            timeout_s=2.0,
        )
        async def opt_node(state):
            return {}

        meta = opt_node.__prismal_node__
        assert meta.capabilities == ("research", "general")
        assert meta.security == "strict"
        assert meta.audit is False
        assert meta.timeout_s == 2.0
        assert meta.source_module == __name__


class TestRegistry:
    async def test_node_registered_and_retrievable(self) -> None:
        @prismal_node(name="registry_node_alpha")
        async def registry_node_alpha(state):
            return {}

        meta = get_node_metadata("registry_node_alpha")
        assert meta is not None
        assert meta.name == "registry_node_alpha"
        assert any(m.name == "registry_node_alpha" for m in list_registered_nodes())

    async def test_capabilities_registered_in_default_map(self) -> None:
        @prismal_node(name="cap_node_beta", capabilities=["vision"])
        async def cap_node_beta(state):
            return {}

        assert DEFAULT_CAPABILITY_MAP.get("cap_node_beta") == ["vision"]

    async def test_double_decoration_is_idempotent(self) -> None:
        @prismal_node(name="idem_node")
        async def idem_node(state):
            return {}

        again = prismal_node(name="idem_node")(idem_node)
        assert again is idem_node
        assert again.__prismal_node__ is idem_node.__prismal_node__

    def test_get_unknown_node_returns_none(self) -> None:
        assert get_node_metadata("does_not_exist_zzz") is None
