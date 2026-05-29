"""Official LangGraph passthrough for prismal extensions.

This module re-exports the LangGraph symbols that prismal supports as a
public extension surface. Importing from here (rather than directly from
``langgraph.*``) guarantees:

* Version compatibility — :data:`VERSION` is the LangGraph version prismal
  was tested against.
* Stable contract — only the symbols below are part of the prismal
  extension API; other LangGraph internals may change without notice.

Example::

    from prismal.langgraph import StateGraph, START, END, Send, add_messages
    from prismal.agents.state import AgentState

    graph = StateGraph(AgentState)
    graph.add_node("my_node", my_node)
    graph.add_edge(START, "my_node")
    graph.add_edge("my_node", END)
    compiled = graph.compile()
"""

from __future__ import annotations

from importlib.metadata import version as _pkg_version

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt

from prismal.agents.state import AgentState
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

VERSION: str = _pkg_version("langgraph")
"""Installed LangGraph version, resolved at import time."""

__all__ = [
    "END",
    "START",
    "VERSION",
    "AgentState",
    "CompiledStateGraph",
    "Send",
    "StateGraph",
    "SubgraphDefinition",
    "SubgraphRegistry",
    "add_messages",
    "interrupt",
]
