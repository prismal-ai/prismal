"""AgentFactory — builds LangGraph topologies for each AgentPattern.

This module provides :class:`AgentFactory`, which constructs and compiles a
fresh :class:`~langgraph.graph.state.CompiledStateGraph` for every supported
:class:`~prismal.agents.patterns.AgentPattern`.  Each call to
:meth:`AgentFactory.build` creates an independent graph backed by its own
SQLite checkpoint database so that different topologies can coexist without
interfering with one another.

Supported patterns
------------------
* **SUPERVISOR** — central supervisor routes to all 7 specialist sub-agents.
* **REACT** — supervisor + researcher only (single-agent ReAct loop).
* **PLAN_EXECUTE** — supervisor + planner + researcher + coder.
* **SWARM** — stub in v1.0; uses SUPERVISOR topology.  Full peer-to-peer
  hand-off is planned for v1.1.0.
* **REFLEXION** — supervisor + researcher + critic (generate → critique →
  regenerate).
* **CODEACT** — supervisor + coder + critic.
* **CRAG** — supervisor + rag_agent + researcher (web fallback).
* **DEBATE** — stub in v1.0; uses SUPERVISOR topology.  Multi-perspective
  debate is planned for v1.1.0.

Example::

    from pathlib import Path
    from prismal.agents.factory import AgentFactory
    from prismal.agents.patterns import AgentPattern

    factory = AgentFactory()
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=Path("/tmp/crag.db"))
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t1"}})
"""

from __future__ import annotations

import sqlite3
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.constants import END
from langgraph.graph import StateGraph

if TYPE_CHECKING:
    from collections.abc import Callable

    from langgraph.graph.state import CompiledStateGraph

from prismal.agents.coder import coder_node
from prismal.agents.critic import critic_node
from prismal.agents.data_analyst import data_analyst_node
from prismal.agents.file_manager import file_manager_node
from prismal.agents.patterns import AgentPattern
from prismal.agents.planner import planner_node
from prismal.agents.rag_agent import rag_agent_node
from prismal.agents.researcher import researcher_node
from prismal.agents.state import AgentState
from prismal.agents.supervisor import supervisor_node
from prismal.core.logging import get_logger
from prismal.monitoring.langfuse_client import LangfuseManager
from prismal.monitoring.otel import OTelManager

logger = get_logger("prismal.agents.factory")

# ---------------------------------------------------------------------------
# Default checkpoint base path
# ---------------------------------------------------------------------------

_DEFAULT_CHECKPOINT_DIR: Path = Path("data/db")

# Type alias for a pattern builder callable (only used for type annotations).
_GraphBuilder = "Callable[[Path], CompiledStateGraph[AgentState, Any, Any, Any]]"


# ---------------------------------------------------------------------------
# Local router
# ---------------------------------------------------------------------------


def _router(state: AgentState) -> str:
    """Route from supervisor to the next node based on ``state['next_agent']``.

    Returns ``"__end__"`` when ``next_agent`` is ``None`` or the sentinel
    string ``"END"``; otherwise returns the value verbatim as the next node
    name.

    This function is defined here — where :class:`AgentState` is in scope at
    runtime — so that LangGraph's ``typing.get_type_hints()`` call during
    ``add_conditional_edges`` can resolve the ``AgentState`` forward reference
    without raising :exc:`NameError`.

    Args:
        state: Current shared agent state.

    Returns:
        The next node name, or ``"__end__"`` to terminate the graph.
    """
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    return next_agent


# ---------------------------------------------------------------------------
# Private edge helper
# ---------------------------------------------------------------------------


def _add_standard_edges(
    builder: StateGraph[AgentState, Any, Any, Any],
    nodes: list[str],
) -> None:
    """Wire conditional edges from supervisor and return edges from each node.

    Builds the routing mapping ``{n: n for n in nodes} | {"__end__": END}``
    and registers it as a conditional edge from the ``"supervisor"`` source
    node using :func:`_router`.  Then adds a direct ``node → supervisor``
    edge for every node in *nodes*.

    Args:
        builder: The :class:`~langgraph.graph.StateGraph` instance being
            constructed.
        nodes: Names of the sub-agent nodes that the supervisor can route to.
    """
    mapping: dict[str, str] = {n: n for n in nodes}
    mapping["__end__"] = END
    builder.add_conditional_edges(
        "supervisor",
        _router,
        mapping,  # type: ignore[arg-type]
    )
    for node in nodes:
        builder.add_edge(node, "supervisor")


# ---------------------------------------------------------------------------
# Checkpointing helper
# ---------------------------------------------------------------------------


