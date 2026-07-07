"""Tests for the extension-surface exception hierarchy (Fase X)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    AdapterError,
    ExtensionError,
    LangChainAdapterError,
    NodeExecutionError,
    NodeTimeoutError,
    NodeValidationError,
    PluginConflictError,
    PluginLoadError,
    PrismalError,
)


class TestExtensionExceptionHierarchy:
    """Every extension error must hang off PrismalError via ExtensionError."""

    def test_extension_error_is_prismal_error(self) -> None:
        assert issubclass(ExtensionError, PrismalError)

    @pytest.mark.parametrize(
        "exc_type",
        [
            NodeExecutionError,
            PluginLoadError,
            PluginConflictError,
            AdapterError,
        ],
    )
    def test_subclasses_of_extension_error(self, exc_type: type) -> None:
        assert issubclass(exc_type, ExtensionError)

    def test_node_timeout_is_node_execution_error(self) -> None:
        assert issubclass(NodeTimeoutError, NodeExecutionError)

    def test_node_validation_is_node_execution_error(self) -> None:
        assert issubclass(NodeValidationError, NodeExecutionError)

    def test_langchain_adapter_is_adapter_error(self) -> None:
        assert issubclass(LangChainAdapterError, AdapterError)


class TestNodeExecutionError:
    def test_carries_node_name_state_keys_and_cause(self) -> None:
        cause = ValueError("boom")
        err = NodeExecutionError("my_node", ["messages", "metadata"], cause)
        assert err.node_name == "my_node"
        assert err.state_keys == ["messages", "metadata"]
        assert err.cause is cause
        assert "my_node" in str(err)

    def test_is_catchable_as_prismal_error(self) -> None:
        with pytest.raises(PrismalError):
            raise NodeExecutionError("n", [], RuntimeError())


class TestNodeTimeoutError:
    def test_carries_timeout(self) -> None:
        err = NodeTimeoutError("slow_node", ["messages"], TimeoutError(), timeout_s=2.5)
        assert err.timeout_s == 2.5
        assert err.node_name == "slow_node"


class TestNodeValidationError:
    def test_carries_direction_and_schema_errors(self) -> None:
        cause = ValueError("bad")
        err = NodeValidationError(
            "critic",
            ["messages", "current_agent"],
            cause,
            direction="output",
            schema_errors=["current_agent: field required"],
        )
        assert err.node_name == "critic"
        assert err.state_keys == ["messages", "current_agent"]
        assert err.cause is cause
        assert err.direction == "output"
        assert err.schema_errors == ["current_agent: field required"]

    def test_accepts_none_cause(self) -> None:
        err = NodeValidationError("n", ["a"], None, direction="input", schema_errors=[])
        assert err.cause is None
        assert err.direction == "input"

    def test_is_node_execution_error(self) -> None:
        err = NodeValidationError("n", [], None, direction="input", schema_errors=["x: y"])
        assert isinstance(err, NodeExecutionError)


class TestPluginLoadError:
    def test_carries_plugin_name_and_entry_point(self) -> None:
        cause = ImportError("no module")
        err = PluginLoadError("prismal_x_demo", "demo:register", cause)
        assert err.plugin_name == "prismal_x_demo"
        assert err.entry_point == "demo:register"
        assert err.cause is cause


class TestPluginConflictError:
    def test_carries_name_and_plugins(self) -> None:
        err = PluginConflictError("triage", ["plugin_a", "plugin_b"])
        assert err.conflicting_name == "triage"
        assert err.plugins == ["plugin_a", "plugin_b"]


class TestLangChainAdapterError:
    def test_carries_runnable_type(self) -> None:
        err = LangChainAdapterError("RunnableLambda", "bad signature")
        assert err.runnable_type == "RunnableLambda"
        assert "bad signature" in str(err)
