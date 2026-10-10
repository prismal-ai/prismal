"""Live provider batch smoke tests (AMR5-01..03). Opt-in: ``-m live_api`` + real keys.

They submit a one-request batch and cancel it immediately (a real batch can take
up to 24 h), so they only prove submit/poll/cancel wiring against the real API.
"""

from __future__ import annotations

import pytest

from prismal.core.config import get_settings
from prismal.providers.batch_adapters import (
    AnthropicBatchAdapter,
    BatchApiPort,
    BatchRequest,
    BatchStatus,
    OpenAIBatchAdapter,
    OpenRouterBatchAdapter,
)

pytestmark = pytest.mark.live_api

_S = get_settings()


def _request(model_ref: str) -> BatchRequest:
    return BatchRequest(
        custom_id="live-smoke-1",
        model_ref=model_ref,
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        max_tokens=8,
    )


async def _submit_poll_cancel(adapter: BatchApiPort, model_ref: str) -> None:
    job_id = await adapter.submit([_request(model_ref)])
    assert job_id
    status = await adapter.poll(job_id)
    assert status in set(BatchStatus)
    await adapter.cancel(job_id)


@pytest.mark.skipif(not _S.anthropic_api_key.get_secret_value(), reason="ANTHROPIC_API_KEY not set")
async def test_anthropic_batch_submit_poll_cancel() -> None:
    await _submit_poll_cancel(
        AnthropicBatchAdapter(_S.anthropic_api_key), "anthropic/claude-haiku-4-5"
    )


@pytest.mark.skipif(not _S.openai_api_key.get_secret_value(), reason="OPENAI_API_KEY not set")
async def test_openai_batch_submit_poll_cancel() -> None:
    await _submit_poll_cancel(OpenAIBatchAdapter(_S.openai_api_key), "openai/gpt-4o-mini")


@pytest.mark.skipif(
    not _S.openrouter_api_key.get_secret_value(), reason="PRISMAL_OPENROUTER_API_KEY not set"
)
async def test_openrouter_batch_submit_poll_cancel() -> None:
    await _submit_poll_cancel(
        OpenRouterBatchAdapter(_S.openrouter_api_key, base_url=_S.openrouter_base_url),
        "gw:openrouter/openai/gpt-4o-mini:batch",
    )
