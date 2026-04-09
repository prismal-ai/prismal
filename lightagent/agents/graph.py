"""
LangGraph SUPERVISOR state machine assembly.

Builds and compiles the complete multi-agent graph for LightAgent.  The graph
follows the SUPERVISOR pattern: a central supervisor node routes each turn to
the most appropriate specialist sub-agent; each sub-agent processes the request
and returns control to the supervisor; the supervisor routes to END when the
task is complete.

Checkpointing is handled by a SQLite-backed
:class:`~langgraph.checkpoint.sqlite.SqliteSaver` that persists conversation
state across invocations.

Example::

    from lightagent.agents.graph import get_compiled_graph

    graph = get_compiled_graph()
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t1"}})
"""

from __future__ import annotations

import sqlite3
import weakref
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.constants import END
from langgraph.graph import StateGraph

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from lightagent.agents.codeact_agent import codeact_node
from lightagent.agents.coder import coder_node
from lightagent.agents.critic import critic_node
from lightagent.agents.cron_manager import cron_manager_node
from lightagent.agents.data_analyst import data_analyst_node
from lightagent.agents.file_manager import file_manager_node
from lightagent.agents.parallel_research import (
    parallel_researcher_node,
    parallel_researcher_worker,
    research_aggregator_node,
    research_dispatcher,
)
from lightagent.agents.planner import planner_node
from lightagent.agents.rag_agent import rag_agent_node
from lightagent.agents.researcher import researcher_node
from lightagent.agents.skill_manager import skill_manager_node
from lightagent.agents.state import AgentState
from lightagent.agents.supervisor import supervisor_node, supervisor_router
from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger

logger = get_logger("lightagent.agents.graph")


# ---------------------------------------------------------------------------
# Router wrapper
# ---------------------------------------------------------------------------
# LangGraph calls ``typing.get_type_hints()`` on the routing function when
# wiring conditional edges.  ``supervisor_router`` is defined in a module that
# uses ``from __future__ import annotations`` and guards the ``AgentState``
# import behind ``TYPE_CHECKING``, so ``AgentState`` is absent from that
# module's globals at runtime and ``get_type_hints`` raises ``NameError``.
#
# Defining a thin wrapper here — where ``AgentState`` *is* in scope — resolves
# the forward reference correctly.
def _supervisor_router(state: AgentState) -> str:
    """
    Delegate to supervisor_router with AgentState resolvable at runtime.

    This wrapper exists solely to satisfy LangGraph's ``get_type_hints`` call
    during conditional-edge registration.  All routing logic remains in
    :func:`~lightagent.agents.supervisor.supervisor_router`.

    Args:
        state: Current agent state.

    Returns:
        The next node name or ``"__end__"``.
    """
    return supervisor_router(state)


def _research_dispatcher(state: AgentState) -> Any:  # noqa: ANN401 -- Send | str return
    """Forward to the parallel research dispatcher with AgentState in scope.

    Same pattern as :func:`_supervisor_router` — LangGraph calls
    ``typing.get_type_hints()`` on the routing function and the underlying
    dispatcher's annotation references ``AgentState`` via a forward
    reference that only resolves in this module.

    Args:
        state: Current agent state.

    Returns:
        Either a list of ``Send`` objects (one per parallel worker) or the
        ``on_empty`` node name as a string.
    """
    return research_dispatcher(state)


# Default path for the SQLite checkpoint database
_DEFAULT_CHECKPOINT_PATH: Path = Path("data/db/checkpoints.db")

# Keep a module-level reference to any async context managers opened by
# ``build_checkpointer()`` so that the underlying connections remain alive
# for the lifetime of the process. LangGraph's ``from_conn_string`` helpers
# are async context managers; if we drop the CM the connection closes and
# subsequent checkpoint operations would raise.
_checkpointer_cms: list[Any] = []


def _extract_sqlite_path(db_url: str) -> str:
    """Extract the filesystem path from a SQLAlchemy-style sqlite URL.

    Supports both ``sqlite:///path/to.db`` and
    ``sqlite+aiosqlite:///path/to.db``. Returns ``":memory:"`` for an empty
    path or when the URL is not a sqlite URL.

    Args:
        db_url: The DB URL to parse.

    Returns:
        The extracted filesystem path or ``":memory:"``.
    """
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if db_url.startswith(prefix):
            path = db_url.removeprefix(prefix)
            return path or ":memory:"
    return ":memory:"


