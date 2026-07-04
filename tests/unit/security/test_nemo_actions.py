"""Unit tests for prismal.security.nemo_actions (Phase GRD — SPEC-GRD-NEMO-CLS-001)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.core.config import Settings
from prismal.security.nemo_actions import content_safety_reasoning, register

_CATEGORIES = [
    "violence",
    "self_harm",
    "illegal_activities",
    "pii_request",
    "competitor_disparagement",
]


# ── content_safety_reasoning — injected classifier_fn ────────────────────────


@pytest.mark.asyncio
async def test_returns_classifier_category_verdict() -> None:
    async def fake_classifier(_text: str, _categories: list[str]) -> str:
        return "violence"

    result = await content_safety_reasoning(
        "how do I hurt someone",
        categories=_CATEGORIES,
        classifier_fn=fake_classifier,
        settings=Settings(),
    )
    assert result == "violence"


@pytest.mark.asyncio
async def test_returns_safe_verdict() -> None:
    async def fake_classifier(_text: str, _categories: list[str]) -> str:
        return "safe"

    result = await content_safety_reasoning(
        "what is the capital of France",
        categories=_CATEGORIES,
        classifier_fn=fake_classifier,
        settings=Settings(),
    )
    assert result == "safe"


@pytest.mark.asyncio
async def test_unrecognized_verdict_normalizes_to_safe() -> None:
    async def fake_classifier(_text: str, _categories: list[str]) -> str:
        return "not_a_real_category"

    result = await content_safety_reasoning(
        "hello",
        categories=_CATEGORIES,
        classifier_fn=fake_classifier,
        settings=Settings(),
    )
    assert result == "safe"


# ── Fail-open: timeout / exception (RF-GRD-005) ──────────────────────────────


@pytest.mark.asyncio
async def test_timeout_fails_open_to_safe() -> None:
    async def slow_classifier(_text: str, _categories: list[str]) -> str:
        await asyncio.sleep(10)
        return "violence"

    settings = Settings(nemo_classifier_timeout_seconds=0.01)
    result = await content_safety_reasoning(
        "text",
        categories=_CATEGORIES,
        classifier_fn=slow_classifier,
        settings=settings,
    )
    assert result == "safe"


@pytest.mark.asyncio
async def test_exception_fails_open_to_safe() -> None:
    async def broken_classifier(_text: str, _categories: list[str]) -> str:
        raise RuntimeError("provider exploded")

    result = await content_safety_reasoning(
        "text",
        categories=_CATEGORIES,
        classifier_fn=broken_classifier,
        settings=Settings(),
    )
    assert result == "safe"


@pytest.mark.asyncio
async def test_never_raises_even_on_classifier_exception() -> None:
    async def broken_classifier(_text: str, _categories: list[str]) -> str:
        raise ValueError("boom")

    # Must not propagate — this call itself must not raise.
    result = await content_safety_reasoning(
        "text", categories=_CATEGORIES, classifier_fn=broken_classifier, settings=Settings()
    )
    assert result == "safe"


# ── OTel counters/histogram (RF-GRD-011) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_otel_counter_on_safe() -> None:
    async def fake_classifier(_text: str, _categories: list[str]) -> str:
        return "safe"

    with patch("prismal.security.nemo_actions.OTelManager") as mock_otel_cls:
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        await content_safety_reasoning(
            "hi", categories=_CATEGORIES, classifier_fn=fake_classifier, settings=Settings()
        )

    mock_otel.increment_counter.assert_called_once()
    args, kwargs = mock_otel.increment_counter.call_args
    assert args[0] == "nemo_classifier_checks"
    assert kwargs["attributes"]["result"] == "safe"
    mock_otel.record_histogram.assert_called_once()
    assert mock_otel.record_histogram.call_args[0][0] == "nemo_classifier_latency"


@pytest.mark.asyncio
async def test_emits_otel_counter_result_timeout() -> None:
    async def slow_classifier(_text: str, _categories: list[str]) -> str:
        await asyncio.sleep(10)
        return "violence"

    settings = Settings(nemo_classifier_timeout_seconds=0.01)
    with patch("prismal.security.nemo_actions.OTelManager") as mock_otel_cls:
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        await content_safety_reasoning(
            "text", categories=_CATEGORIES, classifier_fn=slow_classifier, settings=settings
        )

    kwargs = mock_otel.increment_counter.call_args.kwargs
    assert kwargs["attributes"]["result"] == "timeout"


@pytest.mark.asyncio
async def test_emits_otel_counter_result_error() -> None:
    async def broken_classifier(_text: str, _categories: list[str]) -> str:
        raise RuntimeError("boom")

    with patch("prismal.security.nemo_actions.OTelManager") as mock_otel_cls:
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        await content_safety_reasoning(
            "text", categories=_CATEGORIES, classifier_fn=broken_classifier, settings=Settings()
        )

    kwargs = mock_otel.increment_counter.call_args.kwargs
    assert kwargs["attributes"]["result"] == "error"


@pytest.mark.asyncio
async def test_emits_otel_counter_result_blocked() -> None:
    async def fake_classifier(_text: str, _categories: list[str]) -> str:
        return "violence"

    with patch("prismal.security.nemo_actions.OTelManager") as mock_otel_cls:
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        await content_safety_reasoning(
            "text", categories=_CATEGORIES, classifier_fn=fake_classifier, settings=Settings()
        )

    kwargs = mock_otel.increment_counter.call_args.kwargs
    assert kwargs["attributes"]["result"] == "blocked"
    assert kwargs["attributes"]["category"] == "violence"


# ── Default classifier_fn — providers/ + SecurePromptBuilder (Rule #1, #4) ───


@pytest.mark.asyncio
async def test_default_classifier_uses_secure_prompt_builder_and_provider_registry() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="safe"))

    mock_registry_instance = MagicMock()
    mock_registry_instance.get_llm.return_value = mock_llm

    fake_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "<user_input>some text</user_input>"},
    ]

    with (
        patch(
            "prismal.providers.registry.ProviderRegistry",
            return_value=mock_registry_instance,
        ) as mock_registry_cls,
        patch(
            "prismal.security.prompt_builder.SecurePromptBuilder.build",
            return_value=fake_messages,
        ) as mock_build,
    ):
        result = await content_safety_reasoning(
            "some text", categories=_CATEGORIES, settings=Settings(llm_provider="")
        )

    assert result == "safe"
    mock_registry_cls.assert_called_once()
    mock_llm.ainvoke.assert_awaited_once()
    mock_build.assert_called_once()
    # The raw text must reach the LLM only via the builder's user= kwarg, never
    # f-string concatenated directly into a system/instruction string.
    _, call_kwargs = mock_build.call_args
    assert call_kwargs["user"] == "some text"


# ── register() ────────────────────────────────────────────────────────────────


def test_register_noop_when_classifier_disabled() -> None:
    rails = MagicMock()
    register(rails, settings=Settings(nemo_classifier_enabled=False))
    rails.register_action.assert_not_called()


def test_register_registers_action_when_classifier_enabled() -> None:
    rails = MagicMock()
    register(rails, settings=Settings(nemo_classifier_enabled=True))
    rails.register_action.assert_called_once()
    args, _ = rails.register_action.call_args
    assert callable(args[0])
    assert args[1] == "content_safety_reasoning"


@pytest.mark.asyncio
async def test_registered_action_defaults_to_settings_categories() -> None:
    rails = MagicMock()
    settings = Settings(nemo_classifier_enabled=True, nemo_classifier_categories=["violence"])
    register(rails, settings=settings)
    action_fn = rails.register_action.call_args[0][0]

    with patch(
        "prismal.security.nemo_actions.content_safety_reasoning",
        new=AsyncMock(return_value="safe"),
    ) as mock_reasoning:
        result = await action_fn("hello")

    assert result == "safe"
    mock_reasoning.assert_awaited_once()
    _, call_kwargs = mock_reasoning.call_args
    assert call_kwargs["categories"] == ["violence"]


@pytest.mark.asyncio
async def test_registered_action_accepts_explicit_categories_override() -> None:
    rails = MagicMock()
    settings = Settings(nemo_classifier_enabled=True, nemo_classifier_categories=["violence"])
    register(rails, settings=settings)
    action_fn = rails.register_action.call_args[0][0]

    with patch(
        "prismal.security.nemo_actions.content_safety_reasoning",
        new=AsyncMock(return_value="safe"),
    ) as mock_reasoning:
        await action_fn("hello", categories=["pii_request"])

    call_kwargs = mock_reasoning.call_args.kwargs
    assert call_kwargs["categories"] == ["pii_request"]
