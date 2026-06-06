"""Tests for plugin discovery (X4, SPEC-EXT-004)."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest
from langchain_core.tools import BaseTool

from prismal.agents.extension import plugins as plugins_mod
from prismal.agents.extension import prismal_node
from prismal.agents.extension.builder import PrismalStateGraphBuilder
from prismal.agents.extension.plugins import (
    RAGEngineRegistry,
    discover_plugins,
    get_plugin_info,
    list_plugins,
)
from prismal.agents.subgraphs.registry import SubgraphRegistry
from prismal.core.config import Settings

# ── Plugin fixtures referenced via entry-point `value` strings ────────────────


async def _node(state):
    return {"current_agent": "plugin"}


def register_demo_subgraph(registry: SubgraphRegistry) -> None:
    b = PrismalStateGraphBuilder("demo_sub")
    b.add_node("n", _node)
    b.set_entry_point("n")
    b.add_edge("n", "__end__")
    registry.register_sync("demo_sub", b.compile())


def register_broken_subgraph(registry: SubgraphRegistry) -> None:
    raise RuntimeError("plugin blew up")


@prismal_node(name="plugin_node_demo", capabilities=["general"])
async def plugin_node_demo(state):
    return {"current_agent": "plugin_node"}


class DemoTool(BaseTool):
    name: str = "demo_plugin_tool"
    description: str = "A demo plugin tool"

    def _run(self, *args: object, **kwargs: object) -> str:
        return "ok"


def make_demo_tool() -> DemoTool:
    return DemoTool()


class DemoRAGEngine:
    """A trivial RAG engine plugin."""

    name = "demo_rag"


def _ep(name: str, attr: str, group: str) -> EntryPoint:
    return EntryPoint(name=name, value=f"{__name__}:{attr}", group=f"prismal.{group}")


@pytest.fixture
def fake_entry_points(monkeypatch: pytest.MonkeyPatch):
    mapping: dict[str, list[EntryPoint]] = {
        "prismal.subgraphs": [_ep("demo_sub", "register_demo_subgraph", "subgraphs")],
        "prismal.nodes": [_ep("plugin_node_demo", "plugin_node_demo", "nodes")],
        "prismal.tools": [_ep("demo_tool", "make_demo_tool", "tools")],
        "prismal.rag_engines": [_ep("demo_rag", "DemoRAGEngine", "rag_engines")],
    }

    def fake(group: str) -> list[EntryPoint]:
        return mapping.get(group, [])

    monkeypatch.setattr(plugins_mod, "_entry_points", fake)
    return mapping


def _settings(**kw) -> Settings:
    base = {"plugins_autodiscover": True}
    base.update(kw)
    return Settings(**base)


class TestDiscoverAllGroups:
    def test_loads_every_group(self, fake_entry_points) -> None:
        reg = SubgraphRegistry()
        RAGEngineRegistry.get_instance()._engines.clear()
        report = discover_plugins(settings=_settings(), registry=reg)
        assert report.loaded_count == 4
        assert report.failed_count == 0
        assert reg.get("demo_sub") is not None
        assert RAGEngineRegistry.get_instance().get("demo_rag") is DemoRAGEngine


class TestFailureIsolation:
    def test_one_failure_does_not_abort_rest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(group: str):
            if group == "prismal.subgraphs":
                return [
                    _ep("broken", "register_broken_subgraph", "subgraphs"),
                    _ep("demo_sub", "register_demo_subgraph", "subgraphs"),
                ]
            return []

        monkeypatch.setattr(plugins_mod, "_entry_points", fake)
        reg = SubgraphRegistry()
        report = discover_plugins(settings=_settings(), registry=reg, groups=["subgraphs"])
        assert report.failed_count == 1
        assert report.loaded_count == 1
        assert reg.get("demo_sub") is not None


class TestAllowDenyList:
    def test_denylist_skips(self, fake_entry_points) -> None:
        report = discover_plugins(
            settings=_settings(plugins_denylist=["demo_sub"]),
            registry=SubgraphRegistry(),
            groups=["subgraphs"],
        )
        assert report.loaded_count == 0
        assert report.skipped[0].status == "skipped_by_denylist"

    def test_allowlist_skips_others(self, fake_entry_points) -> None:
        report = discover_plugins(
            settings=_settings(plugins_allowlist=["something_else"]),
            registry=SubgraphRegistry(),
            groups=["subgraphs"],
        )
        assert report.loaded_count == 0
        assert report.skipped[0].status == "skipped_not_in_allowlist"

    def test_denylist_precedence_over_allowlist(self, fake_entry_points) -> None:
        report = discover_plugins(
            settings=_settings(plugins_allowlist=["demo_sub"], plugins_denylist=["demo_sub"]),
            registry=SubgraphRegistry(),
            groups=["subgraphs"],
        )
        assert report.skipped[0].status == "skipped_by_denylist"


class TestAutodiscoverToggle:
    def test_toggle_off_discovers_nothing(self, fake_entry_points) -> None:
        report = discover_plugins(settings=_settings(plugins_autodiscover=False))
        assert report.loaded_count == 0


class TestListAndInfo:
    def test_list_plugins_does_not_load(self, fake_entry_points) -> None:
        infos = list_plugins(settings=_settings())
        names = {i.name for i in infos}
        assert {"demo_sub", "plugin_node_demo", "demo_tool", "demo_rag"} <= names

    def test_get_plugin_info(self, fake_entry_points) -> None:
        info = get_plugin_info("demo_sub", settings=_settings())
        assert info is not None
        assert info.group == "subgraphs"
        assert info.object_name == "register_demo_subgraph"

    def test_get_plugin_info_unknown(self, fake_entry_points) -> None:
        assert get_plugin_info("nope_zzz", settings=_settings()) is None


class TestRAGEngineRegistry:
    def test_register_and_get(self) -> None:
        reg = RAGEngineRegistry()
        reg.register("eng", DemoRAGEngine)
        assert reg.get("eng") is DemoRAGEngine
        assert "eng" in reg.list()

    def test_duplicate_raises(self) -> None:
        reg = RAGEngineRegistry()
        reg.register("eng", DemoRAGEngine)
        with pytest.raises(ValueError, match="already"):
            reg.register("eng", DemoRAGEngine)