async def build_checkpointer(db_url: str | None = None) -> Any:  # noqa: ANN401 — no common base type
    """Build an async checkpointer based on the configured DB URL.

    Selects between ``AsyncPostgresSaver`` (for ``postgresql://``-prefixed
    URLs) and ``AsyncSqliteSaver`` (for ``sqlite:///``-prefixed URLs, or as
    an in-memory fallback). The returned checkpointer has already had
    ``setup()`` invoked so LangGraph's checkpoint tables are guaranteed to
    exist before the first write.

    Args:
        db_url: Optional explicit DB URL. When ``None``, the value from
            ``get_settings().db_url`` is used.

    Returns:
        A fully-initialized async checkpointer ready to pass to
        ``StateGraph.compile(checkpointer=...)``.

    Raises:
        ImportError: If ``db_url`` selects the PostgreSQL backend but
            ``langgraph-checkpoint-postgres`` is not installed.
    """
    url = db_url if db_url is not None else (get_settings().db_url or "")

    if url.startswith(("postgresql://", "postgresql+asyncpg://", "postgres://")):
        try:
            from langgraph.checkpoint.postgres.aio import (  # type: ignore[import-not-found]
                AsyncPostgresSaver,
            )
        except ImportError as e:  # pragma: no cover — exercised via mocked tests
            raise ImportError(
                "PostgreSQL checkpointer backend requires the optional "
                "'langgraph-checkpoint-postgres' package. Install it with "
                "`uv pip install langgraph-checkpoint-postgres`."
            ) from e

        # AsyncPostgresSaver accepts plain ``postgresql://`` URIs (psycopg).
        normalized = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        cm = AsyncPostgresSaver.from_conn_string(normalized)
        saver = await cm.__aenter__()
        await saver.setup()
        _checkpointer_cms.append(cm)
        logger.info("checkpointer_built", backend="postgresql")
        return saver

    # SQLite branch (also the fallback for empty/unknown URLs).
    import aiosqlite

    sqlite_path = _extract_sqlite_path(url)
    if sqlite_path != ":memory:":
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(sqlite_path)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    logger.info("checkpointer_built", backend="sqlite", path=sqlite_path)
    return saver


