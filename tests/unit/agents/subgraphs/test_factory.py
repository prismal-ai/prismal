"""Unit tests for SubgraphFactory."""
import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from lightagent.agents.subgraphs.factory import SubgraphFactory
from lightagent.agents.subgraphs.registry import SubgraphDefinition
from lightagent.agents.state import create_initial_state

# aiosqlite connections held by AsyncSqliteSaver are intentionally kept open
# for the lifetime of the compiled graph; GC finaliser warnings are expected.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)


async def _node_a(state: dict) -> dict:  # type: ignore[type-arg]
    """Test node A."""
    return {
        "current_agent": "node_a",
        "metadata": {**state.get("metadata", {}), "visited_a": True},
    }


async def _node_b(state: dict) -> dict:  # type: ignore[type-arg]
    """Test node B."""
    return {
        "current_agent": "node_b",
        "metadata": {**state.get("metadata", {}), "visited_b": True},
    }


@pytest.mark.asyncio
async def test_factory_build_returns_compiled_graph() -> None:
    """SubgraphFactory.build() returns a CompiledStateGraph."""
    defn = SubgraphDefinition(
        name="simple",
        description="Two-node test graph",
        entry_point="node_a",
        nodes={"node_a": _node_a, "node_b": _node_b},
        edges=[("node_a", "node_b")],
        conditional_edges={},
    )
    factory = SubgraphFactory()
    graph = await factory.build(defn, checkpointer_path=":memory:")
    assert isinstance(graph, CompiledStateGraph)


@pytest.mark.asyncio
async def test_factory_graph_invokable() -> None:
    """A built subgraph can be invoked via ainvoke."""
    defn = SubgraphDefinition(
        name="simple2",
        description="Invokable test",
        entry_point="node_a",
        nodes={"node_a": _node_a},
        edges=[],
        conditional_edges={},
    )
    factory = SubgraphFactory()
    graph = await factory.build(defn, checkpointer_path=":memory:")
    state = create_initial_state("test-sess")
    state["messages"] = [HumanMessage(content="build something")]
    result = await graph.ainvoke(
        state, config={"configurable": {"thread_id": "t1"}}
    )
    assert result["current_agent"] == "node_a"


@pytest.mark.asyncio
async def test_factory_separate_checkpointer_per_subgraph(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Each subgraph gets its own compiled graph (not shared)."""
    defn1 = SubgraphDefinition(
        name="sg1",
        description="d",
        entry_point="node_a",
        nodes={"node_a": _node_a},
        edges=[],
        conditional_edges={},
    )
    defn2 = SubgraphDefinition(
        name="sg2",
        description="d",
        entry_point="node_b",
        nodes={"node_b": _node_b},
        edges=[],
        conditional_edges={},
    )
    factory = SubgraphFactory()
    path1 = str(tmp_path / "checkpoints_subgraph_sg1.db")
    path2 = str(tmp_path / "checkpoints_subgraph_sg2.db")
    g1 = await factory.build(defn1, checkpointer_path=path1)
    g2 = await factory.build(defn2, checkpointer_path=path2)
    assert g1 is not g2
