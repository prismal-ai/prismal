"""Unit tests for lightagent.scheduler.prefect_flows (T-080)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Task .fn tests (call underlying function directly, no Prefect context) ────


def test_index_document_task_returns_string() -> None:
    """_index_document_task.fn returns a status string containing filename."""
    from lightagent.scheduler.prefect_flows import _index_document_task

    result = _index_document_task.fn("/data/documents/report.txt")
    assert isinstance(result, str)
    assert "report.txt" in result


def test_index_document_task_result_format() -> None:
    """_index_document_task.fn returns 'indexed:<filename>' format."""
    from lightagent.scheduler.prefect_flows import _index_document_task

    result = _index_document_task.fn("/some/path/annual_report.pdf")
    assert result.startswith("indexed:")
    assert "annual_report.pdf" in result


def test_reload_config_task_returns_true() -> None:
    """_reload_config_task.fn returns True on success."""
    from lightagent.scheduler.prefect_flows import _reload_config_task

    assert _reload_config_task.fn() is True


def test_discover_skills_task_calls_reload_all() -> None:
    """_discover_skills_task.fn calls asyncio.run(manager.reload_all())."""
    from lightagent.scheduler.prefect_flows import _discover_skills_task

    with (
        patch("lightagent.scheduler.prefect_flows.SkillsManager") as mock_cls,
        patch("lightagent.scheduler.prefect_flows.asyncio.run") as mock_run,
    ):
        mock_mgr = MagicMock()
        mock_mgr.list_skills.return_value = []
        mock_cls.return_value = mock_mgr

        result = _discover_skills_task.fn()

    mock_run.assert_called_once()
    assert isinstance(result, list)


def test_discover_skills_task_returns_skill_names() -> None:
    """_discover_skills_task.fn returns list of skill names."""
    from lightagent.scheduler.prefect_flows import _discover_skills_task

    skill_a = MagicMock()
    skill_a.name = "weather"
    skill_b = MagicMock()
    skill_b.name = "web_search"

    with (
        patch("lightagent.scheduler.prefect_flows.SkillsManager") as mock_cls,
        patch("lightagent.scheduler.prefect_flows.asyncio.run"),
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
    from lightagent.scheduler.prefect_flows import document_index_flow

    with patch(
        "lightagent.scheduler.prefect_flows._index_document_task",
        return_value="indexed:report.txt",
    ) as mock_task:
        result = document_index_flow.fn("/data/documents/report.txt")

    mock_task.assert_called_once_with("/data/documents/report.txt")
    assert result == "indexed:report.txt"


def test_skill_discovery_flow_delegates_to_task() -> None:
    """skill_discovery_flow.fn delegates to _discover_skills_task."""
    from lightagent.scheduler.prefect_flows import skill_discovery_flow

    with patch(
        "lightagent.scheduler.prefect_flows._discover_skills_task",
        return_value=["weather", "web_search"],
    ) as mock_task:
        result = skill_discovery_flow.fn()

    mock_task.assert_called_once()
    assert result == ["weather", "web_search"]


def test_config_reload_flow_delegates_to_task() -> None:
    """config_reload_flow.fn delegates to _reload_config_task."""
    from lightagent.scheduler.prefect_flows import config_reload_flow

    with patch(
        "lightagent.scheduler.prefect_flows._reload_config_task",
        return_value=True,
    ) as mock_task:
        result = config_reload_flow.fn()

    mock_task.assert_called_once()
    assert result is True


def test_agent_run_flow_returns_string() -> None:
    """agent_run_flow.fn returns a result string."""
    from lightagent.scheduler.prefect_flows import agent_run_flow

    result = agent_run_flow.fn("Summarise quarterly report")
    assert isinstance(result, str)
    assert len(result) > 0


def test_agent_run_flow_with_empty_task() -> None:
    """agent_run_flow.fn handles empty task description gracefully."""
    from lightagent.scheduler.prefect_flows import agent_run_flow

    result = agent_run_flow.fn("")
    assert "no-op" in result


# ── Prefect object type checks ────────────────────────────────────────────────


def test_flows_are_prefect_flows() -> None:
    """All exported symbols are Prefect Flow instances."""
    from prefect import Flow

    from lightagent.scheduler.prefect_flows import (
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
    from lightagent.scheduler.prefect_flows import _index_document_task

    assert _index_document_task.retries >= 1


def test_discover_skills_task_has_retries() -> None:
    """_discover_skills_task is configured with at least 1 retry."""
    from lightagent.scheduler.prefect_flows import _discover_skills_task

    assert _discover_skills_task.retries >= 1
