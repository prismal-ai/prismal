"""Periodic gateway quota poll → ``prismal.gateway_quota_remaining`` (Phase GW — GW5-05).

Fail-open: a failed poll is counted and the loop keeps running. Started by the
composition root (``gateway_quota_poll_interval_s``) and stopped by
``RuntimeContext.aclose()``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from prismal.providers.gateways.openai_compatible import count

if TYPE_CHECKING:
    from prismal.providers.gateways.base import QuotaSnapshot
    from prismal.providers.gateways.router import GatewayRouter


class GatewayQuotaPoller:
    """Polls ``router.quotas()`` and publishes the remaining allowance as a gauge."""

    def __init__(self, router: GatewayRouter, *, interval_s: float | None = None) -> None:
        self._router = router
        self._interval_s = (
            float(interval_s)
            if interval_s is not None
            else float(router.settings.gateway_quota_poll_interval_s)
        )
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> list[QuotaSnapshot]:
        """One poll; never raises."""
        try:
            snapshots = await self._router.quotas()
        except Exception:
            count("gateway_errors", gateway="*", kind="quota_poll")
            return []
        with suppress(Exception):
            from prismal.monitoring import otel

            manager = otel.OTelManager()
            for snap in snapshots:
                if snap.remaining is not None:
                    manager.set_gauge(
                        "gateway_quota_remaining",
                        snap.remaining,
                        attributes={"gateway": snap.gateway, "window": snap.window},
                    )
        return snapshots

    def start(self) -> None:
        """Start the background loop on the running event loop (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self._interval_s)

    async def aclose(self) -> None:
        """Stop the loop (idempotent, never raises)."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


__all__ = ["GatewayQuotaPoller"]
