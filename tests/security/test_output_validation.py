"""Security tests: PII and API key patterns must be detected in output."""

from __future__ import annotations

import pytest

from lightagent.security.guardrails import GuardrailsEngine

_PII_SAMPLES = [
    ("email", "Please reply to john.doe@company.org for assistance."),
    ("phone_us", "Call us at (555) 123-4567 anytime."),
    ("phone_us", "Call us at 555-123-4567 anytime."),
    ("ssn", "His SSN is 123-45-6789."),
    ("credit_card", "Card number: 4111111111111111"),
]

_API_KEY_SAMPLES = [
    ("anthropic", "sk-ant-" + "A" * 95),
    ("openai", "sk-" + "B" * 48),
    ("google", "AIza" + "C" * 35),
    ("aws_access", "AKIAIOSFODNN7EXAMPLE"),
]


@pytest.fixture
def engine() -> GuardrailsEngine:
    """Return a GuardrailsEngine with default strict settings."""
    return GuardrailsEngine()


@pytest.mark.parametrize(("pii_type", "text"), _PII_SAMPLES)
@pytest.mark.asyncio
async def test_pii_detected_in_output(engine: GuardrailsEngine, pii_type: str, text: str) -> None:
    """Every PII sample must be detected and flagged in LLM output."""
    result = await engine.validate_output(text)
    assert not result.safe, f"{pii_type} not detected in: {text!r}"
    assert any("pii" in r for r in result.reasons)


@pytest.mark.parametrize(("key_type", "key_value"), _API_KEY_SAMPLES)
@pytest.mark.asyncio
async def test_api_key_detected_in_output(
    engine: GuardrailsEngine, key_type: str, key_value: str
) -> None:
    """Every API key sample must be detected and flagged in LLM output."""
    result = await engine.validate_output(f"Here is the key: {key_value}")
    assert not result.safe, f"{key_type} key not detected"
    assert any("api_keys" in r for r in result.reasons)
