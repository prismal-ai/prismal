"""Demonstrate plugin discovery with an in-memory entry point.

This monkeypatches the entry-point seam so the example runs without installing
a real plugin distribution. In production, ``discover_plugins()`` reads entry
points declared in installed packages' ``pyproject.toml``.

Run::

    python examples/extension/discover_plugins_demo.py
"""

from __future__ import annotations

from importlib.metadata import EntryPoint

from prismal.agents.extension import PrismalStateGraphBuilder, discover_plugins
from prismal.agents.extension import plugins as plugins_mod
from prismal.agents.subgraphs.registry import SubgraphRegistry
from prismal.core.config import Settings


async def _node(state: dict) -> dict:
    return {"metadata": {"demo": True}}


def register_demo_pipeline(registry: SubgraphRegistry) -> None:
    builder = PrismalStateGraphBuilder("demo_plugin_pipeline")
    builder.add_node("n", _node)
    builder.set_entry_point("n")
    builder.add_edge("n", "__end__")
    registry.register_sync("demo_plugin_pipeline", builder.compile())


def main() -> None:
    ep = EntryPoint(
        name="demo_plugin_pipeline",
        value=f"{__name__}:register_demo_pipeline",
        group="prismal.subgraphs",
    )
    plugins_mod._entry_points = lambda group: [ep] if group == "prismal.subgraphs" else []

    registry = SubgraphRegistry()
    report = discover_plugins(
        settings=Settings(plugins_autodiscover=True),
        registry=registry,
        groups=["subgraphs"],
    )
    print(f"loaded={report.loaded_count} failed={report.failed_count}")
    print("registered subgraphs:", registry.list())


if __name__ == "__main__":
    main()
