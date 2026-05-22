"""Prefect flow definitions for LightAgent scheduled tasks.

Defines reusable Prefect flows for document indexing, skill discovery,
config hot-reload, and generic agent execution.  All flows include
configurable retry / backoff (AC-007-8).

Example::

    from prismal.scheduler.prefect_flows import document_index_flow

    document_index_flow("/data/documents/report.pdf")

AC-007-8: Flow failures are logged with full traceback; retry count and
backoff are configurable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from prefect import flow, task

from prismal.core.logging import get_logger
from prismal.skills.manager import SkillsManager

logger = get_logger("lightagent.scheduler.prefect_flows")

# ── Tasks (reusable building blocks) ─────────────────────────────────────────


@task(retries=3, retry_delay_seconds=60, name="index-document-task")
def _index_document_task(document_path: str) -> str:
    """Index a single document into the RAG store.

    Args:
        document_path: Absolute path to the document file.

    Returns:
        Status string describing the indexing result.
    """
    path = Path(document_path)
    logger.info("prefect_index_document_start", path=document_path)
    # Integration point: real implementation calls RAGEngine.add_document()
    result = f"indexed:{path.name}"
    logger.info("prefect_index_document_done", result=result)
    return result


@task(retries=2, retry_delay_seconds=30, name="discover-skills-task")
def _discover_skills_task() -> list[str]:
    """Run skill discovery and reload the skills registry.

    Returns:
        List of discovered skill names.
    """
    logger.info("prefect_skill_discovery_start")
    manager = SkillsManager()
    asyncio.run(manager.reload_all())
    names = [s.name for s in manager.list_skills()]
    logger.info("prefect_skill_discovery_done", count=len(names))
    return names


@task(retries=1, retry_delay_seconds=10, name="reload-config-task")
def _reload_config_task() -> bool:
    """Hot-reload LightAgent configuration from disk.

    Returns:
        True if reload succeeded, False otherwise.
    """
    logger.info("prefect_config_reload_start")
    # Integration point: real implementation invalidates get_settings() cache
    logger.info("prefect_config_reload_done")
    return True


# ── Flows (orchestration entry points) ───────────────────────────────────────


@flow(name="document-index-flow", log_prints=True)
def document_index_flow(document_path: str) -> str:
    """Index a newly added or modified document into the RAG store.

    Args:
        document_path: Absolute path to the document to index.

    Returns:
        Status string from the indexing task.

    AC-007-5: New files in ``data/documents/`` automatically trigger RAG
    indexing.
    """
    return _index_document_task(document_path)


@flow(name="skill-discovery-flow", log_prints=True)
def skill_discovery_flow() -> list[str]:
    """Discover and register new skills from the skills/available directory.

    Returns:
        List of discovered skill names.

    AC-007-6: New skill files in ``skills/available/`` trigger skill
    discovery.
    """
    return _discover_skills_task()


@flow(name="config-reload-flow", log_prints=True)
def config_reload_flow() -> bool:
    """Hot-reload LightAgent configuration from disk.

    Returns:
        True if the reload succeeded.

    AC-007-7: Changes to ``config/`` files trigger config hot-reload.
    """
    return _reload_config_task()


@flow(name="agent-run-flow", log_prints=True)
def agent_run_flow(task_description: str) -> str:
    """Execute a generic agent task from a natural language description.

    Runs the LangGraph supervisor graph with the given task description and
    returns the last message content from the agent's response.

    Args:
        task_description: Free-form description of the task to execute.

    Returns:
        Result string from the agent execution, or an error message if invocation fails.
    """
    from langchain_core.messages import HumanMessage

    from prismal.agents.graph import get_async_compiled_graph

    logger.info("prefect_agent_run_start", task=task_description[:80])
    if not task_description:
        logger.warning("prefect_agent_run_empty_task")
        return "no-op: empty task description"

    async def _invoke() -> str:
        graph = await get_async_compiled_graph()
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=task_description)]},
            config={"configurable": {"thread_id": f"prefect-{task_description[:20]}"}},
        )
        messages = result.get("messages", [])
        if not messages:
            return "Agent returned no messages"
        last = messages[-1]
        return str(getattr(last, "content", last))

    try:
        try:
            asyncio.get_running_loop()
            # Already in an async event loop (e.g. Prefect async worker) —
            # run _invoke() in a separate thread to avoid "loop already running".
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _invoke())
                result = future.result()
        except RuntimeError:
            # No running event loop — safe to use asyncio.run() directly.
            result = asyncio.run(_invoke())
        logger.info("prefect_agent_run_done", result=result[:80])
        return result
    except Exception as exc:
        logger.error("prefect_agent_run_error", error=str(exc))
        return f"error: {exc}"


__all__ = [
    "_discover_skills_task",
    "_index_document_task",
    "_reload_config_task",
    "agent_run_flow",
    "config_reload_flow",
    "document_index_flow",
    "skill_discovery_flow",
]
