"""Unit tests for prismal.security.structured_output_guard (Phase GRD — SPEC-GRD-SOG-001)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from prismal.core.config import Settings
from prismal.core.exceptions import MissingDependencyError


class _Schema(BaseModel):
    name: str
    age: int


def _mock_guardrails_module(*, outcomes: list[MagicMock]) -> MagicMock:
    """Build a fake ``guardrails`` module whose ``Guard.validate`` yields *outcomes* in order."""
    mock_guard_instance = MagicMock()
    mock_guard_instance.validate.side_effect = outcomes
    mock_guard_instance.use.return_value = mock_guard_instance

    mock_guard_cls = MagicMock()
    mock_guard_cls.for_pydantic.return_value = mock_guard_instance

    mock_module = MagicMock()
    mock_module.Guard = mock_guard_cls
    return mock_module


def _outcome(*, passed: bool, validated_output: dict[str, object] | None = None) -> MagicMock:
    outcome = MagicMock()
    outcome.validation_passed = passed
    outcome.validated_output = validated_output
    return outcome


# ── Construction — MissingDependencyError (RF-GRD-010) ───────────────────────


def test_construction_raises_missing_dependency_error_when_guardrails_absent() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    with patch.dict(sys.modules, {"guardrails": None}):
        with pytest.raises(MissingDependencyError) as exc_info:
            StructuredOutputGuard(settings=Settings())

    assert exc_info.value.extra_to_install == "guardrails-ai"


def test_construction_succeeds_when_guardrails_installed() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[])
    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(settings=Settings())

    assert guard is not None


# ── validate() — schema check only (no re-ask needed) ────────────────────────


@pytest.mark.asyncio
async def test_validate_returns_ok_on_first_pass() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(settings=Settings())
        verdict = await guard.validate("my_tool", '{"name": "bob", "age": 5}', _Schema)

    assert verdict.ok is True
    assert verdict.coerced == {"name": "bob", "age": 5}
    assert verdict.reask_count == 0


@pytest.mark.asyncio
async def test_validate_zero_max_reasks_returns_exhausted_immediately() -> None:
    """max_reasks=0 means validate once, no re-ask (per settings description)."""
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[_outcome(passed=False)])
    settings = Settings(structured_output_guard_max_reasks=0)
    reask_fn = AsyncMock()

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(settings=settings, reask_fn=reask_fn)
        verdict = await guard.validate("my_tool", "not json", _Schema)

    assert verdict.ok is False
    assert verdict.reason == "reask_exhausted"
    assert verdict.reask_count == 0
    reask_fn.assert_not_awaited()


# ── Bounded, metered re-ask loop (RF-GRD-006, RF-GRD-007) ────────────────────


@pytest.mark.asyncio
async def test_validate_resolves_after_one_reask() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[
            _outcome(passed=False),
            _outcome(passed=True, validated_output={"name": "bob", "age": 5}),
        ]
    )
    reask_fn = AsyncMock(return_value='{"name": "bob", "age": 5}')

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=2), reask_fn=reask_fn
        )
        verdict = await guard.validate("my_tool", "bad json", _Schema)

    assert verdict.ok is True
    assert verdict.reask_count == 1
    reask_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_never_resolves_returns_reask_exhausted() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=False), _outcome(passed=False), _outcome(passed=False)]
    )
    reask_fn = AsyncMock(return_value="still bad")

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=2), reask_fn=reask_fn
        )
        verdict = await guard.validate("my_tool", "bad json", _Schema)

    assert verdict.ok is False
    assert verdict.reason == "reask_exhausted"
    assert verdict.reask_count == 2
    assert reask_fn.await_count == 2


@pytest.mark.asyncio
async def test_budget_guard_denial_on_first_attempt_stops_immediately() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[_outcome(passed=False)])
    reask_fn = AsyncMock()
    budget_guard_fn = AsyncMock(return_value=False)

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=2),
            reask_fn=reask_fn,
            budget_guard_fn=budget_guard_fn,
        )
        verdict = await guard.validate("my_tool", "bad json", _Schema)

    assert verdict.ok is False
    assert verdict.reason == "budget_denied"
    assert verdict.reask_count == 0
    reask_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_guard_denial_after_one_attempt_stops_with_partial_count() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[_outcome(passed=False), _outcome(passed=False)])
    reask_fn = AsyncMock(return_value="still bad")
    budget_guard_fn = AsyncMock(side_effect=[True, False])

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=3),
            reask_fn=reask_fn,
            budget_guard_fn=budget_guard_fn,
        )
        verdict = await guard.validate("my_tool", "bad json", _Schema)

    assert verdict.ok is False
    assert verdict.reason == "budget_denied"
    assert verdict.reask_count == 1
    assert reask_fn.await_count == 1


@pytest.mark.asyncio
async def test_none_budget_guard_fn_is_zero_overhead_always_allow() -> None:
    """budget_guard_fn=None (default) never blocks a re-ask attempt."""
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=False), _outcome(passed=True, validated_output={"ok": True})]
    )
    reask_fn = AsyncMock(return_value='{"ok": true}')

    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=2), reask_fn=reask_fn
        )
        verdict = await guard.validate("my_tool", "bad", _Schema)

    assert verdict.ok is True
    assert verdict.reask_count == 1


# ── Default reask_fn — providers/ + SecurePromptBuilder ──────────────────────


@pytest.mark.asyncio
async def test_default_reask_fn_uses_provider_registry() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=False), _outcome(passed=True, validated_output={"ok": True})]
    )
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"ok": true}'))
    mock_registry_instance = MagicMock()
    mock_registry_instance.get_llm.return_value = mock_llm

    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch(
            "prismal.providers.registry.ProviderRegistry",
            return_value=mock_registry_instance,
        ) as mock_registry_cls,
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=1, llm_provider="")
        )
        verdict = await guard.validate("my_tool", "bad", _Schema)

    assert verdict.ok is True
    mock_registry_cls.assert_called_once()
    mock_llm.ainvoke.assert_awaited_once()


# ── Hub validators (RF-GRD-009, opt-in per-call + master gate) ───────────────


@pytest.mark.asyncio
async def test_hub_validators_not_run_when_master_gate_disabled() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch("prismal.security.structured_output_guard._resolve_hub_validator") as mock_resolve,
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=False)
        )
        verdict = await guard.validate(
            "my_tool", '{"name": "bob", "age": 5}', _Schema, hub_validators=["detect_pii"]
        )

    assert verdict.hub_findings == []
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
async def test_hub_validators_not_run_when_none_named_per_call() -> None:
    """Master gate True but no per-call hub_validators -> zero-cost, none run (DD-GRD-007)."""
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch("prismal.security.structured_output_guard._resolve_hub_validator") as mock_resolve,
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=True)
        )
        verdict = await guard.validate("my_tool", '{"name": "bob", "age": 5}', _Schema)

    assert verdict.hub_findings == []
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
async def test_hub_validator_failure_recorded_in_findings() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    mock_fail_result = MagicMock()
    mock_fail_result.outcome = "fail"
    mock_fail_result.error_message = "PII detected"

    mock_validator_instance = MagicMock()
    mock_validator_instance.validate.return_value = mock_fail_result
    mock_validator_cls = MagicMock(return_value=mock_validator_instance)

    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch(
            "prismal.security.structured_output_guard._resolve_hub_validator",
            return_value=mock_validator_cls,
        ),
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=True)
        )
        verdict = await guard.validate(
            "my_tool", '{"name": "bob", "age": 5}', _Schema, hub_validators=["detect_pii"]
        )

    assert verdict.ok is True
    assert verdict.hub_findings == ["detect_pii: PII detected"]


@pytest.mark.asyncio
async def test_hub_validator_pass_result_adds_no_finding() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    mock_pass_result = MagicMock()
    mock_pass_result.outcome = "pass"

    mock_validator_instance = MagicMock()
    mock_validator_instance.validate.return_value = mock_pass_result
    mock_validator_cls = MagicMock(return_value=mock_validator_instance)

    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch(
            "prismal.security.structured_output_guard._resolve_hub_validator",
            return_value=mock_validator_cls,
        ),
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=True)
        )
        verdict = await guard.validate(
            "my_tool", '{"name": "bob", "age": 5}', _Schema, hub_validators=["detect_pii"]
        )

    assert verdict.hub_findings == []


@pytest.mark.asyncio
async def test_unresolvable_hub_validator_gracefully_skipped() -> None:
    """An uninstalled/unknown Hub validator name never crashes validate()."""
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch(
            "prismal.security.structured_output_guard._resolve_hub_validator",
            return_value=None,
        ),
    ):
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=True)
        )
        verdict = await guard.validate(
            "my_tool", '{"name": "bob", "age": 5}', _Schema, hub_validators=["not_a_real_one"]
        )

    assert verdict.ok is True
    assert verdict.hub_findings == []


# ── OTel counters (RF-GRD-011) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_reask_counter_resolved_on_first_pass() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch("prismal.security.structured_output_guard.OTelManager") as mock_otel_cls,
    ):
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        guard = StructuredOutputGuard(settings=Settings())
        await guard.validate("my_tool", '{"name": "bob", "age": 5}', _Schema)

    args, kwargs = mock_otel.increment_counter.call_args
    assert args[0] == "structured_output_reask"
    assert kwargs["attributes"]["outcome"] == "resolved"


@pytest.mark.asyncio
async def test_emits_reask_counter_exhausted() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[_outcome(passed=False)])
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch("prismal.security.structured_output_guard.OTelManager") as mock_otel_cls,
    ):
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        guard = StructuredOutputGuard(settings=Settings(structured_output_guard_max_reasks=0))
        await guard.validate("my_tool", "bad", _Schema)

    kwargs = mock_otel.increment_counter.call_args.kwargs
    assert kwargs["attributes"]["outcome"] == "exhausted"


@pytest.mark.asyncio
async def test_emits_reask_counter_budget_denied() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(outcomes=[_outcome(passed=False)])
    budget_guard_fn = AsyncMock(return_value=False)
    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch("prismal.security.structured_output_guard.OTelManager") as mock_otel_cls,
    ):
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_max_reasks=2),
            budget_guard_fn=budget_guard_fn,
        )
        await guard.validate("my_tool", "bad", _Schema)

    kwargs = mock_otel.increment_counter.call_args.kwargs
    assert kwargs["attributes"]["outcome"] == "budget_denied"


@pytest.mark.asyncio
async def test_emits_hub_validator_block_counter() -> None:
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    mock_fail_result = MagicMock()
    mock_fail_result.outcome = "fail"
    mock_fail_result.error_message = "PII detected"
    mock_validator_cls = MagicMock(
        return_value=MagicMock(validate=MagicMock(return_value=mock_fail_result))
    )

    with (
        patch.dict(sys.modules, {"guardrails": mock_module}),
        patch(
            "prismal.security.structured_output_guard._resolve_hub_validator",
            return_value=mock_validator_cls,
        ),
        patch("prismal.security.structured_output_guard.OTelManager") as mock_otel_cls,
    ):
        mock_otel = MagicMock()
        mock_otel_cls.return_value = mock_otel
        guard = StructuredOutputGuard(
            settings=Settings(structured_output_guard_hub_validators_enabled=True)
        )
        await guard.validate(
            "my_tool", '{"name": "bob", "age": 5}', _Schema, hub_validators=["detect_pii"]
        )

    calls = [
        c
        for c in mock_otel.increment_counter.call_args_list
        if c.args[0] == "structured_output_hub_validator_blocks"
    ]
    assert len(calls) == 1
    assert calls[0].kwargs["attributes"]["validator"] == "detect_pii"


# ── Composition with OutputValidator (RF-GRD-008, DD-GRD-006) ────────────────


@pytest.mark.asyncio
async def test_coerced_output_still_flows_through_output_validator() -> None:
    from prismal.security.output_validator import OutputValidator
    from prismal.security.structured_output_guard import StructuredOutputGuard

    mock_module = _mock_guardrails_module(
        outcomes=[_outcome(passed=True, validated_output={"name": "bob", "age": 5})]
    )
    with patch.dict(sys.modules, {"guardrails": mock_module}):
        guard = StructuredOutputGuard(settings=Settings())
        verdict = await guard.validate("my_tool", '{"name": "bob", "age": 5}', _Schema)

    output_verdict = OutputValidator().validate_tool_args("my_tool", verdict.coerced, _Schema)
    assert output_verdict.ok is True
    assert output_verdict.coerced == {"name": "bob", "age": 5}
