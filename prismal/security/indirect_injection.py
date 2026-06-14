"""Indirect prompt-injection detector (Phase H — SPEC-HRD-INJ-001).

Untrusted content returning from tools / RAG / web / media re-enters the model
on a path that the input-side guardrails never inspect. ``IndirectInjectionDetector``
closes that gap: before such content is re-injected it is scored by the existing
:class:`~prismal.security.guardrails.GuardrailsEngine` **plus** a small
indirect-injection heuristic pack (assistant-directed imperatives, tool/role
overrides, exfiltration intent). An optional LLM classifier (``providers/``) can
be max-combined when enabled.

The detector is identity-agnostic and never raises on the hot path: in ``warn``
mode it flags + sanitizes (fail-open); in ``enforce`` mode it blocks (fail-closed).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.security.audit import AuditLogger
    from prismal.security.guardrails import GuardrailsEngine
    from prismal.security.taint import TaintTag

logger = get_logger("prismal.security.indirect_injection")

ClassifierFn = Callable[[str], Awaitable[float]]

_RE_FLAGS = re.IGNORECASE | re.DOTALL | re.MULTILINE

# Indirect-injection heuristic pack. Each entry is a (category, pattern) whose
# match flags assistant-directed manipulation embedded in untrusted content.
_HEURISTIC_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "instruction_override": [
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,40}\b"
            r"(previous|prior|above|earlier|all)\b.{0,20}\b(instruction|prompt|rule|context)",
            _RE_FLAGS,
        ),
        re.compile(r"\bnew\b\s+(instructions?|rules?|system\s+prompt)\b", _RE_FLAGS),
        re.compile(r"\byou\s+are\s+now\b", _RE_FLAGS),
        re.compile(r"\bsystem\s*(prompt|message)\b\s*[:=]", _RE_FLAGS),
    ],
    "role_or_tool_override": [
        re.compile(
            r"\b(call|invoke|use|run|execute)\b.{0,30}\b(tool|function|command)\b", _RE_FLAGS
        ),
        re.compile(r"\b(delete_file|shell_exec|rm\s+-rf|drop\s+table|sudo|exec\()", _RE_FLAGS),
        re.compile(r"^\s*(role|assistant|system)\s*[:=]", _RE_FLAGS),
    ],
    "exfiltration": [
        re.compile(
            r"\b(reveal|print|show|leak|expose|send|exfiltrate|post|upload)\b.{0,40}"
            r"\b(system\s*prompt|api[_\s-]?key|secret|password|token|credential|env)",
            _RE_FLAGS,
        ),
        re.compile(r"\b(curl|wget|fetch)\b.{0,40}https?://", _RE_FLAGS),
        re.compile(r"\bemail\b.{0,40}(the\s+(result|output|data|content)|to\s+\S+@)", _RE_FLAGS),
    ],
}


@dataclass(frozen=True)
class InjectionVerdict:
    """Outcome of scoring one piece of untrusted content."""

    blocked: bool
    risk: float
    vector: Literal["direct", "tool", "rag", "media"]
    reason: str = ""
    sanitized: str | None = None  # content with neutralized directives (warn mode)


class IndirectInjectionDetector:
    """Scores untrusted content for indirect prompt injection before re-injection."""

    def __init__(
        self,
        *,
        guardrails: GuardrailsEngine | None = None,
        classifier_fn: ClassifierFn | None = None,
        audit: AuditLogger | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialize the detector.

        Args:
            guardrails: Reused scoring engine. Lazily constructed when omitted.
            classifier_fn: Optional async ``(text) -> risk`` LLM classifier.
            audit: Optional audit logger for hash-first flag/block records.
            settings: Settings snapshot; resolved from ``get_settings()`` if None.
        """
        self._guardrails = guardrails
        self._classifier_fn = classifier_fn
        self._audit = audit
        self._settings = settings

    def _resolve_settings(self) -> Settings:
        if self._settings is not None:
            return self._settings
        from prismal.core.config import get_settings

        return get_settings()

    def _resolve_guardrails(self) -> GuardrailsEngine:
        if self._guardrails is None:
            from prismal.security.guardrails import GuardrailsEngine

            self._guardrails = GuardrailsEngine()
        return self._guardrails

    async def check(
        self,
        content: str,
        *,
        vector: str,
        tag: TaintTag | None = None,  # noqa: ARG002 — provenance hint, reserved for richer scoring
    ) -> InjectionVerdict:
        """Score *content* and return an :class:`InjectionVerdict`. Never raises."""
        settings = self._resolve_settings()
        mode = settings.hardening_mode
        normalized_vector = self._normalize_vector(vector)

        if not content:
            return InjectionVerdict(blocked=False, risk=0.0, vector=normalized_vector)

        risk, reasons = await self._score(content, settings)
        threshold = settings.hardening_injection_threshold
        flagged = risk >= threshold

        if not flagged or mode == "off":
            return InjectionVerdict(
                blocked=False, risk=risk, vector=normalized_vector, reason=";".join(reasons)
            )

        reason = ";".join(reasons) or "indirect_injection"
        sanitized = self._sanitize(content)
        self._audit_and_count(normalized_vector, risk, reason, blocked=(mode == "enforce"))

        if mode == "enforce":
            return InjectionVerdict(
                blocked=True,
                risk=risk,
                vector=normalized_vector,
                reason=reason,
                sanitized=sanitized,
            )
        # warn — flag + sanitize, do not block (fail-open)
        return InjectionVerdict(
            blocked=False,
            risk=risk,
            vector=normalized_vector,
            reason=reason,
            sanitized=sanitized,
        )

    # ── internals ──────────────────────────────────────────────────────────────

    async def _score(self, content: str, settings: Settings) -> tuple[float, list[str]]:
        """Combine guardrails + heuristic (+ optional classifier) into [0,1] risk."""
        reasons: list[str] = []
        risk = 0.0

        # GuardrailsEngine score (0-100 → 0-1). Never let it break the hot path.
        with suppress(Exception):
            result = await self._resolve_guardrails().validate_input(content)
            if result.risk_score > 0:
                risk = max(risk, result.risk_score / 100.0)
                reasons.extend(result.reasons)

        heuristic_risk, heuristic_reasons = self._heuristic_score(content)
        if heuristic_risk > 0:
            risk = max(risk, heuristic_risk)
            reasons.extend(heuristic_reasons)

        if settings.hardening_injection_classifier and self._classifier_fn is not None:
            with suppress(Exception):
                classifier_risk = float(await self._classifier_fn(content))
                if classifier_risk > 0:
                    risk = max(risk, min(1.0, classifier_risk))
                    reasons.append("classifier")

        return min(1.0, risk), reasons

    @staticmethod
    def _heuristic_score(content: str) -> tuple[float, list[str]]:
        matched: list[str] = [
            f"indirect:{category}"
            for category, patterns in _HEURISTIC_PATTERNS.items()
            if any(p.search(content) for p in patterns)
        ]
        n = len(matched)
        if n == 0:
            return 0.0, []
        # 1 category → 0.75, 2 → 0.95, ≥3 → 1.0 (mirrors guardrails' escalation).
        return min(1.0, 0.55 + 0.20 * n), matched

    @staticmethod
    def _sanitize(content: str) -> str:
        """Neutralize matched injection spans so warn-mode content is defanged."""
        sanitized = content
        for patterns in _HEURISTIC_PATTERNS.values():
            for pattern in patterns:
                sanitized = pattern.sub("[neutralized-instruction]", sanitized)
        return sanitized

    @staticmethod
    def _normalize_vector(vector: str) -> Literal["direct", "tool", "rag", "media"]:
        if vector == "direct":
            return "direct"
        if vector == "rag" or vector == "web":
            return "rag"
        if vector == "media":
            return "media"
        return "tool"

    def _audit_and_count(self, vector: str, risk: float, reason: str, *, blocked: bool) -> None:
        from prismal.monitoring.otel import OTelManager

        with suppress(Exception):
            OTelManager().increment_counter("injection_detected", attributes={"vector": vector})
        logger.warning(
            "indirect_injection_detected",
            vector=vector,
            risk=round(risk, 3),
            blocked=blocked,
            reason=reason,
        )
        if self._audit is None:
            return
        with suppress(Exception):
            self._audit.log_event(
                "indirect_injection",
                {"vector": vector, "risk": round(risk, 3), "blocked": blocked, "reason": reason},
            )


__all__ = ["ClassifierFn", "IndirectInjectionDetector", "InjectionVerdict"]
