"""Unit tests for prismal.scheduler.prefect_flows (T-080)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

# ── Task .fn tests (call underlying function directly, no Prefect context) ────


def test_index_document_task_returns_string() -> None:
    """_index_document_task.fn returns a status string containing filename."""
    from prismal.scheduler.prefect_flows import _index_document_task

    result = _index_document_task.fn("/data/documents/report.txt")
    assert isinstance(result, str)
    assert "report.txt" in result


def test_index_document_task_result_format() -> None:
    """_index_document_task.fn returns 'indexed:<filename>' format."""
    from prismal.scheduler.prefect_flows import _index_document_task

    result = _index_document_task.fn("/some/path/annual_report.pdf")
    assert result.startswith("indexed:")
    assert "annual_report.pdf" in result


def test_reload_config_task_returns_true() -> None:
    """_reload_config_task.fn returns True on success."""
    from prismal.scheduler.prefect_flows import _reload_config_task

    assert _reload_config_task.fn() is True


def test_discover_skills_task_calls_reload_all() -> None:
    """_discover_skills_task.fn calls asyncio.run(manager.reload_all())."""
    from prismal.scheduler.prefect_flows import _discover_skills_task

    with (
        patch("prismal.scheduler.prefect_flows.SkillsManager") as mock_cls,
        patch("prismal.scheduler.prefect_flows.asyncio.run") as mock_run,
    ):
        mock_mgr = MagicMock()
        mock_mgr.list_skills.return_value = []
        mock_cls.return_value = mock_mgr

        result = _discover_skills_task.fn()

    mock_run.assert_called_once()
    assert isinstance(result, list)


def test_discover_skills_task_returns_skill_names() -> None:
    """_discover_skills_task.fn returns list of skill names."""
    from prismal.scheduler.prefect_flows import _discover_skills_task

    skill_a = MagicMock()
    skill_a.name = "weather"
    skill_b = MagicMock()
    skill_b.name = "web_search"

    with (
        patch("prismal.scheduler.prefect_flows.SkillsManager") as mock_cls,
        patch("prismal.scheduler.prefect_flows.asyncio.run"),
    ):
        mock_mgr = MagicMock()
        mock_mgr.list_skills.return_value = [skill_a, skill_b]
        mock_cls.return_value = mock_mgr

        result = _discover_skills_task.fn()

    assert "weather" in result
    assert "web_search" in result


# ── Flow .fn tests (mock tasks to avoid Prefect API logging outside context) ──


def test_document_index_flow_delegates_to_task() -> None:
    """document_index_flow.fn delegates to _index_document_task."""
    from prismal.scheduler.prefect_flows import document_index_flow

    with patch(
        "prismal.scheduler.prefect_flows._index_document_task",
        return_value="indexed:report.txt",
    ) as mock_task:
        result = document_index_flow.fn("/data/documents/report.txt")

    mock_task.assert_called_once_with("/data/documents/report.txt")
    assert result == "indexed:report.txt"


def test_skill_discovery_flow_delegates_to_task() -> None:
    """skill_discovery_flow.fn delegates to _discover_skills_task."""
    from prismal.scheduler.prefect_flows import skill_discovery_flow

    with patch(
        "prismal.scheduler.prefect_flows._discover_skills_task",
        return_value=["weather", "web_search"],
    ) as mock_task:
        result = skill_discovery_flow.fn()

    mock_task.assert_called_once()
    assert result == ["weather", "web_search"]


def test_config_reload_flow_delegates_to_task() -> None:
    """config_reload_flow.fn delegates to _reload_config_task."""
    from prismal.scheduler.prefect_flows import config_reload_flow

    with patch(
        "prismal.scheduler.prefect_flows._reload_config_task",
        return_value=True,
    ) as mock_task:
        result = config_reload_flow.fn()

    mock_task.assert_called_once()
    assert result is True


def test_agent_run_flow_returns_string() -> None:
    """agent_run_flow.fn returns a result string from the graph."""

    from prismal.scheduler.prefect_flows import agent_run_flow

    def fake_run_drain(coro: object) -> str:  # type: ignore[type-arg]
        # Close the coroutine so Python doesn't warn about unawaited coroutine.
        import inspect

        if inspect.iscoroutine(coro):
            coro.close()  # type: ignore[union-attr]
        return "Agent response text"

    with patch(
        "prismal.scheduler.prefect_flows.asyncio.run", side_effect=fake_run_drain
    ) as mock_run:
        result = agent_run_flow.fn("Summarise quarterly report")

    mock_run.assert_called_once()
    assert isinstance(result, str)
    assert len(result) > 0


def test_agent_run_flow_with_empty_task() -> None:
    """agent_run_flow.fn handles empty task description gracefully."""
    from prismal.scheduler.prefect_flows import agent_run_flow

    result = agent_run_flow.fn("")
    assert "no-op" in result


def test_agent_run_flow_calls_graph() -> None:
    """agent_run_flow.fn calls asyncio.run with a coroutine that invokes the graph.

    We patch asyncio.run to capture and execute the coroutine directly using a
    fresh event loop, while get_async_compiled_graph is patched at its source
    module so the local import inside agent_run_flow resolves to the mock.
    """
    import asyncio as _asyncio

    from prismal.scheduler.prefect_flows import agent_run_flow

    mock_msg = MagicMock()
    mock_msg.content = "done"
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"messages": [mock_msg]}
    mock_get_graph = AsyncMock(return_value=mock_graph)

    def fake_asyncio_run(coro: object) -> str:  # type: ignore[type-arg]
        loop = _asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)  # type: ignore[arg-type]
        finally:
            loop.close()

    with (
        patch("prismal.agents.graph.get_async_compiled_graph", mock_get_graph),
        patch(
            "prismal.scheduler.prefect_flows.asyncio.run",
            side_effect=fake_asyncio_run,
        ),
    ):
        result = agent_run_flow.fn("fetch latest stock prices")

    assert result == "done"
    mock_graph.ainvoke.assert_called_once()
    call_kwargs = mock_graph.ainvoke.call_args[0][0]
    assert call_kwargs["messages"][0].content == "fetch latest stock prices"


def test_agent_run_flow_returns_error_string_on_exception() -> None:
    """agent_run_flow.fn returns an error string instead of raising on failure."""
    import inspect

    from prismal.scheduler.prefect_flows import agent_run_flow

    def fake_run_raise(coro: object) -> str:  # type: ignore[type-arg]
        if inspect.iscoroutine(coro):
            coro.close()  # type: ignore[union-attr]
        raise RuntimeError("graph unavailable")

    with patch(
        "prismal.scheduler.prefect_flows.asyncio.run",
        side_effect=fake_run_raise,
    ):
        result = agent_run_flow.fn("do something")

    assert isinstance(result, str)
    assert result.startswith("error:")
    assert "graph unavailable" in result


# ── Prefect object type checks ────────────────────────────────────────────────


def test_flows_are_prefect_flows() -> None:
    """All exported symbols are Prefect Flow instances."""
    from prefect import Flow

    from prismal.scheduler.prefect_flows import (
        agent_run_flow,
        config_reload_flow,
        document_index_flow,
        skill_discovery_flow,
    )

    for flow_obj in (
        document_index_flow,
        skill_discovery_flow,
        config_reload_flow,
        agent_run_flow,
    ):
        assert isinstance(flow_obj, Flow)


def test_document_index_task_has_retries() -> None:
    """_index_document_task is configured with at least 1 retry."""
    from prismal.scheduler.prefect_flows import _index_document_task

    assert _index_document_task.retries >= 1


def test_discover_skills_task_has_retries() -> None:
    """_discover_skills_task is configured with at least 1 retry."""
    from prismal.scheduler.prefect_flows import _discover_skills_task

    assert _discover_skills_task.retries >= 1
