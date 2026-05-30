"""Tests for the synchronous registration path on SubgraphRegistry (X4)."""

from __future__ import annotations

import pytest

from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry


def _defn(name: str) -> SubgraphDefinition:
    async def node(state):
        return {}

    return SubgraphDefinition(
        name=name,
        description="d",
        entry_point="n",
        nodes={"n": node},
        edges=[("n", "__end__")],
    )


class TestRegisterSync:
    def test_register_sync_stores_definition(self) -> None:
        reg = SubgraphRegistry()
        reg.register_sync("sync_pipe", _defn("sync_pipe"))
        assert reg.get("sync_pipe") is not None
        assert "sync_pipe" in reg.list()

    def test_register_sync_duplicate_raises(self) -> None:
        reg = SubgraphRegistry()
        reg.register_sync("dup", _defn("dup"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register_sync("dup", _defn("dup"))