def build_supervisor_graph(
    checkpoint_path: Path | None = None,
    checkpointer: Any = None,  # noqa: ANN401 — no common base type for LangGraph checkpointers
    dev_pipeline_graph: Any = None,  # noqa: ANN401 — CompiledStateGraph for dev_pipeline subgraph (Phase 24)
    ml_pipeline_graph: Any = None,  # noqa: ANN401 — CompiledStateGraph for ml_pipeline subgraph (Phase 26)
    financial_analyst_graph: Any = None,  # noqa: ANN401 — CompiledStateGraph for financial_analyst subgraph (Phase 27)
) -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """
    Build and compile the LangGraph SUPERVISOR state machine.

    Constructs a :class:`~langgraph.graph.StateGraph` wired with the supervisor
    node as the entry point, all seven specialist sub-agent nodes, conditional
    edges from the supervisor (via
    :func:`~lightagent.agents.supervisor.supervisor_router`),
    and direct return edges from every sub-agent back to the supervisor.

    The graph is compiled with a SQLite-backed checkpointer so that conversation
    state is persisted across invocations.  The parent directory of
    ``checkpoint_path`` is created automatically if it does not exist.

    Args:
        checkpoint_path: Path to the SQLite database file used for LangGraph
            checkpointing.  Defaults to ``data/db/checkpoints.db`` relative to
            the current working directory.  Ignored when ``checkpointer`` is
            provided.
        checkpointer: Optional pre-built checkpointer (e.g. AsyncSqliteSaver
            for async usage).  When provided, ``checkpoint_path`` is ignored
            and no SQLite connection is opened internally.
        dev_pipeline_graph: Optional pre-compiled dev_pipeline subgraph
            (Phase 24).  When provided together with ``get_settings().enable_subgraphs``
            being ``True``, this node is added to the supervisor graph so that
            the supervisor can route to it.  The caller is responsible for
            building this asynchronously before calling this function.
        ml_pipeline_graph: Optional pre-compiled ml_pipeline subgraph
            (Phase 26).  When provided together with ``get_settings().enable_subgraphs``
            being ``True``, this node is added to the supervisor graph so that
            the supervisor can route to it.  The caller is responsible for
            building this asynchronously before calling this function.
        financial_analyst_graph: Optional pre-compiled financial_analyst subgraph
            (Phase 27).  When provided together with ``get_settings().enable_subgraphs``
            being ``True``, this node is added to the supervisor graph so that
            the supervisor can route to it.  The caller is responsible for
            building this asynchronously before calling this function.

    Returns:
        A fully compiled :class:`~langgraph.graph.state.CompiledStateGraph`
        ready for use with ``.invoke()`` or ``.ainvoke()``.
    """
    db_path: Path = (
        checkpoint_path if checkpoint_path is not None else _DEFAULT_CHECKPOINT_PATH
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug("building_supervisor_graph", checkpoint_path=str(db_path))

    # ------------------------------------------------------------------ #
    # Graph definition                                                     #
    # ------------------------------------------------------------------ #
    builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)

    # Register all nodes
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("coder", coder_node)
    # Phase 38: CodeAct agent — direct Python code generation with
    # auto-correction. Runs alongside the classic ``coder`` node; the
    # supervisor picks between them based on task complexity.
    builder.add_node("codeact", codeact_node)
    builder.add_node("rag_agent", rag_agent_node)
    builder.add_node("planner", planner_node)
    builder.add_node("critic", critic_node)
    builder.add_node("data_analyst", data_analyst_node)
    builder.add_node("file_manager", file_manager_node)
    builder.add_node("skill_manager", skill_manager_node)
    builder.add_node("cron_manager", cron_manager_node)

    # Phase 34: parallel research fan-out / fan-in.
    # ``parallel_researcher`` is a passthrough that anchors the conditional
    # dispatcher edges; ``parallel_researcher_worker`` runs concurrently for
    # each pending task; ``research_aggregator`` merges the results once all
    # workers have finished.
    builder.add_node("parallel_researcher", parallel_researcher_node)
    builder.add_node("parallel_researcher_worker", parallel_researcher_worker)
    builder.add_node("research_aggregator", research_aggregator_node)

    # Phase 24: dev_pipeline subgraph node (opt-in via enable_subgraphs setting).
    # The compiled subgraph must be built externally (async) and passed in.
    _include_dev_pipeline = (
        get_settings().enable_subgraphs and dev_pipeline_graph is not None
    )
    if _include_dev_pipeline:
        builder.add_node("dev_pipeline", dev_pipeline_graph)

    # Phase 26: ml_pipeline subgraph node (opt-in via enable_subgraphs setting).
    _include_ml_pipeline = (
        get_settings().enable_subgraphs and ml_pipeline_graph is not None
    )
    if _include_ml_pipeline:
        builder.add_node("ml_pipeline", ml_pipeline_graph)

    # Phase 27: financial_analyst subgraph node (opt-in via enable_subgraphs setting).
    _include_financial_analyst = (
        get_settings().enable_subgraphs and financial_analyst_graph is not None
    )
    if _include_financial_analyst:
        builder.add_node("financial_analyst", financial_analyst_graph)

    # Entry point
    builder.set_entry_point("supervisor")

    # Conditional edges: supervisor → sub-agent or END
    conditional_edges: dict[str, str | object] = {
        "researcher": "researcher",
        "coder": "coder",
        "codeact": "codeact",
        "rag_agent": "rag_agent",
        "planner": "planner",
        "critic": "critic",
        "data_analyst": "data_analyst",
        "file_manager": "file_manager",
        "skill_manager": "skill_manager",
        "cron_manager": "cron_manager",
        "parallel_researcher": "parallel_researcher",
        "__end__": END,
    }
    if _include_dev_pipeline:
        conditional_edges["dev_pipeline"] = "dev_pipeline"
    if _include_ml_pipeline:
        conditional_edges["ml_pipeline"] = "ml_pipeline"
    if _include_financial_analyst:
        conditional_edges["financial_analyst"] = "financial_analyst"

    builder.add_conditional_edges(
        "supervisor",
        _supervisor_router,
        conditional_edges,
    )

    # Direct edges: every sub-agent returns to supervisor
    base_members = (
        "researcher",
        "coder",
        "codeact",
        "rag_agent",
        "planner",
        "critic",
        "data_analyst",
        "file_manager",
        "skill_manager",
        "cron_manager",
    )
    for member in base_members:
        builder.add_edge(member, "supervisor")

    # Phase 34: parallel research wiring.
    # ``parallel_researcher`` fans out via Send() to ``parallel_researcher_worker``;
    # if there are no pending tasks the dispatcher routes straight to
    # ``research_aggregator`` (its ``on_empty`` target).  Each worker invocation
    # then converges back on ``research_aggregator``, which routes to supervisor.
    builder.add_conditional_edges(
        "parallel_researcher",
        _research_dispatcher,
        {
            "parallel_researcher_worker": "parallel_researcher_worker",
            "research_aggregator": "research_aggregator",
        },
    )
    builder.add_edge("parallel_researcher_worker", "research_aggregator")
    builder.add_edge("research_aggregator", "supervisor")

    if _include_dev_pipeline:
        builder.add_edge("dev_pipeline", "supervisor")
    if _include_ml_pipeline:
        builder.add_edge("ml_pipeline", "supervisor")
    if _include_financial_analyst:
        builder.add_edge("financial_analyst", "supervisor")

    # ------------------------------------------------------------------ #
    # Compile with checkpointing                                           #
    # ------------------------------------------------------------------ #
    # When a checkpointer is supplied externally (e.g. AsyncSqliteSaver
    # created in an async context), use it directly.  Otherwise fall back
    # to the sync SqliteSaver so that non-async callers still work.
    if checkpointer is None:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        weakref.finalize(checkpointer, conn.close)

    compiled: CompiledStateGraph[AgentState, Any, Any, Any] = builder.compile(
        checkpointer=checkpointer
    )

    logger.info("supervisor_graph_compiled", checkpoint_path=str(db_path))
    return compiled


