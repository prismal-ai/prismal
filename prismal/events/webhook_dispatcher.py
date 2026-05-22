"""Async webhook delivery engine with HMAC-SHA256 signing and retry logic."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

RETRY_DELAYS: tuple[float, ...] = (1.0, 4.0, 16.0)
WEBHOOK_EVENTS: frozenset[str] = frozenset(
    {"chat.completed", "skill.executed", "cron.triggered", "security.blocked"}
)


class WebhookDispatcher:
    """Delivers webhook events to registered subscriber URLs.

    Signs each payload with HMAC-SHA256 using the subscriber's secret.
    Retries failed deliveries up to 3 times with exponential backoff.
    Marks webhooks inactive after exhausting retries.

    Args:
        db_path: Path to the SQLite database holding webhook registrations.
    """

    def __init__(self, db_path: str = "data/db/lightagent.db") -> None:
        """Initialize dispatcher with database path.

        Args:
            db_path: SQLite database file path.
        """
        self._db_path = db_path

    async def dispatch(self, event: str, payload: dict[str, Any]) -> None:
        """Dispatch an event to all matching active webhooks.

        Args:
            event: Event name (e.g., "chat.completed").
            payload: Event data to deliver.
        """
        if event not in WEBHOOK_EVENTS:
            logger.warning("webhook_dispatcher.unknown_event", event_name=event)
            return
        webhooks = await self._get_matching_webhooks(event)
        for wh in webhooks:
            asyncio.create_task(  # noqa: RUF006
                self._deliver_with_retry(wh, event, payload)
            )

    async def _get_matching_webhooks(self, event: str) -> list[dict[str, Any]]:
        """Fetch active webhooks that subscribe to this event.

        Args:
            event: The event name to filter by.

        Returns:
            List of webhook row dicts.
        """
        try:
            import aiosqlite
        except ImportError:
            return []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute(
                    "SELECT id, url, secret, events FROM webhooks WHERE active = 1"
                ) as cursor:
                    rows = await cursor.fetchall()
            except Exception:
                return []
        result = []
        for row in rows:
            events_list: list[str] = json.loads(row["events"])
            if event in events_list:
                result.append(dict(row))
        return result

    async def _deliver_with_retry(
        self,
        webhook: dict[str, Any],
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Attempt delivery with retry and mark inactive on exhaustion.

        Args:
            webhook: Webhook row with id, url, secret, events.
            event: Event name.
            payload: Event data dict.
        """
        body = json.dumps({"event": event, "payload": payload, "timestamp": time.time()})
        secret = webhook.get("secret", "")
        if not secret:
            logger.warning(
                "webhook_dispatcher.empty_secret_skipped",
                webhook_id=webhook["id"],
                detail=(
                    "webhook has no secret — delivery skipped; "
                    "re-register with a strong secret (≥32 chars)"
                ),
            )
            return
        sig = self._sign(body, secret)

        async with httpx.AsyncClient(timeout=10.0) as http:
            for attempt, delay in enumerate(RETRY_DELAYS):
                try:
                    r = await http.post(
                        webhook["url"],
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-LightAgent-Signature": sig,
                            "X-LightAgent-Event": event,
                        },
                    )
                    if r.is_success:
                        logger.info(
                            "webhook_dispatcher.delivered",
                            webhook_id=webhook["id"],
                            event_name=event,
                            attempt=attempt + 1,
                        )
                        return
                    logger.warning(
                        "webhook_dispatcher.delivery_failed",
                        webhook_id=webhook["id"],
                        status=r.status_code,
                        attempt=attempt + 1,
                    )
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    logger.warning(
                        "webhook_dispatcher.delivery_error",
                        webhook_id=webhook["id"],
                        error=str(exc),
                        attempt=attempt + 1,
                    )
                if attempt < len(RETRY_DELAYS) - 1:
                    await asyncio.sleep(delay)

        await self._mark_inactive(webhook["id"])

    async def _mark_inactive(self, webhook_id: str) -> None:
        """Mark a webhook as inactive after exhausting retries.

        Args:
            webhook_id: The webhook UUID to deactivate.
        """
        try:
            import aiosqlite

            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("UPDATE webhooks SET active = 0 WHERE id = ?", (webhook_id,))
                await db.commit()
        except Exception as exc:
            logger.error("webhook_dispatcher.mark_inactive_failed", error=str(exc))

    @staticmethod
    def _sign(body: str, secret: str) -> str:
        """Compute HMAC-SHA256 signature for a payload.

        Args:
            body: JSON-serialized payload string.
            secret: HMAC signing secret (must be non-empty).

        Returns:
            Hex-encoded HMAC-SHA256 digest.

        Raises:
            ValueError: If ``secret`` is empty.
        """
        if not secret:
            raise ValueError("webhook secret must not be empty")
        return hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()


__all__ = ["WEBHOOK_EVENTS", "WebhookDispatcher"]
