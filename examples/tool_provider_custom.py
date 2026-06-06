"""A custom tool provider conforming ``ToolProviderPort`` (Fase Y).

Shows the smallest possible host-supplied provider: a plain class with a
``get_tools`` method — no base class, no registration. The core resolves
tools through it once injected with ``set_tool_provider``.

Run::

    python examples/tool_provider_custom.py
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from prismal.agents.extension import ToolProviderPort, conforms_to
from prismal.agents.tool_registry import get_tools_for_agent, set_tool_provider


def _make_tool(name: str, description: str) -> StructuredTool:
    def _fn(query: str = "") -> str:
        return f"[{name}] {query}"

    return StructuredTool.from_function(func=_fn, name=name, description=description)


class MyToolProvider:
    """Per-agent lookup backed by a plain dict — replace with your own source.

    Conforms ``ToolProviderPort`` structurally: the only requirement is
    ``get_tools(*, agent_name, capabilities=None) -> list[BaseTool]``.
    It must not raise on a degraded source — return ``[]`` instead.
    """

    def __init__(self) -> None:
        self._catalog = {
            "researcher": [
                _make_tool("acme_search", "Search the ACME internal knowledge base"),
            ],
            "coder": [
                _make_tool("acme_ci_status", "Check the ACME CI pipeline status"),
                _make_tool("acme_deploy", "Trigger an ACME staging deploy"),
            ],
        }

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[StructuredTool]:
        del capabilities  # this provider does not filter by capability
        return self._catalog.get(agent_name, [])


def main() -> None:
    provider = MyToolProvider()

    # Structural conformance — no inheritance needed.
    print("conforms to ToolProviderPort:", conforms_to(provider, ToolProviderPort))

    # Variante A: inject once at startup; every agent node resolves through it.
    set_tool_provider(provider)

    for agent in ("researcher", "coder", "planner"):
        tools = get_tools_for_agent(agent)
        print(f"{agent:>10}: {[t.name for t in tools]}")


if __name__ == "__main__":
    main()
