"""AgentState definition for LangGraph agent orchestration.

Defines the shared state TypedDict that flows through every node in the
LangGraph state machine.  All mutable fields use plain Python types so that
LangGraph can merge state snapshots correctly.

The only field with a custom reducer is ``messages``, which uses the built-in
``add_messages`` reducer from ``langgraph.graph.message`` to append rather
than replace the conversation history.

Example::

    from lightagent.agents.state import AgentState, create_initial_state

    state = create_initial_state(session_id="sess-abc123")
    # Pass state to LangGraph's StateGraph
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    BaseMessage,  # noqa: TC002 -- runtime import required for LangGraph type introspection
)
from langgraph.graph.message import add_messages

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.agents.state")


class AgentState(TypedDict):
    """Shared state for the LightAgent LangGraph state machine.

    This TypedDict is passed between every node in the graph.  LangGraph
    serialises and merges values between nodes using the field annotations;
    ``messages`` uses the ``add_messages`` reducer so that each node appends to
    the conversation rather than overwriting it.

    Attributes:
        messages: Full conversation history.  New messages are appended via the
            ``add_messages`` reducer (never replaced wholesale).
        current_agent: Name of the agent node that is currently executing.
        next_agent: Name of the next agent to route to, or ``None`` when the
            supervisor should decide.
        task_plan: Ordered list of sub-tasks produced by Plan-Execute planning.
        completed_tasks: Tasks that have been successfully finished.
        pending_tasks: Tasks that are queued but not yet started.
        retrieved_docs: Raw documents returned from the RAG pipeline, each
            represented as a metadata + content dict.  Uses an
            ``operator.add`` reducer so multiple parallel nodes can append
            results without overwriting each other (Phase 34).
        doc_grades: Relevance scores (0.0-1.0) for each retrieved document,
            aligned by index with ``retrieved_docs``.
        tool_results: Serialised outputs from tool calls.  Uses an
            ``operator.add`` reducer for parallel fan-in (Phase 34).
        tool_errors: Serialised errors from failed tool calls.
        parallel_results: Generic aggregation field for parallel worker nodes
            (Phase 34).  Uses an ``operator.add`` reducer so each ``Send``
            target can emit a partial result that is appended to the merged
            list visible to downstream aggregator nodes.
        dev_pipeline_modules: Optional list of module dicts populated by the
            dev_pipeline ``developer_agent`` when it produces multi-module
            code (Phase 34, T-313).  Consumed by the ``module_dispatcher``
            inside the dev_pipeline subgraph to fan out unit testing across
            modules in parallel.  An empty list means the developer produced
            a single-module artifact and the sequential ``unit_tester`` path
            is used instead.
        risk_score: Aggregate risk score (0.0-100.0) set by the Security
            Gateway; values >= ``settings.risk_threshold`` block execution.
        permissions_granted: Permission tokens approved for the current turn.
        security_flags: Human-readable labels for detected security concerns.
        session_id: Unique identifier for the current user session.
        created_at: ISO 8601 timestamp of when this state was first created.
        token_count: Running total of tokens consumed in this session.
        estimated_cost_usd: Running estimated cost in US dollars.
        iteration_count: Number of Reflexion self-critique iterations performed.
        metadata: Arbitrary key-value pairs for extension without schema changes.
        channel_context: Origin channel context when the request entered
            LightAgent through a messaging gateway (Telegram, Slack, Discord,
            …).  When non-``None`` the dict contains ``channel``, ``chat_id``
            and ``user_id`` so tools like ``cron_add`` can auto-route their
            output back to the originating chat.  ``None`` for CLI, dashboard,
            API, or any other non-channel invocation.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    current_agent: str
    next_agent: str | None
    task_plan: list[str]
    completed_tasks: list[str]
    pending_tasks: list[str]
    retrieved_docs: Annotated[list[dict[str, Any]], operator.add]
    doc_grades: list[float]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    tool_errors: list[dict[str, Any]]
    parallel_results: Annotated[list[dict[str, Any]], operator.add]
    dev_pipeline_modules: list[dict[str, Any]]
    risk_score: float
    permissions_granted: list[str]
    security_flags: list[str]
    session_id: str
    created_at: str
    token_count: int
    estimated_cost_usd: float
    iteration_count: int
    metadata: dict[str, Any]
    channel_context: dict[str, str] | None


def create_initial_state(session_id: str) -> AgentState:
    """Return a fresh AgentState with all fields at their zero/empty defaults.

    The ``current_agent`` is set to ``"supervisor"`` so that LangGraph begins
    execution at the supervisor node.  ``created_at`` is stamped with the
    current UTC time in ISO 8601 format.

    Each call returns a fully independent state object — mutable fields (lists,
    dicts) are created as new instances so that states never share references.

    Args:
        session_id: Unique identifier for the session this state belongs to.

    Returns:
        A fully-populated :class:`AgentState` ready to be passed to a
        ``StateGraph`` invocation.

    Example::

        state = create_initial_state(session_id="sess-abc123")
        graph.invoke(state)
    """
    logger.debug("creating_initial_state", session_id=session_id)
    return AgentState(
        messages=[],
        current_agent="supervisor",
        next_agent=None,
        task_plan=[],
        completed_tasks=[],
        pending_tasks=[],
        retrieved_docs=[],
        doc_grades=[],
        tool_results=[],
        tool_errors=[],
        parallel_results=[],
        dev_pipeline_modules=[],
        risk_score=0.0,
        permissions_granted=[],
        security_flags=[],
        session_id=session_id,
        created_at=datetime.now(UTC).isoformat(),
        token_count=0,
        estimated_cost_usd=0.0,
        iteration_count=0,
        metadata={},
        channel_context=None,
    )


__all__ = ["AgentState", "create_initial_state"]
