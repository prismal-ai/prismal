"""
LangGraph agent system — public API.

Provides the compiled multi-agent supervisor graph, all pattern implementations,
AgentState definition, and utilities for spawning sub-agents.

Quick start::

    from lightagent.agents import AgentFactory, AgentPattern, create_initial_state

    factory = AgentFactory()
    graph = factory.build(AgentPattern.SUPERVISOR)
    state = create_initial_state(session_id="demo")
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "demo"}})
"""

from lightagent.agents.factory import AgentFactory
from lightagent.agents.graph import build_supervisor_graph, get_compiled_graph
from lightagent.agents.patterns import AgentPattern
from lightagent.agents.spawner import SubAgentSpawner
from lightagent.agents.state import AgentState, create_initial_state

__all__ = [
    "AgentFactory",
    "AgentPattern",
    "AgentState",
    "SubAgentSpawner",
    "build_supervisor_graph",
    "create_initial_state",
    "get_compiled_graph",
]
