"""Async cost reconciliation (Phase GW — SPEC-GW-REC-001, DD-GW-005).

After a gateway call completes with a ``generation_id``, fetch the
authoritative per-call cost from the gateway's generation API and record the
drift ``|authoritative - reported|``. Alert-only (ARCHITECTURE §10): it never
rewrites already-metered usage. Fully fail-open: a failed lookup, a full
queue or a missing event loop drops the job (counter only) and never affects
the turn.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger
from prismal.providers.gateways.openai_compatible import count

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.providers.gateways.base import GatewayCallMeta
    from prismal.providers.gateways.router import GatewayRouter

logger = get_logger("prismal.providers.gateways.reconcile")


class CostReconciler:
    """Bounded, non-blocking queue of generation lookups."""

    def __init__(
        self,
        router: GatewayRouter,
        *,
        settings: Settings | None = None,
        max_concurrency: int = 4,
        max_pending: int = 256,
    ) -> None:
        self._router = router
        self._settings = settings if settings is not None else router.settings
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_pending = max_pending
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        """Number of reconciliation tasks still running."""
        return len(self._tasks)

    def schedule(self, meta: GatewayCallMeta) -> None:
        """Queue a reconciliation for *meta*; never blocks, never raises."""
        try:
            if not self._settings.gateway_reconcile_enabled:
                return
            if meta.generation_id is None or meta.reported_cost_usd is None:
                return
            gateway = self._router.get(meta.gateway)
            if gateway is None or not gateway.profile.capabilities.has_generation_api:
                return
            if len(self._tasks) >= self._max_pending:
                count("gateway_errors", gateway=meta.gateway, kind="reconcile_dropped")
                return
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no running loop (sync caller): nothing to schedule on
            return
        except Exception:
            return
        task = loop.create_task(self._reconcile(meta))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reconcile(self, meta: GatewayCallMeta) -> None:
        gateway = self._router.get(meta.gateway)
        if gateway is None or meta.generation_id is None or meta.reported_cost_usd is None:
            return
        try:
            await asyncio.sleep(self._settings.gateway_reconcile_delay_s)
            async with self._semaphore:
                stats = await gateway.fetch_generation(meta.generation_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            count("gateway_errors", gateway=meta.gateway, kind="reconcile")
            return
        if stats is None:
            return
        drift = abs(stats.total_cost_usd - meta.reported_cost_usd)
        with suppress(Exception):
            from prismal.monitoring import otel

            otel.OTelManager().record_histogram(
                "gateway_cost_drift_usd", drift, attributes={"gateway": meta.gateway}
            )
        logger.debug("gateway.cost_reconciled", gateway=meta.gateway, drift_usd=drift)

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for queued jobs up to *timeout_s*, then cancel the rest (never raises)."""
        if not self._tasks:
            return
        tasks = list(self._tasks)
        _done, still_pending = await asyncio.wait(tasks, timeout=timeout_s)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)
        self._tasks.difference_update(tasks)


__all__ = ["CostReconciler"]
