"""Tests for PrismalStateGraphBuilder (X3, SPEC-EXT-003)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.extension import prismal_node
from prismal.agents.extension.builder import BuilderDefaults, PrismalStateGraphBuilder
from prismal.agents.state import create_initial_state
from prismal.agents.subgraphs.registry import SubgraphDefinition


async def _classify(state):
    return {"current_agent": "classify"}


async def _respond(state):
    return {"current_agent": "respond"}


def _state():
    s = create_initial_state(session_id="sess-builder")
    s["messages"] = [HumanMessage(content="hi")]
    return s


class TestCompileDefinition:
    def test_compile_returns_subgraph_definition(self) -> None:
        b = PrismalStateGraphBuilder("my_pipeline")
        b.add_node("classify", _classify)
        b.add_node("respond", _respond)
        b.add_edge("classify", "respond")
        b.add_edge("respond", "__end__")
        b.set_entry_point("classify")
        defn = b.compile()
        assert isinstance(defn, SubgraphDefinition)
        assert defn.name == "my_pipeline"
        assert defn.entry_point == "classify"
        assert set(defn.nodes) == {"classify", "respond"}
        assert ("classify", "respond") in defn.edges

    def test_fluent_returns_self(self) -> None:
        b = PrismalStateGraphBuilder("p")
        assert b.add_node("classify", _classify) is b
        assert b.add_edge("classify", "__end__") is b
        assert b.set_entry_point("classify") is b


class TestAutoWrap:
    def test_plain_callable_is_auto_wrapped(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify, capabilities=["general"])
        defn = b.set_entry_point("classify").add_edge("classify", "__end__").compile()
        assert hasattr(defn.nodes["classify"], "__prismal_node__")

    def test_predecorated_node_not_rewrapped(self) -> None:
        @prismal_node(name="already")
        async def already(state):
            return {}

        b = PrismalStateGraphBuilder("p")
        b.add_node("already", already)
        defn = b.set_entry_point("already").add_edge("already", "__end__").compile()
        assert defn.nodes["already"] is already


class TestValidation:
    def test_duplicate_node_raises(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        with pytest.raises(ValueError, match="already"):
            b.add_node("classify", _respond)

    def test_compile_without_entry_point_raises(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        with pytest.raises(ValueError, match="entry"):
            b.compile()

    def test_edge_to_unknown_node_raises_at_compile(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.set_entry_point("classify")
        b.add_edge("classify", "ghost")
        with pytest.raises(ValueError, match="ghost"):
            b.compile()


class TestCompileRaw:
    async def test_linear_pipeline_runs_end_to_end(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.add_node("respond", _respond)
        b.add_edge("classify", "respond")
        b.add_edge("respond", "__end__")
        b.set_entry_point("classify")
        compiled = b.compile_raw()
        result = await compiled.ainvoke(_state())
        assert result["current_agent"] == "respond"

    async def test_conditional_edges_route(self) -> None:
        async def router(state):
            return {"current_agent": "router"}

        async def yes(state):
            return {"current_agent": "yes"}

        async def no(state):
            return {"current_agent": "no"}

        b = PrismalStateGraphBuilder("p")
        b.add_node("router", router)
        b.add_node("yes", yes)
        b.add_node("no", no)
        b.add_conditional_edges("router", lambda s: "left", {"left": "yes", "right": "no"})
        b.add_edge("yes", "__end__")
        b.add_edge("no", "__end__")
        b.set_entry_point("router")
        compiled = b.compile_raw()
        result = await compiled.ainvoke(_state())
        assert result["current_agent"] == "yes"


class TestSupervisorNode:
    async def test_valid_routing(self) -> None:
        async def routing(state):
            return "worker"

        async def worker(state):
            return {"current_agent": "worker"}

        b = PrismalStateGraphBuilder("p")
        b.add_node("worker", worker)
        b.add_supervisor_node(routing, valid_next=["worker"], name="supervisor")
        b.set_entry_point("supervisor")
        b.add_edge("worker", "__end__")
        compiled = b.compile_raw()
        result = await compiled.ainvoke(_state())
        assert result["current_agent"] == "worker"

    async def test_invalid_routing_raises(self) -> None:
        async def routing(state):
            return "nonexistent"

        async def worker(state):
            return {}

        b = PrismalStateGraphBuilder("p")
        b.add_node("worker", worker)
        b.add_supervisor_node(routing, valid_next=["worker"], name="supervisor")
        b.set_entry_point("supervisor")
        b.add_edge("worker", "__end__")
        compiled = b.compile_raw()
        with pytest.raises(ValueError, match="nonexistent"):
            await compiled.ainvoke(_state())


class TestSecurityLayer:
    def test_entry_security_layer_becomes_entry_point(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.set_entry_point("classify")
        b.add_edge("classify", "__end__")
        b.add_security_layer(at="entry")
        defn = b.compile()
        assert defn.entry_point != "classify"
        assert any(to == "classify" for _, to in defn.edges)

    async def test_entry_security_layer_sanitizes_and_runs(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.set_entry_point("classify")
        b.add_edge("classify", "__end__")
        b.add_security_layer(at="entry")
        compiled = b.compile_raw()
        result = await compiled.ainvoke(_state())
        assert result["metadata"]["security_layer"]["sanitized"] is True
        assert result["current_agent"] == "classify"

    async def test_exit_security_layer_reroutes_to_end(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.set_entry_point("classify")
        b.add_edge("classify", "__end__")
        b.add_security_layer(at="exit")
        # The original classify -> __end__ edge must now route through the layer.
        assert not any(to == "__end__" for f, to in b._edges if f == "classify")
        compiled = b.compile_raw()
        result = await compiled.ainvoke(_state())
        assert result["metadata"]["security_layer"]["at"] == "exit"

    def test_conditional_edge_unknown_source_raises(self) -> None:
        b = PrismalStateGraphBuilder("p")
        b.add_node("classify", _classify)
        b.set_entry_point("classify")
        b.add_edge("classify", "__end__")
        b.add_conditional_edges("ghost_src", lambda s: "x", {"x": "classify"})
        with pytest.raises(ValueError, match="ghost_src"):
            b.compile()


class TestBuilderDefaults:
    def test_defaults_applied_to_autowrapped_node(self) -> None:
        b = PrismalStateGraphBuilder("p", defaults=BuilderDefaults(security="off", audit=False))
        b.add_node("classify", _classify)
        defn = b.set_entry_point("classify").add_edge("classify", "__end__").compile()
        meta = defn.nodes["classify"].__prismal_node__
        assert meta.security == "off"
        assert meta.audit is False
