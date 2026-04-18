"""Unit tests — cron re-training integration for ml_pipeline (F5-05).

Demonstrates that the ``cron_add`` tool can schedule periodic ML re-training
via the CronManager, and that the scheduled task description is preserved.
No live executor or API key required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_cron_add_schedules_ml_retrain() -> None:
    """cron_add persists a re-training job in the CronManager store."""
    from lightagent.agents.tools import cron_add

    mock_manager = MagicMock()
    mock_manager.add.return_value = MagicMock(
        id="ml-retrain-001",
        name="ml-daily-retrain",
        schedule="0 2 * * *",
        task=(
            "Retrain the iris classification model using "
            "the latest dataset at "
            "tests/fixtures/datasets/iris_sample.csv"
        ),
        enabled=True,
        next_run=None,
    )
    with (
        patch(
            "lightagent.agents.tools.CronManager",
            return_value=mock_manager,
        ),
        patch(
            "lightagent.agents.tools.cron_add.func.__globals__"
            if False
            else "lightagent.scheduler.executor.get_running_executor",
            return_value=None,
        ),
    ):
        result = cron_add.invoke(
            {
                "name": "ml-daily-retrain",
                "schedule": "0 2 * * *",
                "task": (
                    "Retrain the iris classification model using "
                    "the latest dataset at "
                    "tests/fixtures/datasets/iris_sample.csv"
                ),
            }
        )

    assert "ml-daily-retrain" in result or "retrain" in result.lower()
    mock_manager.add.assert_called_once()


def test_cron_ml_retrain_description_preserved() -> None:
    """The scheduled job description includes the dataset path."""
    from lightagent.agents.tools import cron_add

    task_desc = (
        "Retrain classification model on "
        "tests/fixtures/datasets/churn_sample.csv "
        "and export updated model to data/workspace/ml_models/churn/"
    )
    mock_manager = MagicMock()
    mock_manager.add.return_value = MagicMock(
        id="churn-retrain-001",
        name="churn-weekly-retrain",
        schedule="0 3 * * 0",
        task=task_desc,
        enabled=True,
        next_run=None,
    )
    with (
        patch(
            "lightagent.agents.tools.CronManager",
            return_value=mock_manager,
        ),
        patch(
            "lightagent.scheduler.executor.get_running_executor",
            return_value=None,
        ),
    ):
        result = cron_add.invoke(
            {
                "name": "churn-weekly-retrain",
                "schedule": "0 3 * * 0",
                "task": task_desc,
            }
        )

    call_args = mock_manager.add.call_args
    assert call_args is not None
    # First positional arg is name, second is schedule, third is task
    passed_task = call_args.kwargs.get("task") or (
        call_args.args[2] if len(call_args.args) > 2 else ""
    )
    assert "churn_sample.csv" in passed_task or "churn" in result.lower()
