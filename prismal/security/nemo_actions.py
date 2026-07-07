"""Reasoning-capable safety-classifier NeMo custom action (Phase GRD — SPEC-GRD-NEMO-CLS-001).

Scores text against configurable categories using a reasoning-capable LLM
judgment call, gated by ``settings.nemo_classifier_enabled``. Registered as a
NeMo custom action on ``LLMRails`` from ``NemoRailsLayer.__init__`` and
invoked from a Colang flow (``config/nemo_rails/safety_classifier.co``), which
wraps a non-"safe" verdict in the existing ``[NEMO_BLOCKED:<category>]``
sentinel — no new parsing is required downstream (DD-GRD-002).

Fail-open by design: a timeout or any exception returns ``"safe"`` and is
never propagated to the caller (DD-GRD-003 / RF-GRD-005). The classifier's
own judgment-call timeout (``settings.nemo_classifier_timeout_seconds``) is
independent from the existing 450ms dialog-rail budget in ``nemo_rails.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.security.audit import AuditLogger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.security.nemo_actions")

ClassifierFn = Callable[[str, Sequence[str]], Awaitable[str]]

_audit_logger = AuditLogger()


async def _default_classifier_fn(
    text: str, categories: Sequence[str], *, settings: Settings
) -> str:
    """Default judgment call: resolves an LLM via ``providers/`` (Rule #4).

    ``text`` reaches the classifier's prompt only via :class:`SecurePromptBuilder`
    (Critical Rule #1) — never f-string concatenated into the instructions.
    """
    from prismal.providers.registry import ProviderRegistry
    from prismal.security.prompt_builder import SecurePromptBuilder

    system = (
        "You are a content-safety classifier. Score the user message against "
        f"these categories: {', '.join(categories)}. Respond with exactly one "
        "matching category name, or 'safe' if none apply. Respond with only "
        "the category name or 'safe' — no other text."
    )
    messages = SecurePromptBuilder().build(system=system, user=text)

    llm = ProviderRegistry(settings=settings).get_llm(settings.nemo_classifier_model)
    response = await llm.ainvoke(messages)
    content = getattr(response, "content", response)
    return str(content).strip().lower()


def _normalize_verdict(raw_verdict: str, categories: Sequence[str]) -> str:
    """Normalize a classifier's raw output to a configured category or 'safe'."""
    normalized = raw_verdict.strip().lower()
    valid = {c.lower() for c in categories}
    if normalized in valid:
        return normalized
    if normalized == "safe":
        return "safe"
    logger.warning("nemo_classifier_unrecognized_verdict", raw_verdict=raw_verdict[:100])
    return "safe"


async def content_safety_reasoning(
    text: str,
    *,
    categories: Sequence[str],
    classifier_fn: ClassifierFn | None = None,
    settings: Settings | None = None,
) -> str:
    """Score ``text`` against ``categories`` using a reasoning-capable safety classifier.

    Bounded by ``settings.nemo_classifier_timeout_seconds`` — independent from
    the base dialog-rail's ``_NEMO_TIMEOUT_SECONDS`` (DD-GRD-003). Never
    raises: a timeout or any exception returns ``"safe"`` (fail-open) and is
    audited + counted as ``result="timeout"``/``"error"``.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()

    fn = classifier_fn
    if fn is None:

        async def fn(t: str, cats: Sequence[str]) -> str:
            return await _default_classifier_fn(t, cats, settings=settings)

    start = time.monotonic()
    result = "safe"
    verdict = "safe"
    try:
        raw_verdict = await asyncio.wait_for(
            fn(text, categories), timeout=settings.nemo_classifier_timeout_seconds
        )
        verdict = _normalize_verdict(raw_verdict, categories)
        result = "safe" if verdict == "safe" else "blocked"
    except TimeoutError:
        result = "timeout"
        logger.debug("nemo_classifier_timeout", timeout=settings.nemo_classifier_timeout_seconds)
    except Exception as exc:
        result = "error"
        logger.warning("nemo_classifier_error", error=str(exc)[:200])
    finally:
        latency = time.monotonic() - start
        with suppress(Exception):
            OTelManager().increment_counter(
                "nemo_classifier_checks", attributes={"category": verdict, "result": result}
            )
            OTelManager().record_histogram("nemo_classifier_latency", latency)
        with suppress(Exception):
            _audit_logger.log_event(
                "nemo_classifier_check",
                {"category": verdict, "result": result, "text_length": len(text)},
            )

    return verdict


def register(rails: Any, *, settings: Settings | None = None) -> None:
    """Register :func:`content_safety_reasoning` as a NeMo custom action.

    No-op unless ``settings.nemo_classifier_enabled``. Called from
    ``NemoRailsLayer.__init__`` after ``RailsConfig.from_path()`` succeeds.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()

    if not settings.nemo_classifier_enabled:
        return

    default_categories = list(settings.nemo_classifier_categories)

    async def _action(text: str, categories: Sequence[str] | None = None) -> str:
        cats = categories if categories is not None else default_categories
        return await content_safety_reasoning(text, categories=cats, settings=settings)

    rails.register_action(_action, "content_safety_reasoning")


__all__ = ["ClassifierFn", "content_safety_reasoning", "register"]
