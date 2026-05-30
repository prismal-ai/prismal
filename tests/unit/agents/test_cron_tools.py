"""Unit tests for cron management LangChain tools (T-254)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    name: str = "test-job",
    schedule: str = "0 9 * * *",
    task: str = "do something",
    status: str = "active",
    next_run: datetime | None = None,
) -> MagicMock:
    """Return a mock CronJob with sensible defaults."""
    job = MagicMock()
    job.name = name
    job.schedule = schedule
    job.task = task
    job.status = status
    job.next_run = next_run or datetime(2099, 1, 1, 9, 0, 0)
    return job


# ---------------------------------------------------------------------------
# cron_add
# ---------------------------------------------------------------------------


class TestCronAdd:
    """Tests for the cron_add tool."""

    def test_cron_add_success(self) -> None:
        """Mock CronManager.add returning a job; verify output contains name and next_run."""
        from prismal.agents.tools import cron_add

        job = _make_job(name="daily-brief", next_run=datetime(2099, 6, 15, 9, 0, 0))

        with patch("prismal.scheduler.cron_manager.CronManager.add", return_value=job):
            result = cron_add.invoke(
                {"name": "daily-brief", "schedule": "0 9 * * *", "task": "Send a brief"}
            )

        assert "daily-brief" in result
        assert "2099-06-15" in result

    def test_cron_add_duplicate_returns_error(self) -> None:
        """When CronManager.add raises ValueError the tool returns an error string, not raise."""
        from prismal.agents.tools import cron_add

        with patch(
            "prismal.scheduler.cron_manager.CronManager.add",
            side_effect=ValueError("Cron job 'daily-brief' already exists"),
        ):
            result = cron_add.invoke(
                {"name": "daily-brief", "schedule": "0 9 * * *", "task": "Send a brief"}
            )

        assert "Failed to schedule job" in result
        assert "already exists" in result

    def test_cron_add_no_next_run(self) -> None:
        """When job.next_run is None the output contains 'unknown'."""
        from prismal.agents.tools import cron_add

        job = _make_job(name="no-time-job", next_run=None)
        job.next_run = None  # ensure None

        with patch("prismal.scheduler.cron_manager.CronManager.add", return_value=job):
            result = cron_add.invoke(
                {"name": "no-time-job", "schedule": "0 9 * * *", "task": "mystery"}
            )

        assert "unknown" in result

    def test_cron_add_passes_timezone_to_manager(self) -> None:
        """cron_add forwards the timezone parameter to CronManager.add()."""
        from prismal.agents.tools import cron_add

        job = _make_job(name="tz-job")

        with patch("prismal.scheduler.cron_manager.CronManager.add", return_value=job) as mock_add:
            cron_add.invoke(
                {
                    "name": "tz-job",
                    "schedule": "0 9 * * *",
                    "task": "Do something",
                    "timezone": "America/Caracas",
                }
            )

        mock_add.assert_called_once_with(
            "tz-job",
            "0 9 * * *",
            "Do something",
            max_retries=2,
            output_channel=None,
            output_target=None,
            timezone="America/Caracas",
        )

    def test_cron_add_includes_timezone_in_confirmation(self) -> None:
        """cron_add confirmation message includes timezone when provided."""
        from prismal.agents.tools import cron_add

        job = _make_job(name="tz-job")

        with patch("prismal.scheduler.cron_manager.CronManager.add", return_value=job):
            result = cron_add.invoke(
                {
                    "name": "tz-job",
                    "schedule": "0 9 * * *",
                    "task": "Do something",
                    "timezone": "Europe/Madrid",
                }
            )

        assert "Europe/Madrid" in result

    def test_cron_add_default_timezone_is_empty_string(self) -> None:
        """cron_add passes empty-string timezone when parameter is omitted."""
        from prismal.agents.tools import cron_add

        job = _make_job(name="no-tz-job")

        with patch("prismal.scheduler.cron_manager.CronManager.add", return_value=job) as mock_add:
            cron_add.invoke(
                {
                    "name": "no-tz-job",
                    "schedule": "0 9 * * *",
                    "task": "Task without tz",
                }
            )

        assert mock_add.call_args.kwargs.get("timezone", "") == ""


# ---------------------------------------------------------------------------
# cron_once
# ---------------------------------------------------------------------------


class TestCronOnce:
    """Tests for the cron_once tool."""

    def test_cron_once_success(self) -> None:
        """cron_once returns a confirmation with the scheduled time."""
        from prismal.agents.tools import cron_once

        job = _make_job(name="once-job", schedule="once:2099-12-01T10:00:00")

        with patch("prismal.scheduler.cron_manager.CronManager.add_once", return_value=job):
            result = cron_once.invoke(
                {
                    "name": "once-job",
                    "run_at": "2099-12-01 10:00:00",
                    "task": "Send reminder",
                }
            )

        assert "once-job" in result
        assert "2099-12-01" in result

    def test_cron_once_invalid_datetime_returns_error(self) -> None:
        """cron_once returns an error message for invalid datetime format."""
        from prismal.agents.tools import cron_once

        result = cron_once.invoke(
            {
                "name": "bad-job",
                "run_at": "not-a-date",
                "task": "irrelevant",
            }
        )

        assert "Invalid datetime" in result

    def test_cron_once_passes_timezone_to_manager(self) -> None:
        """cron_once forwards the timezone parameter to CronManager.add_once()."""
        from prismal.agents.tools import cron_once

        job = _make_job(name="tz-once", schedule="once:2099-12-01T10:00:00")

        with patch(
            "prismal.scheduler.cron_manager.CronManager.add_once", return_value=job
        ) as mock_add_once:
            cron_once.invoke(
                {
                    "name": "tz-once",
                    "run_at": "2099-12-01 10:00:00",
                    "task": "Ping",
                    "timezone": "America/New_York",
                }
            )

        assert mock_add_once.call_args.kwargs.get("timezone") == "America/New_York"

    def test_cron_once_includes_timezone_in_confirmation(self) -> None:
        """cron_once confirmation message includes timezone when provided."""
        from prismal.agents.tools import cron_once

        job = _make_job(name="tz-once", schedule="once:2099-12-01T10:00:00")

        with patch("prismal.scheduler.cron_manager.CronManager.add_once", return_value=job):
            result = cron_once.invoke(
                {
                    "name": "tz-once",
                    "run_at": "2099-12-01 10:00:00",
                    "task": "Alert",
                    "timezone": "Asia/Tokyo",
                }
            )

        assert "Asia/Tokyo" in result

    def test_cron_once_default_timezone_is_empty_string(self) -> None:
        """cron_once passes empty-string timezone when parameter is omitted."""
        from prismal.agents.tools import cron_once

        job = _make_job(name="no-tz-once", schedule="once:2099-12-01T10:00:00")

        with patch(
            "prismal.scheduler.cron_manager.CronManager.add_once", return_value=job
        ) as mock_add_once:
            cron_once.invoke(
                {
                    "name": "no-tz-once",
                    "run_at": "2099-12-01 10:00:00",
                    "task": "Task without tz",
                }
            )

        assert mock_add_once.call_args.kwargs.get("timezone", "") == ""


# ---------------------------------------------------------------------------
# cron_list
# ---------------------------------------------------------------------------


class TestCronList:
    """Tests for the cron_list tool."""

    def test_cron_list_empty(self) -> None:
        """When no jobs exist the tool returns the empty-state message."""
        from prismal.agents.tools import cron_list

        with patch("prismal.scheduler.cron_manager.CronManager.list_jobs", return_value=[]):
            result = cron_list.invoke({})

        assert result == "No cron jobs scheduled."

    def test_cron_list_with_jobs(self) -> None:
        """When two jobs exist both names appear in the output."""
        from prismal.agents.tools import cron_list

        jobs = [
            _make_job(name="job-alpha", next_run=datetime(2099, 1, 1, 9, 0)),
            _make_job(name="job-beta", next_run=datetime(2099, 3, 15, 12, 0)),
        ]

        with patch("prismal.scheduler.cron_manager.CronManager.list_jobs", return_value=jobs):
            result = cron_list.invoke({})

        assert "job-alpha" in result
        assert "job-beta" in result
        assert "NAME | SCHEDULE | STATUS | NEXT RUN | TASK" in result

    def test_cron_list_error_returns_string(self) -> None:
        """When CronManager raises the tool returns an error string."""
        from prismal.agents.tools import cron_list

        with patch(
            "prismal.scheduler.cron_manager.CronManager.list_jobs",
            side_effect=RuntimeError("db gone"),
        ):
            result = cron_list.invoke({})

        assert "Failed to list jobs" in result


# ---------------------------------------------------------------------------
# cron_pause
# ---------------------------------------------------------------------------


class TestCronPause:
    """Tests for the cron_pause tool."""

    def test_cron_pause_success(self) -> None:
        """Verify the result contains 'paused' on success."""
        from prismal.agents.tools import cron_pause

        with patch("prismal.scheduler.cron_manager.CronManager.pause") as mock_pause:
            result = cron_pause.invoke({"name": "daily-brief"})

        mock_pause.assert_called_once_with("daily-brief")
        assert "paused" in result
        assert "daily-brief" in result

    def test_cron_pause_not_found(self) -> None:
        """When CronManager.pause raises KeyError the result contains 'not found'."""
        from prismal.agents.tools import cron_pause

        with patch(
            "prismal.scheduler.cron_manager.CronManager.pause",
            side_effect=KeyError("daily-brief"),
        ):
            result = cron_pause.invoke({"name": "daily-brief"})

        assert "not found" in result
        assert "daily-brief" in result


# ---------------------------------------------------------------------------
# cron_resume
# ---------------------------------------------------------------------------


class TestCronResume:
    """Tests for the cron_resume tool."""

    def test_cron_resume_success(self) -> None:
        """Verify the result contains 'resumed' on success."""
        from prismal.agents.tools import cron_resume

        with patch("prismal.scheduler.cron_manager.CronManager.resume") as mock_resume:
            result = cron_resume.invoke({"name": "daily-brief"})

        mock_resume.assert_called_once_with("daily-brief")
        assert "resumed" in result
        assert "daily-brief" in result

    def test_cron_resume_not_found(self) -> None:
        """When CronManager.resume raises KeyError the result contains 'not found'."""
        from prismal.agents.tools import cron_resume

        with patch(
            "prismal.scheduler.cron_manager.CronManager.resume",
            side_effect=KeyError("daily-brief"),
        ):
            result = cron_resume.invoke({"name": "daily-brief"})

        assert "not found" in result
        assert "daily-brief" in result


# ---------------------------------------------------------------------------
# cron_remove
# ---------------------------------------------------------------------------


class TestCronRemove:
    """Tests for the cron_remove tool."""

    def test_cron_remove_success(self) -> None:
        """Verify the result contains 'removed' on success."""
        from prismal.agents.tools import cron_remove

        with patch("prismal.scheduler.cron_manager.CronManager.remove") as mock_remove:
            result = cron_remove.invoke({"name": "old-job"})

        mock_remove.assert_called_once_with("old-job")
        assert "removed" in result
        assert "old-job" in result

    def test_cron_remove_not_found(self) -> None:
        """When CronManager.remove raises KeyError the result contains 'not found'."""
        from prismal.agents.tools import cron_remove

        with patch(
            "prismal.scheduler.cron_manager.CronManager.remove",
            side_effect=KeyError("old-job"),
        ):
            result = cron_remove.invoke({"name": "old-job"})

        assert "not found" in result
        assert "old-job" in result


# ---------------------------------------------------------------------------
# Tool registry integration
# ---------------------------------------------------------------------------


class TestCronToolsRegistry:
    """Tests verifying cron tools are registered for the correct agents."""

    def _get_tool_names(self, agent_name: str) -> list[str]:
        """Return tool names for an agent with MCP + skills mocked to empty."""
        with (
            patch("prismal.agents.tool_registry.get_mcp_tools", return_value=[]),
            patch("prismal.agents.tool_registry.get_skill_tools", return_value=[]),
        ):
            from prismal.agents.tool_registry import get_tools_for_agent

            tools = get_tools_for_agent(agent_name)
            return [t.name for t in tools]

    def test_cron_manager_tools_registered_for_planner(self) -> None:
        """cron_add must appear in the planner's tool list."""
        tool_names = self._get_tool_names("planner")
        assert "cron_add" in tool_names

    def test_all_cron_tools_registered_for_planner(self) -> None:
        """All 5 cron tools must appear in the planner's tool list."""
        tool_names = self._get_tool_names("planner")
        for expected in (
            "cron_add",
            "cron_list",
            "cron_pause",
            "cron_resume",
            "cron_remove",
        ):
            assert expected in tool_names, f"{expected!r} missing from planner tools"

    def test_cron_manager_tools_registered_for_cron_manager(self) -> None:
        """cron_add must appear in the cron_manager agent's tool list."""
        tool_names = self._get_tool_names("cron_manager")
        assert "cron_add" in tool_names

    def test_all_cron_tools_registered_for_cron_manager(self) -> None:
        """All 5 cron tools must appear in the cron_manager agent's tool list."""
        tool_names = self._get_tool_names("cron_manager")
        for expected in (
            "cron_add",
            "cron_list",
            "cron_pause",
            "cron_resume",
            "cron_remove",
        ):
            assert expected in tool_names, f"{expected!r} missing from cron_manager tools"

    def test_cron_manager_tools_list_has_7_items(self) -> None:
        """CRON_MANAGER_TOOLS list must contain exactly 7 tools.

        The 7 tools are: get_current_time, cron_once, cron_add, cron_list,
        cron_pause, cron_resume, cron_remove.
        """
        from prismal.agents.tools import CRON_MANAGER_TOOLS

        assert len(CRON_MANAGER_TOOLS) == 7
