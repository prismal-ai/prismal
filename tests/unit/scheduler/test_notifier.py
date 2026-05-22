"""Unit tests for CronNotifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.scheduler.notifier import CronNotifier


class TestCronNotifier:
    """Tests for CronNotifier alert delivery."""

    @pytest.mark.asyncio
    async def test_notify_failure_sends_telegram_when_configured(self) -> None:
        """notify_failure POSTs to Telegram sendMessage when chat_id is set."""
        notifier = CronNotifier(
            telegram_chat_id="123456",
            telegram_token="bot-token",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await notifier.notify_failure(
                job_name="daily-job",
                error="something broke",
                duration_seconds=3.5,
            )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "sendMessage" in call_kwargs.args[0]
        payload = call_kwargs.kwargs["json"]
        assert payload["chat_id"] == "123456"
        assert "daily-job" in payload["text"]
        assert "something broke" in payload["text"]

    @pytest.mark.asyncio
    async def test_notify_failure_sends_slack_when_configured(self) -> None:
        """notify_failure POSTs to Slack chat.postMessage when channel is set."""
        notifier = CronNotifier(
            slack_channel="#alerts",
            slack_token="xoxb-token",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"ok": True})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await notifier.notify_failure(
                job_name="weekly-report",
                error="timeout",
                duration_seconds=60.0,
            )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "chat.postMessage" in call_kwargs.args[0]
        payload = call_kwargs.kwargs["json"]
        assert payload["channel"] == "#alerts"
        assert "weekly-report" in payload["text"]
        assert "timeout" in payload["text"]

    @pytest.mark.asyncio
    async def test_notify_failure_skips_when_not_configured(self) -> None:
        """notify_failure does nothing when no targets are configured."""
        notifier = CronNotifier()
        # Should complete without any HTTP calls or errors
        await notifier.notify_failure(
            job_name="job",
            error="err",
            duration_seconds=1.0,
        )

    @pytest.mark.asyncio
    async def test_notify_failure_swallows_http_errors(self) -> None:
        """HTTP errors in notify_failure are logged but not raised."""
        import httpx

        notifier = CronNotifier(
            telegram_chat_id="123",
            telegram_token="tok",
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.HTTPError("network error"))
            mock_client_cls.return_value = mock_client

            # Must not raise
            await notifier.notify_failure(
                job_name="job",
                error="err",
                duration_seconds=1.0,
            )

    @pytest.mark.asyncio
    async def test_notify_sends_both_when_both_configured(self) -> None:
        """notify_failure sends to both Telegram and Slack when both configured."""
        notifier = CronNotifier(
            telegram_chat_id="123",
            telegram_token="tg-tok",
            slack_channel="#ch",
            slack_token="slack-tok",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"ok": True})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await notifier.notify_failure(
                job_name="job",
                error="err",
                duration_seconds=2.0,
            )

        assert mock_client.post.call_count == 2
