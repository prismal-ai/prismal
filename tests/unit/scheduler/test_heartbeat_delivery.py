"""Unit tests for HeartbeatDelivery multi-channel sender."""

from __future__ import annotations

import builtins
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.scheduler.heartbeat_delivery import HeartbeatDelivery


@pytest.mark.asyncio
async def test_send_telegram_calls_httpx() -> None:
    """send() makes a POST to the Telegram sendMessage API."""
    delivery = HeartbeatDelivery(telegram_token="bot_tok")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client

        await delivery.send("telegram", "12345", "Hello from heartbeat")

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "sendMessage" in str(call_kwargs)
    assert "12345" in str(call_kwargs)


@pytest.mark.asyncio
async def test_send_slack_calls_httpx() -> None:
    """send() makes a POST to the Slack chat.postMessage API."""
    delivery = HeartbeatDelivery(slack_token="xoxb-fake")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client

        await delivery.send("slack", "#morning", "Good morning!")

    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_discord_posts_to_webhook() -> None:
    """send() with 'discord' channel posts to the webhook URL target."""
    delivery = HeartbeatDelivery()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client

        webhook_url = "https://discord.com/api/webhooks/123/abc"
        await delivery.send("discord", webhook_url, "Discord alert")

    mock_client.post.assert_called_once()
    assert webhook_url in str(mock_client.post.call_args)


@pytest.mark.asyncio
async def test_send_unknown_channel_logs_warning_and_returns() -> None:
    """send() with an unsupported channel type does not raise."""
    delivery = HeartbeatDelivery()
    await delivery.send("carrier_pigeon", "user@example.com", "Message")


@pytest.mark.asyncio
async def test_send_http_error_does_not_raise() -> None:
    """send() swallows HTTP errors (fail-open design)."""
    delivery = HeartbeatDelivery(telegram_token="tok")

    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client

        # Must not raise
        await delivery.send("telegram", "12345", "test")


@pytest.mark.asyncio
async def test_send_telegram_skipped_when_no_token() -> None:
    """send() skips Telegram silently when no token is configured."""
    delivery = HeartbeatDelivery(telegram_token="")
    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        await delivery.send("telegram", "12345", "hello")
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_slack_skipped_when_no_token() -> None:
    """send() skips Slack silently when no token is configured."""
    delivery = HeartbeatDelivery(slack_token="")
    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        await delivery.send("slack", "#ch", "hello")
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_skipped_when_no_smtp_config() -> None:
    """send() skips email delivery silently when smtp_host is not configured."""
    delivery = HeartbeatDelivery(smtp_host="", smtp_from="")
    with patch("lightagent.scheduler.heartbeat_delivery.httpx.AsyncClient") as mock_cls:
        await delivery.send("email", "user@example.com", "hello")
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_import_error_does_not_raise() -> None:
    """send() does not raise when aiosmtplib is not installed."""
    delivery = HeartbeatDelivery(
        smtp_host="smtp.example.com", smtp_from="bot@example.com"
    )
    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "aiosmtplib":
            raise ImportError("No module named 'aiosmtplib'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        await delivery.send("email", "user@example.com", "test message")
