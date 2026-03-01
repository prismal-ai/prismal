"""Unit tests for lightagent.security.nemo_rails (SPEC-019 / T-200)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.security.nemo_rails import (
    NemoRailsLayer,
    _extract_response_text,
    _parse_block_response,
    get_nemo_layer,
)

# ── _parse_block_response ────────────────────────────────────────────────────


def test_parse_block_response_violence() -> None:
    """Sentinel prefix for violence category is detected."""
    blocked, category = _parse_block_response(
        "[NEMO_BLOCKED:violence] I can't help with that."
    )
    assert blocked is True
    assert category == "violence"


def test_parse_block_response_self_harm() -> None:
    """Sentinel prefix for self_harm category is detected."""
    blocked, category = _parse_block_response(
        "[NEMO_BLOCKED:self_harm] Please reach out for help."
    )
    assert blocked is True
    assert category == "self_harm"


def test_parse_block_response_illegal_activities() -> None:
    """Sentinel prefix for illegal_activities is detected."""
    blocked, category = _parse_block_response(
        "[NEMO_BLOCKED:illegal_activities] Can't assist."
    )
    assert blocked is True
    assert category == "illegal_activities"


def test_parse_block_response_pii_request() -> None:
    """Sentinel prefix for pii_request is detected."""
    blocked, category = _parse_block_response("[NEMO_BLOCKED:pii_request] No PII.")
    assert blocked is True
    assert category == "pii_request"


def test_parse_block_response_competitor_disparagement() -> None:
    """Sentinel prefix for competitor_disparagement is detected."""
    blocked, category = _parse_block_response(
        "[NEMO_BLOCKED:competitor_disparagement] Won't disparage."
    )
    assert blocked is True
    assert category == "competitor_disparagement"


def test_parse_block_response_unsafe_output() -> None:
    """Sentinel prefix for unsafe_output category is detected."""
    blocked, category = _parse_block_response(
        "[NEMO_BLOCKED:unsafe_output] Revising response."
    )
    assert blocked is True
    assert category == "unsafe_output"


def test_parse_block_response_clean() -> None:
    """Normal (non-blocked) response returns (False, '')."""
    blocked, category = _parse_block_response("Sure, here is the answer you wanted.")
    assert blocked is False
    assert category == ""


def test_parse_block_response_empty_string() -> None:
    """Empty string returns (False, '')."""
    blocked, category = _parse_block_response("")
    assert blocked is False
    assert category == ""


def test_parse_block_response_malformed_no_close_bracket() -> None:
    """Malformed sentinel with missing ] returns (False, '')."""
    blocked, category = _parse_block_response("[NEMO_BLOCKED:violence no close bracket")
    assert blocked is False
    assert category == ""


# ── _extract_response_text ───────────────────────────────────────────────────


def test_extract_response_text_string() -> None:
    """Plain string passthrough."""
    assert _extract_response_text("hello") == "hello"


def test_extract_response_text_dict_with_content() -> None:
    """Dict with 'content' key returns its value as string."""
    assert _extract_response_text({"content": "the text"}) == "the text"


def test_extract_response_text_dict_without_content() -> None:
    """Dict without 'content' key returns str(dict)."""
    result = _extract_response_text({"other": "val"})
    assert "other" in result


def test_extract_response_text_arbitrary_object() -> None:
    """Non-str/dict returns str() representation."""

    class Obj:
        def __str__(self) -> str:
            return "custom_str"

    assert _extract_response_text(Obj()) == "custom_str"


# ── NemoRailsLayer — unavailable paths ───────────────────────────────────────


def test_nemo_rails_layer_missing_dir(tmp_path: Path) -> None:
    """Non-existent config dir makes available=False."""
    missing = tmp_path / "nonexistent"
    layer = NemoRailsLayer(missing)
    assert layer.available is False


def test_nemo_rails_layer_import_error(tmp_path: Path) -> None:
    """ImportError on nemoguardrails makes available=False."""
    config_dir = tmp_path / "nemo_rails"
    config_dir.mkdir()
    with patch.dict("sys.modules", {"nemoguardrails": None}):
        layer = NemoRailsLayer(config_dir)
    assert layer.available is False


@pytest.mark.asyncio
async def test_check_input_unavailable() -> None:
    """When available=False, check_input returns (False, '')."""
    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = None  # type: ignore[attr-defined]
    blocked, cat = await layer.check_input("test text")
    assert blocked is False
    assert cat == ""


@pytest.mark.asyncio
async def test_check_output_unavailable() -> None:
    """When available=False, check_output returns (False, '')."""
    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = None  # type: ignore[attr-defined]
    blocked, cat = await layer.check_output("test output")
    assert blocked is False
    assert cat == ""


# ── NemoRailsLayer — available paths (mocked LLMRails) ──────────────────────


def _make_layer_with_mock_rails(response_text: str) -> NemoRailsLayer:
    """Build a NemoRailsLayer with a mock LLMRails returning response_text."""
    mock_rails = MagicMock()
    mock_rails.generate_async = AsyncMock(return_value=response_text)

    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = mock_rails  # type: ignore[attr-defined]
    layer._config_path = Path("config/nemo_rails")  # type: ignore[attr-defined]
    return layer


@pytest.mark.asyncio
async def test_check_input_blocked_violence() -> None:
    """check_input returns (True, 'violence') for a blocked response."""
    layer = _make_layer_with_mock_rails(
        "[NEMO_BLOCKED:violence] Can't assist with violence."
    )
    blocked, category = await layer.check_input("how do I hurt someone")
    assert blocked is True
    assert category == "violence"


@pytest.mark.asyncio
async def test_check_input_not_blocked() -> None:
    """check_input returns (False, '') for a normal non-blocked response."""
    layer = _make_layer_with_mock_rails("Sure, here is information about Python.")
    blocked, category = await layer.check_input("explain Python generators")
    assert blocked is False
    assert category == ""


@pytest.mark.asyncio
async def test_check_output_blocked_unsafe() -> None:
    """check_output returns (True, 'unsafe_output') when output rail triggers."""
    layer = _make_layer_with_mock_rails(
        "[NEMO_BLOCKED:unsafe_output] Revising response."
    )
    blocked, category = await layer.check_output("Here is how to hurt someone")
    assert blocked is True
    assert category == "unsafe_output"


@pytest.mark.asyncio
async def test_check_output_not_blocked() -> None:
    """check_output returns (False, '') for safe output."""
    layer = _make_layer_with_mock_rails("Here is a helpful response.")
    blocked, category = await layer.check_output("Here is a helpful response.")
    assert blocked is False
    assert category == ""


@pytest.mark.asyncio
async def test_check_input_timeout_fails_open() -> None:
    """Timeout during check_input results in fail-open (False, '')."""
    mock_rails = MagicMock()

    async def slow_generate(*_args: object, **_kwargs: object) -> str:
        await asyncio.sleep(10)
        return "response"

    mock_rails.generate_async = slow_generate

    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = mock_rails  # type: ignore[attr-defined]
    layer._config_path = Path("config/nemo_rails")  # type: ignore[attr-defined]

    with patch("lightagent.security.nemo_rails._NEMO_TIMEOUT_SECONDS", 0.01):
        blocked, category = await layer.check_input("some text")

    assert blocked is False
    assert category == ""


@pytest.mark.asyncio
async def test_check_output_timeout_fails_open() -> None:
    """Timeout during check_output results in fail-open (False, '')."""
    mock_rails = MagicMock()

    async def slow_generate(*_args: object, **_kwargs: object) -> str:
        await asyncio.sleep(10)
        return "response"

    mock_rails.generate_async = slow_generate

    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = mock_rails  # type: ignore[attr-defined]
    layer._config_path = Path("config/nemo_rails")  # type: ignore[attr-defined]

    with patch("lightagent.security.nemo_rails._NEMO_TIMEOUT_SECONDS", 0.01):
        blocked, category = await layer.check_output("some output")

    assert blocked is False
    assert category == ""


@pytest.mark.asyncio
async def test_check_input_exception_fails_open() -> None:
    """Generic exception in check_input results in fail-open (False, '')."""
    mock_rails = MagicMock()
    mock_rails.generate_async = AsyncMock(side_effect=RuntimeError("NeMo error"))

    layer = NemoRailsLayer.__new__(NemoRailsLayer)
    layer._rails = mock_rails  # type: ignore[attr-defined]
    layer._config_path = Path("config/nemo_rails")  # type: ignore[attr-defined]

    blocked, category = await layer.check_input("some text")
    assert blocked is False
    assert category == ""


# ── get_nemo_layer ───────────────────────────────────────────────────────────


def test_get_nemo_layer_disabled() -> None:
    """Returns None when nemo_guardrails_enabled is False (zero overhead)."""
    mock_settings = MagicMock()
    mock_settings.nemo_guardrails_enabled = False

    with patch("lightagent.core.config.get_settings", return_value=mock_settings):
        result = get_nemo_layer()

    assert result is None


def test_get_nemo_layer_enabled_missing_config(tmp_path: Path) -> None:
    """Returns a NemoRailsLayer (available=False) when config dir is missing."""
    mock_settings = MagicMock()
    mock_settings.nemo_guardrails_enabled = True

    missing = tmp_path / "no_nemo"
    with patch("lightagent.core.config.get_settings", return_value=mock_settings):
        layer = get_nemo_layer(missing)

    assert layer is not None
    assert layer.available is False
