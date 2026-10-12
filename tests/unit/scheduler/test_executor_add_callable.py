"""``CronExecutor.add_callable`` — the public way to schedule a Python callable.

Before this API, callers (memory v2 consolidation) reached into the private
``executor._scheduler``. The trigger is built through the module seam
``_crontab_trigger`` because other conftests may replace ``apscheduler`` with a
``MagicMock`` in ``sys.modules`` — tests patch the seam, never APScheduler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from prismal.scheduler import executor as executor_mod
from prismal.scheduler.cron_manager import CronManager
from prismal.scheduler.executor import CronExecutor


@pytest.fixture
def triggers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    built: list[tuple[str, Any]] = []

    def fake(expr: str, tz: Any) -> str:
        built.append((expr, tz))
        return f"trigger:{expr}"

    monkeypatch.setattr(executor_mod, "_crontab_trigger", fake)
    return built


@pytest.fixture
def executor(tmp_path: Path) -> CronExecutor:
    manager = MagicMock(spec=CronManager)
    ex = CronExecutor(manager=manager)
    ex._scheduler = MagicMock()
    return ex


async def _tick() -> None:
    return None


def test_add_callable_registers_the_function(
    executor: CronExecutor, triggers: list[tuple[str, Any]]
) -> None:
    job_id = executor.add_callable("memory-consolidation", _tick, cron="15 2 * * *")

    assert job_id == "memory-consolidation"
    executor._scheduler.add_job.assert_called_once_with(
        _tick,
        "trigger:15 2 * * *",
        id="memory-consolidation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def test_add_callable_uses_the_scheduler_timezone(
    executor: CronExecutor, triggers: list[tuple[str, Any]]
) -> None:
    executor.add_callable("job", _tick, cron="0 9 * * *")

    assert triggers == [("0 9 * * *", executor._dts.get_scheduler_tz())]


def test_add_callable_honours_an_explicit_timezone(
    executor: CronExecutor, triggers: list[tuple[str, Any]]
) -> None:
    executor.add_callable("job", _tick, cron="0 9 * * *", timezone="Europe/Madrid")

    ((_, tz),) = triggers
    assert str(tz) == "Europe/Madrid"


def test_add_callable_forwards_concurrency_options(
    executor: CronExecutor, triggers: list[tuple[str, Any]]
) -> None:
    executor.add_callable("job", _tick, cron="* * * * *", max_instances=3, coalesce=False)

    kwargs = executor._scheduler.add_job.call_args.kwargs
    assert kwargs["max_instances"] == 3
    assert kwargs["coalesce"] is False


@pytest.mark.parametrize("cron", ["", "61 * *", "* * * * * *", "once:2026-01-01 00:00:00"])
def test_add_callable_rejects_a_non_crontab_schedule(
    executor: CronExecutor, triggers: list[tuple[str, Any]], cron: str
) -> None:
    with pytest.raises(ValueError, match="crontab"):
        executor.add_callable("job", _tick, cron=cron)

    executor._scheduler.add_job.assert_not_called()
    assert triggers == []
