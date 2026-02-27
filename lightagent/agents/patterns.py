"""Agent pattern registry for LangGraph multi-agent system.

Each pattern corresponds to a different graph topology in ``AgentFactory``.
"""

from __future__ import annotations

from enum import Enum


class AgentPattern(str, Enum):
    """Supported multi-agent execution patterns.

    Attributes:
        SUPERVISOR: Supervisor routes to specialist sub-agents (default).
        REACT: Single-agent ReAct tool-use loop.
        PLAN_EXECUTE: Planner decomposes task; executor runs steps in parallel.
        SWARM: Agents hand off to each other without a central supervisor.
        REFLEXION: Generate -> self-critique -> regenerate (up to max_iterations).
        CODEACT: Agent executes Python code as its primary action.
        CRAG: Corrective RAG -- retrieve, grade, fallback to web, generate.
        DEBATE: Multiple agents argue perspectives before consensus.
    """

    SUPERVISOR = "supervisor"
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"
    SWARM = "swarm"
    REFLEXION = "reflexion"
    CODEACT = "codeact"
    CRAG = "crag"
    DEBATE = "debate"


__all__ = ["AgentPattern"]
