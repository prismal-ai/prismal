"""Unit tests for SubgraphRegistry."""

import asyncio

import pytest

from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry


def _make_def(name: str = "test_graph") -> SubgraphDefinition:
    """Create a minimal SubgraphDefinition for testing."""
    return SubgraphDefinition(
        name=name,
        description="A test subgraph",
        entry_point="start",
        nodes={"start": lambda s: s},
        edges=[],
        conditional_edges={},
    )


@pytest.mark.asyncio
async def test_register_and_list() -> None:
    """Test that registering a subgraph makes it appear in list()."""
    reg = SubgraphRegistry()
    await reg.register("pipe1", _make_def("pipe1"))
    assert "pipe1" in reg.list()


@pytest.mark.asyncio
async def test_register_duplicate_raises() -> None:
    """Test that registering a duplicate name raises ValueError."""
    reg = SubgraphRegistry()
    await reg.register("dup", _make_def("dup"))
    with pytest.raises(ValueError, match="already registered"):
        await reg.register("dup", _make_def("dup"))


@pytest.mark.asyncio
async def test_unregister() -> None:
    """Test that unregistering removes the subgraph."""
    reg = SubgraphRegistry()
    await reg.register("to_remove", _make_def("to_remove"))
    await reg.unregister("to_remove")
    assert "to_remove" not in reg.list()


@pytest.mark.asyncio
async def test_unregister_missing_raises() -> None:
    """Test that unregistering a nonexistent name raises KeyError."""
    reg = SubgraphRegistry()
    with pytest.raises(KeyError):
        await reg.unregister("ghost")


def test_get_existing() -> None:
    """Test that get() returns the definition for a registered name."""
    reg = SubgraphRegistry()
    asyncio.run(reg.register("g1", _make_def("g1")))
    defn = reg.get("g1")
    assert defn is not None
    assert defn.name == "g1"


def test_get_missing_returns_none() -> None:
    """Test that get() returns None for unregistered names."""
    reg = SubgraphRegistry()
    assert reg.get("does_not_exist") is None


def test_list_empty() -> None:
    """Test that a fresh registry returns an empty list."""
    reg = SubgraphRegistry()
    assert reg.list() == []


@pytest.mark.asyncio
async def test_singleton_pattern() -> None:
    """Test that get_instance() always returns the same object."""
    # Reset singleton for test isolation
    SubgraphRegistry._instance = None
    a = SubgraphRegistry.get_instance()
    b = SubgraphRegistry.get_instance()
    assert a is b
