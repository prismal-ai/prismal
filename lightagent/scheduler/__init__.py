"""Scheduler package — Prefect flows and cron job management.

Public API::

    from lightagent.scheduler import CronManager, CronJob, CronStatus
    from lightagent.scheduler import CronExecutor
    from lightagent.scheduler import document_index_flow, skill_discovery_flow

``CronExecutor`` and the Prefect flows are loaded lazily on first access to
avoid pulling the full agents/providers chain when only lightweight scheduler
utilities (e.g. ``DateTimeService``, ``CronManager``) are needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightagent.scheduler.cron_manager import CronJob, CronManager, CronStatus
from lightagent.scheduler.datetime_service import DateTimeService

if TYPE_CHECKING:
    from lightagent.scheduler.executor import CronExecutor
    from lightagent.scheduler.prefect_flows import (
        agent_run_flow,
        config_reload_flow,
        document_index_flow,
        skill_discovery_flow,
    )

__all__ = [
    "CronExecutor",
    "CronJob",
    "CronManager",
    "CronStatus",
    "DateTimeService",
    "agent_run_flow",
    "config_reload_flow",
    "document_index_flow",
    "skill_discovery_flow",
]

_LAZY: dict[str, str] = {
    "CronExecutor": "lightagent.scheduler.executor",
    "agent_run_flow": "lightagent.scheduler.prefect_flows",
    "config_reload_flow": "lightagent.scheduler.prefect_flows",
    "document_index_flow": "lightagent.scheduler.prefect_flows",
    "skill_discovery_flow": "lightagent.scheduler.prefect_flows",
}


def __getattr__(name: str) -> object:
    """Lazy-load heavy submodules only when first accessed."""
    if name in _LAZY:
        import importlib

        mod = importlib.import_module(_LAZY[name])
        obj = getattr(mod, name)
        globals()[name] = obj  # cache so subsequent accesses are direct
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
