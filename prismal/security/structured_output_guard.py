"""Schema-first structured-output validation with bounded re-ask (Phase GRD — SPEC-GRD-SOG-001).

``StructuredOutputGuard`` composes with — never replaces — :class:`OutputValidator`
(DD-GRD-006): it validates the model's raw pre-validation text against a Pydantic
schema via ``guardrails-ai``, retrying up to a bounded, Budget-metered number of
times, then hands the coerced value to ``OutputValidator.validate_tool_args()``
for its existing schema/escape checks, unchanged.

DD-GRD-004: only ``guardrails.Guard.validate()`` (pure schema validation, no LLM
call inside guardrails-ai itself) is used. The bounded re-ask loop is driven
entirely by this module via an injected ``reask_fn`` resolved from
``providers/`` (Rule #4) — guardrails-ai's own ``Guard.__call__(llm_api=...)``
re-ask mechanism is never invoked, so there is zero provider-isolation
compromise and no need to route around ``SecurePromptBuilder``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from prismal.core.exceptions import MissingDependencyError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.audit import AuditLogger

if TYPE_CHECKING:
    from pydantic import BaseModel

    from prismal.core.config import Settings

logger = get_logger("prismal.security.structured_output_guard")

BudgetGuardFn = Callable[[dict[str, object]], Awaitable[bool]]
ReaskFn = Callable[[str, str], Awaitable[str]]

# name -> (module path, class name). A minimal, extendable set of well-known
# Guardrails Hub validators (DD-GRD-007) — each requires its own separate
# install (`guardrails hub install hub://guardrails/<name>`); absence is
# handled gracefully by `_resolve_hub_validator`, never a hard dependency.
_HUB_VALIDATOR_REGISTRY: dict[str, tuple[str, str]] = {
    "detect_pii": ("guardrails.hub", "DetectPII"),
    "provenance_llm": ("guardrails.hub", "ProvenanceLLM"),
    "toxic_language": ("guardrails.hub", "ToxicLanguage"),
}


def _resolve_hub_validator(name: str) -> type[Any] | None:
    """Lazily import a named Hub validator class; ``None`` if unavailable.

    Never raises — an uninstalled or unknown validator name is skipped so one
    missing Hub package never fails the whole :meth:`StructuredOutputGuard.validate` call.
    """
    entry = _HUB_VALIDATOR_REGISTRY.get(name)
    if entry is None:
        logger.warning("hub_validator_unknown", name=name)
        return None
    module_path, class_name = entry
    try:
        import importlib

        module = importlib.import_module(module_path)
        return cast("type[Any]", getattr(module, class_name))
    except Exception as exc:
        logger.warning("hub_validator_unavailable", name=name, error=str(exc)[:200])
        return None


@dataclass(frozen=True)
class StructuredOutputVerdict:
    """Outcome of validating one piece of structured model output."""

    ok: bool
    reason: str = ""
    coerced: Any | None = None
    reask_count: int = 0
    hub_findings: list[str] = field(default_factory=list)


class StructuredOutputGuard:
    """Schema-first structured-output validation with bounded, metered re-ask.

    Composes with (does not replace) :class:`~prismal.security.output_validator.OutputValidator`.
    Degrades gracefully to :class:`MissingDependencyError` when the
    ``[guardrails-ai]`` extra is not installed — callers catch it once at
    construction time and fall back to ``OutputValidator.validate_tool_args()`` alone.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        reask_fn: ReaskFn | None = None,
        budget_guard_fn: BudgetGuardFn | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        """Initialize the guard; raises MissingDependencyError if guardrails-ai is absent."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._reask_fn = reask_fn
        self._budget_guard_fn = budget_guard_fn
        self._audit = audit or AuditLogger()

        try:
            import guardrails

            self._guardrails = guardrails
        except ImportError as exc:
            raise MissingDependencyError(
                "guardrails-ai is not installed", extra_to_install="guardrails-ai"
            ) from exc

    async def validate(
        self,
        tool_name: str,
        raw_output: str,
        schema: type[BaseModel],
        *,
        hub_validators: list[str] | None = None,
    ) -> StructuredOutputVerdict:
        """Validate ``raw_output`` against ``schema``, re-asking on failure up to a bound."""
        guard = self._guardrails.Guard.for_pydantic(schema)
        max_reasks = self._settings.structured_output_guard_max_reasks

        outcome = guard.validate(raw_output)
        reask_count = 0

        while not outcome.validation_passed and reask_count < max_reasks:
            if self._budget_guard_fn is not None and not await self._budget_guard_fn({}):
                self._finish(
                    tool_name, ok=False, reason="budget_denied", otel_outcome="budget_denied"
                )
                return StructuredOutputVerdict(
                    ok=False, reason="budget_denied", reask_count=reask_count
                )

            reask_fn = self._reask_fn or self._default_reask_fn
            raw_output = await reask_fn(str(schema.model_json_schema()), raw_output)
            reask_count += 1
            outcome = guard.validate(raw_output)

        if not outcome.validation_passed:
            self._finish(tool_name, ok=False, reason="reask_exhausted", otel_outcome="exhausted")
            return StructuredOutputVerdict(
                ok=False, reason="reask_exhausted", reask_count=reask_count
            )

        hub_findings = self._run_hub_validators(raw_output, hub_validators)
        self._finish(tool_name, ok=True, reason="", otel_outcome="resolved")
        return StructuredOutputVerdict(
            ok=True,
            coerced=outcome.validated_output,
            reask_count=reask_count,
            hub_findings=hub_findings,
        )

    # ── internals ──────────────────────────────────────────────────────────────

    async def _default_reask_fn(self, schema_repr: str, prior_raw_output: str) -> str:
        from prismal.providers.registry import ProviderRegistry
        from prismal.security.prompt_builder import SecurePromptBuilder

        system = (
            "The previous output did not match the required JSON schema. "
            f"Schema: {schema_repr}\nRespond with only corrected JSON matching the schema."
        )
        messages = SecurePromptBuilder().build(system=system, user=prior_raw_output)
        llm = ProviderRegistry(settings=self._settings).get_llm()
        response = await llm.ainvoke(messages)
        content = getattr(response, "content", response)
        return str(content)

    def _run_hub_validators(self, text: str, hub_validators: list[str] | None) -> list[str]:
        if not hub_validators or not self._settings.structured_output_guard_hub_validators_enabled:
            return []
        findings: list[str] = []
        for name in hub_validators:
            validator_cls = _resolve_hub_validator(name)
            if validator_cls is None:
                continue
            try:
                result = validator_cls().validate(text, {})
                if getattr(result, "outcome", None) == "fail":
                    message = getattr(result, "error_message", "validation failed")
                    findings.append(f"{name}: {message}")
                    with suppress(Exception):
                        OTelManager().increment_counter(
                            "structured_output_hub_validator_blocks",
                            attributes={"validator": name},
                        )
            except Exception as exc:
                logger.warning("hub_validator_execution_failed", name=name, error=str(exc)[:200])
        return findings

    def _finish(self, tool_name: str, *, ok: bool, reason: str, otel_outcome: str) -> None:
        with suppress(Exception):
            OTelManager().increment_counter(
                "structured_output_reask", attributes={"outcome": otel_outcome}
            )
        with suppress(Exception):
            self._audit.log_event(
                "structured_output_validate", {"tool": tool_name, "ok": ok, "reason": reason}
            )


__all__ = ["BudgetGuardFn", "ReaskFn", "StructuredOutputGuard", "StructuredOutputVerdict"]
