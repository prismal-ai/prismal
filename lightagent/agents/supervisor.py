"""Supervisor agent node for LangGraph multi-agent orchestration.

The supervisor is the entry point and coordinator of the LangGraph graph.
It analyses the conversation history, decides which specialist sub-agent
should handle the next step (or whether the task is complete), and returns
an updated state dict for LangGraph to act on.

Routing is performed by calling the LLM with a structured system prompt that
asks it to respond with exactly one of the known agent names or the literal
string ``"END"``.

Example::

    from lightagent.agents.supervisor import supervisor_node, supervisor_router
    from lightagent.agents.state import create_initial_state

    state = create_initial_state(session_id="sess-demo")
    updated = await supervisor_node(state)
    next_node = supervisor_router(updated)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.supervisor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMBERS: list[str] = [
    "researcher",
    "coder",
    "rag_agent",
    "planner",
    "critic",
    "data_analyst",
    "file_manager",
]

_VALID_ROUTES: frozenset[str] = frozenset(MEMBERS) | {"END"}

_SYSTEM_PROMPT: str = """You are a supervisor coordinating a team of AI agents.
Your role is to:
1. Analyze the user's request and the conversation history
2. Route to the most appropriate specialist agent
3. Aggregate results from specialists into a coherent response
4. Return a FINAL ANSWER when the task is complete (route to END)

Available agents:
- researcher: Web search, RAG queries, reading files
- coder: Writing and executing code, reading/writing code files
- rag_agent: Internal document knowledge base Q&A
- planner: Decompose complex multi-step tasks
- critic: Review and improve outputs
- data_analyst: SQL queries (DuckDB), DataFrame transforms, charts
- file_manager: File read/write operations
- END: Return final answer to the user

Routing rules:
- Route to 'END' when you have a complete answer
- Route to 'END' for simple factual questions you can answer directly

Respond with ONLY the agent name (e.g. "researcher" or "END"). No explanation."""

# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------


async def supervisor_node(state: AgentState) -> dict[str, object]:
    """Execute the supervisor node logic and decide the next routing target.

    Builds a prompt from the current conversation history, invokes the LLM,
    and normalises its response to a valid routing option.  If the LLM returns
    an unrecognised string the supervisor defaults to ``None`` (i.e. END) and
    logs a warning so the event can be investigated without crashing the graph.

    Args:
        state: Current shared agent state flowing through the LangGraph graph.

    Returns:
        A partial state dict containing:
        - ``current_agent``: always ``"supervisor"``.
        - ``next_agent``: a member name string, or ``None`` when routing to END.
        - ``messages``: a list with one new ``HumanMessage`` carrying the final
          answer when the supervisor routes to END, otherwise an empty list so
          that LangGraph's ``add_messages`` reducer appends nothing.
    """
    session_id: str = str(state.get("session_id", "unknown"))
    logger.debug(
        "supervisor_node_invoked",
        session_id=session_id,
        message_count=len(state["messages"]),
    )

    otel = OTelManager()
    with otel.start_span(
        "agent.supervisor",
        attributes={
            "lightagent.agent": "supervisor",
            "lightagent.session_id": session_id,
            "lightagent.message_count": len(state["messages"]),
        },
    ) as span:
        llm = ProviderRegistry().get_llm_with_fallback()

        # Build the message list directly so the LLM can be called via ainvoke
        # without relying on the prompt | llm pipe operator (which is harder to mock
        # in unit tests and adds no value for this simple routing prompt).
        routing_messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            *state["messages"],
            HumanMessage(
                content=(
                    "Based on the conversation above, which agent should handle "
                    "the next step? Respond with ONLY the agent name or 'END'."
                )
            ),
        ]

        response = await llm.ainvoke(routing_messages)

        raw: str = str(response.content).strip()
        # Normalise: strip surrounding quotes/whitespace and match case-insensitively
        normalised = raw.strip("\"' \t\n").upper()

        # Find exact match against valid routes (case-insensitive)
        matched: str | None = None
        for valid in _VALID_ROUTES:
            if valid.upper() == normalised:
                matched = valid
                break

        if matched is None:
            logger.warning(
                "supervisor_invalid_routing",
                raw_response=raw,
                defaulting_to="END",
                session_id=session_id,
            )
            matched = "END"

        next_agent: str | None = None if matched == "END" else matched

        span.set_attribute("lightagent.routing_decision", matched)

        logger.info(
            "supervisor_routing_decision",
            next_agent=next_agent,
            raw_response=raw,
            session_id=session_id,
        )

        return {
            "current_agent": "supervisor",
            "next_agent": next_agent,
            "messages": [],
        }


# ---------------------------------------------------------------------------
# Supervisor router
# ---------------------------------------------------------------------------

# The return type lists all valid node names plus the LangGraph END sentinel.
_RouterLiteral = Literal[
    "researcher",
    "coder",
    "rag_agent",
    "planner",
    "critic",
    "data_analyst",
    "file_manager",
    "__end__",
]


def supervisor_router(state: AgentState) -> _RouterLiteral:
    """Map ``state["next_agent"]`` to a LangGraph conditional-edge target.

    This function is passed directly to ``StateGraph.add_conditional_edges``
    as the routing function.  It reads the ``next_agent`` field set by
    :func:`supervisor_node` and returns the corresponding node name string.

    Args:
        state: Current shared agent state containing the ``next_agent`` field
            populated by the most recent call to :func:`supervisor_node`.

    Returns:
        The node name to transition to, or ``"__end__"`` when ``next_agent``
        is ``None`` or the string ``"END"``.
    """
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    # Cast is safe: supervisor_node only sets known MEMBERS values
    return next_agent  # type: ignore[return-value]


__all__ = ["MEMBERS", "supervisor_node", "supervisor_router"]
