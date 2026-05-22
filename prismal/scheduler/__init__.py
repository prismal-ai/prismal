"""Scheduler package — Prefect flows and cron job management.

Public API::

    from prismal.scheduler import CronManager, CronJob, CronStatus
    from prismal.scheduler import CronExecutor
    from prismal.scheduler import document_index_flow, skill_discovery_flow

``CronExecutor`` and the Prefect flows are loaded lazily on first access to
avoid pulling the full agents/providers chain when only lightweight scheduler
utilities (e.g. ``DateTimeService``, ``CronManager``) are needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.scheduler.cron_manager import CronJob, CronManager, CronStatus
from prismal.scheduler.datetime_service import DateTimeService

if TYPE_CHECKING:
    from prismal.scheduler.executor import CronExecutor
    from prismal.scheduler.prefect_flows import (
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
    "CronExecutor": "prismal.scheduler.executor",
    "agent_run_flow": "prismal.scheduler.prefect_flows",
    "config_reload_flow": "prismal.scheduler.prefect_flows",
    "document_index_flow": "prismal.scheduler.prefect_flows",
    "skill_discovery_flow": "prismal.scheduler.prefect_flows",
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
