"""Builder for the skynet subgraph (SPEC-SKY-SG-001).

Topology::

    plan ──[dispatcher: one Send per order]──► worker ⇉ reduce → evaluate ─┐
      ▲                                                                     │
      └────────── re-plan while not complete and round < max_rounds ───────┘
                                     │ complete / round cap / nothing unmet
                                     ▼
                                  output → END

The dispatcher is the reused :func:`make_parallel_dispatcher` (DD-SKY-001)
reading the top-level ``skynet_orders`` field (the dispatcher resolves its
``tasks_field`` from the state root — same pattern as ``dev_pipeline_modules``;
deviation from the SPEC's dotted ``metadata.skynet.orders`` sketch, whose
durable twin *is* kept under ``metadata.skynet.orders``).  Concurrent worker
results merge through the Phase 34 ``parallel_results`` ``operator.add``
channel, tagged per entry (DD-SKY-004).

Every backend is callable-injected (DD-SKY-006) so the subgraph runs
end-to-end with fakes and no provider import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.agents.patterns.parallel import make_parallel_dispatcher
from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.worker import SwarmWorker
from prismal.agents.subgraphs.registry import SubgraphDefinition
from prismal.agents.subgraphs.skynet.evaluate_node import (
    make_evaluate_node,
    route_after_evaluate,
)
from prismal.agents.subgraphs.skynet.output_node import make_output_node
from prismal.agents.subgraphs.skynet.plan_node import make_plan_node
from prismal.agents.subgraphs.skynet.reduce_node import make_reduce_node
from prismal.agents.subgraphs.skynet.worker_node import make_worker_node
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.agents.extension.ports import ToolProviderPort
    from prismal.agents.skynet.reduce import ReduceFn
    from prismal.agents.skynet.roles import RoleRegistry
    from prismal.agents.skynet.supervisor import EvaluateFn, PlanFn
    from prismal.agents.skynet.worker import BudgetGuardFn, RemoteSendFn, WorkerFn
    from prismal.agents.subgraphs.registry import SubgraphRegistry
    from prismal.budget.meter import CostMeter
    from prismal.core.config import Settings
    from prismal.security.action_interceptor import ActionInterceptor
    from prismal.security.audit import AuditLogger

logger = get_logger("prismal.subgraphs.skynet.builder")

_NAME = "skynet"
_DESCRIPTION = (
    "Skynet swarm: plan → dynamic Send fan-out to N workers → reduce → "
    "evaluate (bounded re-plan loop) → output"
)


def build_skynet_subgraph(
    settings: Settings | None = None,
    *,
    supervisor: SkynetSupervisor | None = None,
    worker: SwarmWorker | None = None,
    plan_fn: PlanFn | None = None,
    evaluate_fn: EvaluateFn | None = None,
    worker_fn: WorkerFn | None = None,
    reduce_fn: ReduceFn | None = None,
    tool_provider: ToolProviderPort | None = None,
    interceptor: ActionInterceptor | None = None,
    role_registry: RoleRegistry | None = None,
    send_fn: RemoteSendFn | None = None,
    budget_guard_fn: BudgetGuardFn | None = None,
    meter: CostMeter | None = None,
    audit: AuditLogger | None = None,
) -> SubgraphDefinition:
    """Build the skynet :class:`SubgraphDefinition` (RF-SKY-09 / SPEC-SP-INT-001).

    Args:
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.
        supervisor: Fully-built supervisor.  Takes precedence over ``plan_fn``
            / ``evaluate_fn``, which are convenience shortcuts for building
            the default supervisor.
        worker: Fully-built worker.  Takes precedence over ``worker_fn`` /
            ``tool_provider`` / ``interceptor``.
        plan_fn: Injected planner backend (used when *supervisor* is None).
        evaluate_fn: Injected evaluator backend (used when *supervisor* is None).
        worker_fn: Injected worker backend (used when *worker* is None).
        reduce_fn: Injected synthesis backend for the reduce node.
        tool_provider: Injected tool source for the worker (Fase Y).
        interceptor: Injected action gate for the worker.
        role_registry: S+ specialist registry threaded into the supervisor
            (role-aware planning) and the worker (per-role model/persona/tools).
        send_fn: S+ remote delegation backend threaded into the worker.
        budget_guard_fn: S+ pre-call budget check threaded into the worker.
        meter: Shared per-run :class:`CostMeter` (S+2).  When *supervisor* /
            *worker* are built here, ONE meter (this or a fresh one) is threaded
            into the supervisor, worker, reducer, and output node so
            ``SwarmResult.usage`` is the whole-swarm total (DD-SP-003).
        audit: Audit logger threaded into the default supervisor.

    Returns:
        :class:`SubgraphDefinition` with 5 nodes, the Send fan-out edge, and
        the bounded re-plan conditional edge.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    # One shared meter for the whole swarm (DD-SP-003). Built here unless the
    # caller injected one (e.g. the per-run budget registry).
    if meter is None:
        from prismal.budget.meter import CostMeter as _CostMeter

        meter = _CostMeter(settings=settings)
    if supervisor is None:
        supervisor = SkynetSupervisor(
            plan_fn=plan_fn,
            evaluate_fn=evaluate_fn,
            role_registry=role_registry,
            meter=meter,
            audit=audit,
            settings=settings,
        )
    if worker is None:
        worker = SwarmWorker(
            worker_fn=worker_fn,
            tool_provider=tool_provider,
            interceptor=interceptor,
            role_registry=role_registry,
            meter=meter,
            send_fn=send_fn,
            budget_guard_fn=budget_guard_fn,
            settings=settings,
        )

    # One Send per staged order; plan() already capped the list and deferred
    # the overflow, so the dispatcher's own cap is a second line of defense.
    dispatcher = make_parallel_dispatcher(
        tasks_field="skynet_orders",
        worker_node="skynet_worker",
        max_workers=settings.skynet_max_swarm,
        on_empty="skynet_output",
        task_key="_order",
    )

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="skynet_plan",
        nodes={
            "skynet_plan": make_plan_node(supervisor),
            "skynet_worker": make_worker_node(worker),
            "skynet_reduce": make_reduce_node(settings, reduce_fn=reduce_fn, meter=meter),
            "skynet_evaluate": make_evaluate_node(supervisor, settings),
            "skynet_output": make_output_node(meter=meter),
        },
        edges=[
            ("skynet_worker", "skynet_reduce"),
            ("skynet_reduce", "skynet_evaluate"),
        ],
        conditional_edges={"skynet_evaluate": route_after_evaluate},
        send_edges=[("skynet_plan", dispatcher)],
    )
    logger.info("skynet_subgraph_built", nodes=list(definition.nodes.keys()))
    return definition


async def register_skynet(
    registry: SubgraphRegistry | None = None,
    *,
    settings: Settings | None = None,
) -> None:
    """Idempotently register the skynet subgraph (mirrors ``register_kokoro``)."""
    from prismal.agents.subgraphs.registry import SubgraphRegistry as _SubgraphRegistry

    registry = registry or _SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("skynet.already_registered")
        return
    definition = build_skynet_subgraph(settings=settings)
    await registry.register(_NAME, definition)
    logger.info("skynet.registered")


__all__ = ["build_skynet_subgraph", "register_skynet"]