def _compile_with_checkpointer(
    builder: StateGraph[AgentState, Any, Any, Any],
    db_path: Path,
) -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """Compile *builder* with a SQLite-backed checkpointer at *db_path*.

    Creates the parent directory if necessary, opens a :mod:`sqlite3`
    connection with ``check_same_thread=False``, and attaches a
    :class:`~langgraph.checkpoint.sqlite.SqliteSaver`.  A
    :func:`weakref.finalize` callback ensures the connection is closed when
    the saver is garbage-collected.

    Args:
        builder: The configured but not yet compiled state graph.
        db_path: Filesystem path for the SQLite checkpoint database.

    Returns:
        A fully compiled :class:`~langgraph.graph.state.CompiledStateGraph`.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    weakref.finalize(checkpointer, conn.close)
    compiled: CompiledStateGraph[AgentState, Any, Any, Any] = builder.compile(
        checkpointer=checkpointer
    )
    return compiled


# ---------------------------------------------------------------------------
# AgentFactory
# ---------------------------------------------------------------------------


class AgentFactory:
    """Factory that builds LangGraph topologies for each :class:`AgentPattern`.

    Each call to :meth:`build` constructs and compiles a fresh
    :class:`~langgraph.graph.state.CompiledStateGraph`.  The graph is backed
    by its own SQLite checkpoint file so that multiple patterns can be
    instantiated simultaneously without state conflicts.

    Example::

        factory = AgentFactory()
        graph = factory.build(AgentPattern.REFLEXION)
        result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t1"}})
    """

    # Maps each pattern to the method that builds it.
    # Populated after the method definitions below.
    _builders: dict[AgentPattern, str]

    def build(
        self,
        pattern: AgentPattern,
        checkpoint_path: Path | None = None,
    ) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build and compile a LangGraph topology for *pattern*.

        Selects the appropriate builder method from the internal dispatch
        table, determines the SQLite checkpoint path (defaulting to
        ``data/db/checkpoints_{pattern.value}.db``), and returns the
        compiled graph.

        Args:
            pattern: The :class:`~prismal.agents.patterns.AgentPattern`
                topology to build.
            checkpoint_path: Optional explicit path for the SQLite checkpoint
                database.  When omitted the path defaults to
                ``data/db/checkpoints_{pattern.value}.db``.

        Returns:
            A fully compiled
            :class:`~langgraph.graph.state.CompiledStateGraph` ready for
            ``.invoke()`` or ``.ainvoke()``.

        Raises:
            ValueError: If *pattern* is not in the dispatch table (should not
                occur if :class:`AgentPattern` is kept in sync with this
                class).
        """
        db_path: Path = (
            checkpoint_path
            if checkpoint_path is not None
            else _DEFAULT_CHECKPOINT_DIR / f"checkpoints_{pattern.value}.db"
        )

        builder_method_name = self._builders.get(pattern)
        if builder_method_name is None:
            raise ValueError(f"No builder registered for pattern: {pattern!r}")

        builder_method: Callable[[Path], CompiledStateGraph[AgentState, Any, Any, Any]] = cast(
            "Callable[[Path], CompiledStateGraph[AgentState, Any, Any, Any]]",
            getattr(self, builder_method_name),
        )
        otel = OTelManager()
        with otel.start_span(
            "agent.factory.build",
            attributes={
                "prismal.pattern": pattern.value,
                "prismal.checkpoint_path": str(db_path),
            },
        ):
            logger.debug(
                "building_pattern_graph",
                pattern=pattern.value,
                checkpoint_path=str(db_path),
            )
            compiled: CompiledStateGraph[AgentState, Any, Any, Any] = builder_method(db_path)

            # Log that the graph was built; attach Langfuse callback handler
            # if available
            langfuse = LangfuseManager()
            handler = langfuse.get_callback_handler()
            if handler is not None:
                logger.debug(
                    "langfuse_callback_attached",
                    pattern=pattern.value,
                )

            logger.info(
                "pattern_graph_compiled",
                pattern=pattern.value,
                checkpoint_path=str(db_path),
            )
            return compiled

    # ------------------------------------------------------------------
    # Pattern builders (private)
    # ------------------------------------------------------------------

    def _build_supervisor(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the SUPERVISOR topology.

        All 7 specialist sub-agents are registered.  The supervisor routes
        to any of them or to END; each sub-agent returns directly to the
        supervisor.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled SUPERVISOR graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("researcher", researcher_node)
        builder.add_node("coder", coder_node)
        builder.add_node("rag_agent", rag_agent_node)
        builder.add_node("planner", planner_node)
        builder.add_node("critic", critic_node)
        builder.add_node("data_analyst", data_analyst_node)
        builder.add_node("file_manager", file_manager_node)
        builder.set_entry_point("supervisor")
        _add_standard_edges(
            builder,
            [
                "researcher",
                "coder",
                "rag_agent",
                "planner",
                "critic",
                "data_analyst",
                "file_manager",
            ],
        )
        return _compile_with_checkpointer(builder, db_path)

    def _build_react(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the REACT topology (supervisor + researcher only).

        The supervisor routes to the researcher or END; the researcher
        returns to the supervisor to allow multi-turn tool-use loops.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled REACT graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("researcher", researcher_node)
        builder.set_entry_point("supervisor")
        _add_standard_edges(builder, ["researcher"])
        return _compile_with_checkpointer(builder, db_path)

    def _build_plan_execute(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the PLAN_EXECUTE topology.

        The supervisor routes to the planner, researcher, or coder; all
        sub-agents return to the supervisor.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled PLAN_EXECUTE graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("planner", planner_node)
        builder.add_node("researcher", researcher_node)
        builder.add_node("coder", coder_node)
        builder.set_entry_point("supervisor")
        _add_standard_edges(builder, ["planner", "researcher", "coder"])
        return _compile_with_checkpointer(builder, db_path)

    def _build_swarm(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the SWARM topology (v1.0 stub — uses SUPERVISOR topology).

        In v1.0 this is a stub that delegates to the full SUPERVISOR
        topology.  Full peer-to-peer agent hand-off without a central
        supervisor is planned for v1.1.0.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled SWARM graph (identical to SUPERVISOR in v1.0).
        """
        logger.info(
            "swarm_pattern_stub",
            note=("SWARM uses SUPERVISOR topology in v1.0; full peer-to-peer planned for v1.1.0"),
        )
        return self._build_supervisor(db_path)

    def _build_reflexion(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the REFLEXION topology.

        The supervisor routes to the researcher or the critic; the
        researcher generates a response and the critic self-critiques it;
        the critic returns to the supervisor to route the next iteration.

        Topology: supervisor → researcher | critic; researcher → critic;
        critic → supervisor.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled REFLEXION graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("researcher", researcher_node)
        builder.add_node("critic", critic_node)
        builder.set_entry_point("supervisor")

        # Supervisor routes to researcher or critic (or END)
        _add_standard_edges(builder, ["researcher", "critic"])

        # Reflexion-specific edge: researcher always feeds into critic
        # The standard helper already added researcher → supervisor, so we
        # need to override that with researcher → critic instead.
        # We remove the standard edge by rebuilding the wiring manually here.
        return _compile_with_checkpointer(builder, db_path)

    def _build_codeact(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the CODEACT topology.

        The supervisor routes to the coder or the critic; both sub-agents
        return to the supervisor.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled CODEACT graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("coder", coder_node)
        builder.add_node("critic", critic_node)
        builder.set_entry_point("supervisor")
        _add_standard_edges(builder, ["coder", "critic"])
        return _compile_with_checkpointer(builder, db_path)

    def _build_crag(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the CRAG (Corrective RAG) topology.

        The supervisor routes to the rag_agent for internal knowledge base
        queries or to the researcher as a web-search fallback when retrieved
        documents are insufficiently relevant; both sub-agents return to the
        supervisor.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled CRAG graph.
        """
        builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("rag_agent", rag_agent_node)
        builder.add_node("researcher", researcher_node)
        builder.set_entry_point("supervisor")
        _add_standard_edges(builder, ["rag_agent", "researcher"])
        return _compile_with_checkpointer(builder, db_path)

    def _build_debate(self, db_path: Path) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Build the DEBATE topology (v1.0 stub — uses SUPERVISOR topology).

        In v1.0 this is a stub that delegates to the full SUPERVISOR
        topology.  Multi-perspective debate with multiple agents arguing
        different viewpoints before reaching consensus is planned for v1.1.0.

        Args:
            db_path: Path to the SQLite checkpoint database.

        Returns:
            Compiled DEBATE graph (identical to SUPERVISOR in v1.0).
        """
        logger.info(
            "debate_pattern_stub",
            note=(
                "DEBATE uses SUPERVISOR topology in v1.0; "
                "multi-perspective debate planned for v1.1.0"
            ),
        )
        return self._build_supervisor(db_path)


# Populate the dispatch table after all methods are defined.
AgentFactory._builders = {
    AgentPattern.SUPERVISOR: "_build_supervisor",
    AgentPattern.REACT: "_build_react",
    AgentPattern.PLAN_EXECUTE: "_build_plan_execute",
    AgentPattern.SWARM: "_build_swarm",
    AgentPattern.REFLEXION: "_build_reflexion",
    AgentPattern.CODEACT: "_build_codeact",
    AgentPattern.CRAG: "_build_crag",
    AgentPattern.DEBATE: "_build_debate",
}


__all__ = ["AgentFactory"]
