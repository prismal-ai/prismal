"""Multi-channel output delivery for heartbeat (proactive) cron jobs.

Sends the agent's text output to a configured external channel after a
successful job run.  All errors are caught and logged — never raised —
so delivery failures never affect the cron execution outcome.

Supported channels:

- ``"telegram"`` — target is a Telegram chat ID (integer string or ``@username``)
- ``"slack"``    — target is a channel name or ID (e.g. ``"#morning-report"``)
- ``"discord"``  — target is a Discord webhook URL
- ``"email"``    — target is an email address (requires SMTP settings in config)
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger("lightagent.scheduler.heartbeat_delivery")

_TELEGRAM_API = "https://api.telegram.org"
_SLACK_API = "https://slack.com/api/chat.postMessage"


class HeartbeatDelivery:
    """Send heartbeat job output to Telegram, Slack, Discord, or email.

    All channels are opt-in: if the required credential is absent the
    channel is silently skipped.  HTTP / SMTP errors are caught, logged
    as warnings, and never re-raised (fail-open).

    Args:
        telegram_token: Telegram Bot API token.
            Falls back to ``settings.telegram_bot_token``.
        slack_token: Slack Bot User OAuth token (``xoxb-...``).
            Falls back to ``settings.slack_bot_token``.
        smtp_host: SMTP server host for email delivery.
            Falls back to ``settings.heartbeat_smtp_host``.
        smtp_port: SMTP server port.
            Falls back to ``settings.heartbeat_smtp_port``.
        smtp_user: SMTP login username.
            Falls back to ``settings.heartbeat_smtp_user``.
        smtp_password: SMTP login password.
            Falls back to ``settings.heartbeat_smtp_password``.
        smtp_from: Sender email address.
            Falls back to ``settings.heartbeat_smtp_from``.
    """

    def __init__(
        self,
        *,
        telegram_token: str | None = None,
        slack_token: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
    ) -> None:
        """Initialise delivery credentials, falling back to settings."""
        from prismal.core.config import get_settings

        s = get_settings()
        self._tg_token: str = (
            telegram_token
            if telegram_token is not None
            else s.telegram_bot_token.get_secret_value()
        )
        self._slack_token: str = (
            slack_token if slack_token is not None else s.slack_bot_token.get_secret_value()
        )
        self._smtp_host: str = smtp_host if smtp_host is not None else s.heartbeat_smtp_host
        self._smtp_port: int = smtp_port if smtp_port is not None else s.heartbeat_smtp_port
        self._smtp_user: str = smtp_user if smtp_user is not None else s.heartbeat_smtp_user
        self._smtp_password: str = (
            smtp_password
            if smtp_password is not None
            else s.heartbeat_smtp_password.get_secret_value()
        )
        self._smtp_from: str = smtp_from if smtp_from is not None else s.heartbeat_smtp_from

    async def send(self, channel: str, target: str, content: str) -> None:
        """Deliver ``content`` to ``target`` via ``channel``.

        Args:
            channel: One of ``"telegram"``, ``"slack"``, ``"discord"``,
                ``"email"``.  Unknown values are logged and skipped.
            target: Channel-specific destination:
                telegram → chat ID, slack → channel name/ID,
                discord → webhook URL, email → recipient address.
            content: Text body to deliver.
        """
        try:
            match channel.lower():
                case "telegram":
                    await self._send_telegram(target, content)
                case "slack":
                    await self._send_slack(target, content)
                case "discord":
                    await self._send_discord(target, content)
                case "email":
                    await self._send_email(target, content)
                case _:
                    logger.warning(
                        "heartbeat_delivery.unknown_channel",
                        channel=channel,
                        target=target,
                    )
        except Exception as exc:
            logger.warning(
                "heartbeat_delivery.send_error",
                channel=channel,
                target=target,
                error=str(exc),
            )

    async def _send_telegram(self, chat_id: str, text: str) -> None:
        """POST text to a Telegram chat via sendMessage.

        Args:
            chat_id: Telegram chat ID or ``@username``.
            text: Message text to send.
        """
        if not self._tg_token:
            logger.debug("heartbeat_delivery.telegram_skipped", reason="no_token")
            return
        url = f"{_TELEGRAM_API}/bot{self._tg_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
            resp.raise_for_status()
        logger.info("heartbeat_delivery.telegram_sent", chat_id=chat_id)

    async def _send_slack(self, channel: str, text: str) -> None:
        """POST text to a Slack channel via chat.postMessage.

        Args:
            channel: Slack channel name or ID (e.g. ``#morning-report``).
            text: Message text to send.
        """
        if not self._slack_token:
            logger.debug("heartbeat_delivery.slack_skipped", reason="no_token")
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _SLACK_API,
                headers={"Authorization": f"Bearer {self._slack_token}"},
                json={"channel": channel, "text": text},
            )
            resp.raise_for_status()
        logger.info("heartbeat_delivery.slack_sent", channel=channel)

    async def _send_discord(self, webhook_url: str, text: str) -> None:
        """POST text to a Discord channel via webhook URL.

        Args:
            webhook_url: Full Discord webhook URL.
            text: Message text to send.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"content": text})
            resp.raise_for_status()
        logger.info("heartbeat_delivery.discord_sent", webhook_url=webhook_url[:40])

    async def _send_email(self, recipient: str, text: str) -> None:
        """Send text to an email address via SMTP (aiosmtplib).

        Args:
            recipient: Recipient email address.
            text: Message body to send.
        """
        if not self._smtp_host or not self._smtp_from:
            logger.debug("heartbeat_delivery.email_skipped", reason="no_smtp_config")
            return
        try:
            from email.mime.text import MIMEText

            import aiosmtplib

            message = MIMEText(text, "plain", "utf-8")
            message["Subject"] = "LightAgent Heartbeat Report"
            message["From"] = self._smtp_from
            message["To"] = recipient
            await aiosmtplib.send(
                message,
                hostname=self._smtp_host,
                port=self._smtp_port or 587,
                username=self._smtp_user or None,
                password=self._smtp_password or None,
                use_tls=False,
                start_tls=True,
            )
            logger.info("heartbeat_delivery.email_sent", recipient=recipient)
        except ImportError:
            logger.warning(
                "heartbeat_delivery.email_skipped",
                reason="aiosmtplib_not_installed",
            )


__all__ = ["HeartbeatDelivery"]
