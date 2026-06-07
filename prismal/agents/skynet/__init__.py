"""Skynet swarm supervisor (Fase S).

A meta-supervisor that decomposes one order into many sub-orders, dispatches a
dynamically-sized swarm of workers via LangGraph ``Send`` fan-out, and reduces
their outputs into a single result (see ``specs/skynet-swarm/``).

Phases S1-S3 ship the value objects, the :class:`SkynetSupervisor`, the
:class:`SwarmWorker`, and :func:`reduce_results`; the subgraph and supervisor
integration land in later phases.
"""

from __future__ import annotations

from prismal.agents.skynet.reduce import ReduceFn, reduce_results
from prismal.agents.skynet.supervisor import EvaluateFn, PlanFn, SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, SwarmResult, WorkerResult
from prismal.agents.skynet.worker import WORKER_AGENT_NAME, SwarmWorker, WorkerFn

__all__ = [
    "WORKER_AGENT_NAME",
    "EvaluateFn",
    "PlanFn",
    "ReduceFn",
    "SkynetSupervisor",
    "SwarmOrder",
    "SwarmPlan",
    "SwarmResult",
    "SwarmWorker",
    "WorkerFn",
    "WorkerResult",
    "reduce_results",
]