@lru_cache(maxsize=1)
def get_compiled_graph() -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """
    Return the compiled LangGraph supervisor graph as a cached singleton.

    On the first call the graph is built using the default checkpoint path
    (``data/db/checkpoints.db``).  Subsequent calls return the same object
    without rebuilding, making this safe to call from multiple modules.

    Returns:
        The shared :class:`~langgraph.graph.state.CompiledStateGraph` instance.
    """
    logger.debug("get_compiled_graph_cache_miss")
    return build_supervisor_graph()


# ---------------------------------------------------------------------------
# Async graph singleton (for channel bots / ainvoke callers)
# ---------------------------------------------------------------------------
_async_graph: CompiledStateGraph[AgentState, Any, Any, Any] | None = None


async def get_async_compiled_graph() -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """
    Return the compiled LangGraph supervisor graph with an async checkpointer.

    Uses :class:`~langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver` so that
    ``graph.ainvoke()`` works correctly in async contexts (e.g. channel bots).

    The graph is built once and cached as a module-level singleton; subsequent
    calls return the same object without reopening the database.

    Returns:
        A compiled graph backed by :class:`AsyncSqliteSaver`.
    """
    global _async_graph
    if _async_graph is not None:
        return _async_graph

    # Phase 36: the checkpointer backend is now selected from
    # ``settings.db_url`` via ``build_checkpointer()``. For the default
    # SQLite configuration this behaves identically to the previous
    # hardcoded path; for ``postgresql://`` URLs it returns an
    # ``AsyncPostgresSaver`` with its tables already created.
    checkpointer = await build_checkpointer(get_settings().db_url)

    # Phase 24: build dev_pipeline subgraph asynchronously when enabled.
    dev_pipeline_graph: Any = None  # ANN401: no common base type for compiled subgraphs
    ml_pipeline_graph: Any = None  # ANN401: no common base type for compiled subgraphs
    financial_analyst_graph: Any = None  # ANN401: no common base type
    if get_settings().enable_subgraphs:
        from lightagent.agents.subgraphs.dev_pipeline.builder import (
            get_compiled_dev_pipeline,
        )
        from lightagent.agents.subgraphs.ml_pipeline.builder import (
            get_compiled_ml_pipeline,
        )

        dev_pipeline_graph = await get_compiled_dev_pipeline()
        # Phase 26: build ml_pipeline subgraph asynchronously when enabled.
        ml_pipeline_graph = await get_compiled_ml_pipeline()
        # Phase 27: build financial_analyst subgraph asynchronously when enabled.
        from lightagent.agents.subgraphs.financial.builder import (
            get_compiled_financial_analyst,
        )

        financial_analyst_graph = await get_compiled_financial_analyst()

    _async_graph = build_supervisor_graph(
        checkpointer=checkpointer,
        dev_pipeline_graph=dev_pipeline_graph,
        ml_pipeline_graph=ml_pipeline_graph,
        financial_analyst_graph=financial_analyst_graph,
    )
    logger.debug("async_graph_initialized")
    return _async_graph


def list_session_ids() -> list[str]:
    """
    Return all distinct thread IDs from the LangGraph SQLite checkpointer.

    Reads directly from the checkpointer's SQLite database.  Returns an
    empty list if the database does not exist yet.

    Returns:
        Sorted list of session ID strings.
    """
    db = _DEFAULT_CHECKPOINT_PATH
    if not db.exists():
        return []
    try:
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


__all__ = [
    "build_checkpointer",
    "build_supervisor_graph",
    "get_async_compiled_graph",
    "get_compiled_graph",
    "list_session_ids",
]
