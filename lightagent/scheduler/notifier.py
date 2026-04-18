"""Cron failure notifier — sends alerts to Telegram and/or Slack.

``CronNotifier`` is a lightweight, dependency-free notification helper
that makes direct httpx POSTs to the Telegram ``sendMessage`` and
Slack ``chat.postMessage`` APIs when a cron job fails.

It is injected into :class:`~lightagent.scheduler.executor.CronExecutor`
as an optional dependency so that tests can pass a no-op instance or a
mock without touching real HTTP.

Configuration is read from :class:`~lightagent.core.config.Settings` at
construction time when tokens/targets are not passed explicitly.
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger("lightagent.scheduler.notifier")

_TELEGRAM_API = "https://api.telegram.org"
_SLACK_API = "https://slack.com/api/chat.postMessage"


class CronNotifier:
    """Send cron job failure alerts to Telegram and/or Slack.

    Both destinations are opt-in: if the corresponding token/target is
    empty the channel is silently skipped.  HTTP errors are caught,
    logged as warnings, and never re-raised.

    Args:
        telegram_chat_id: Telegram chat ID to post alerts to.  Falls
            back to ``settings.cron_notify_telegram_chat_id``.
        telegram_token: Telegram Bot API token.  Falls back to
            ``settings.telegram_bot_token``.
        slack_channel: Slack channel name or ID (e.g. ``#alerts``).
            Falls back to ``settings.cron_notify_slack_channel``.
        slack_token: Slack Bot User OAuth token (``xoxb-...``).  Falls
            back to ``settings.slack_bot_token``.
    """

    def __init__(
        self,
        *,
        telegram_chat_id: str | None = None,
        telegram_token: str | None = None,
        slack_channel: str | None = None,
        slack_token: str | None = None,
    ) -> None:
        """Initialise the notifier, falling back to settings for missing values."""
        from lightagent.core.config import get_settings

        s = get_settings()

        self._tg_chat_id: str = (
            telegram_chat_id if telegram_chat_id is not None else s.cron_notify_telegram_chat_id
        )
        self._tg_token: str = (
            telegram_token
            if telegram_token is not None
            else s.telegram_bot_token.get_secret_value()
        )
        self._slack_channel: str = (
            slack_channel if slack_channel is not None else s.cron_notify_slack_channel
        )
        self._slack_token: str = (
            slack_token if slack_token is not None else s.slack_bot_token.get_secret_value()
        )

    async def notify_failure(
        self,
        job_name: str,
        error: str,
        duration_seconds: float,
    ) -> None:
        """Send a failure alert to all configured notification channels.

        Calls :meth:`_send_telegram` and :meth:`_send_slack` when the
        corresponding target is configured.  Errors from either call are
        caught internally — this method never raises.

        Args:
            job_name: Name of the cron job that failed.
            error: Error message from the exception.
            duration_seconds: How long the job ran before failing.
        """
        text = f"Cron job FAILED: {job_name}\nError: {error}\nDuration: {duration_seconds:.1f}s"
        if self._tg_chat_id and self._tg_token:
            await self._send_telegram(text)
        if self._slack_channel and self._slack_token:
            await self._send_slack(text)

    async def notify_success(
        self,
        job_name: str,
        output: str | None,
    ) -> None:
        """Send a success notification to all configured notification channels.

        Sends to Telegram and/or Slack when the corresponding credentials are
        set.  When neither external channel is configured, prints the
        notification directly to the terminal (console fallback) so that CLI
        users always receive their reminders even without external integrations.

        Errors from external channels are caught internally — this method
        never raises.

        Args:
            job_name: Name of the cron job that completed successfully.
            output: The agent's output message, or ``None`` if unavailable.
        """
        text = f"Cron reminder: {job_name}"
        if output:
            text += f"\n\n{output}"

        sent_externally = False
        if self._tg_chat_id and self._tg_token:
            await self._send_telegram(text)
            sent_externally = True
        if self._slack_channel and self._slack_token:
            await self._send_slack(text)
            sent_externally = True

        # Console fallback — always shown when no external channel is configured
        # so that CLI users see their reminders in the terminal.
        if not sent_externally:
            self._print_console(job_name, output)

    def _print_console(self, job_name: str, output: str | None) -> None:
        """Print a reminder notification to the terminal.

        Uses Rich when available; falls back to plain ``print`` otherwise.
        The notification is printed immediately (``flush=True``) so it appears
        even if the terminal is waiting at the input prompt.

        Args:
            job_name: Name of the cron job.
            output: The agent's output, or ``None``.
        """
        try:
            from rich import print as rich_print
            from rich.rule import Rule

            rich_print("")
            rich_print(Rule("[bold yellow]⏰ Recordatorio[/bold yellow]"))
            rich_print(f"[bold cyan]{job_name}[/bold cyan]")
            if output:
                rich_print(f"[dim]{output}[/dim]")
            rich_print(Rule())
            rich_print("")
        except ImportError:
            print(f"\n⏰ Recordatorio: {job_name}", flush=True)
            if output:
                print(output, flush=True)
            print("", flush=True)

    async def _send_telegram(self, text: str) -> None:
        """POST a message to the configured Telegram chat.

        Args:
            text: The message text to send.
        """
        url = f"{_TELEGRAM_API}/bot{self._tg_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json={"chat_id": self._tg_chat_id, "text": text},
                )
                resp.raise_for_status()
            logger.debug("cron_notify_telegram_sent", chat_id=self._tg_chat_id)
        except httpx.HTTPError as exc:
            logger.warning(
                "cron_notify_telegram_error",
                chat_id=self._tg_chat_id,
                error=str(exc),
            )

    async def _send_slack(self, text: str) -> None:
        """POST a message to the configured Slack channel.

        Args:
            text: The message text to send.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _SLACK_API,
                    json={"channel": self._slack_channel, "text": text},
                    headers={"Authorization": f"Bearer {self._slack_token}"},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(
                        "cron_notify_slack_not_ok",
                        channel=self._slack_channel,
                        slack_error=data.get("error"),
                    )
            logger.debug("cron_notify_slack_sent", channel=self._slack_channel)
        except httpx.HTTPError as exc:
            logger.warning(
                "cron_notify_slack_error",
                channel=self._slack_channel,
                error=str(exc),
            )


__all__ = ["CronNotifier"]
