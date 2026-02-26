"""Guardrails engine — Security Layers L2 (injection detection) and output scanning.

Orchestrates L1 (InputSanitizer) + L2 (regex pattern matching from YAML).
Supports three enforcement modes: strict, permissive, audit-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import lightagent.core.config as _config_module
from lightagent.core.logging import get_logger
from lightagent.security.sanitizer import InputSanitizer

logger = get_logger("lightagent.security.guardrails")

_PATTERNS_FILE = Path(__file__).parent / "patterns" / "injection_patterns.yaml"
_RE_FLAGS = re.IGNORECASE | re.DOTALL | re.MULTILINE


@dataclass
class GuardrailResult:
    """Result from a guardrail validation pass.

    Attributes:
        safe: True if the text passed all checks.
        risk_score: Integer 0-100. 0 = clean, 100 = high risk.
        reasons: List of reason tags (e.g. 'injection:override_instructions').
        sanitized_text: The L1-sanitized version of the input.
    """

    safe: bool
    risk_score: int
    reasons: list[str] = field(default_factory=list)
    sanitized_text: str = ""


class GuardrailsEngine:
    """Orchestrates L1+L2 input validation and output scanning.

    L1: InputSanitizer (control chars, unicode, length).
    L2: Regex pattern matching from injection_patterns.yaml.
    Output: PII and API key pattern detection.

    Risk scoring (n = number of matched categories):
    - n=0 -> 0  (safe)
    - n=1 -> 70 (single injection category hit)
    - n=2 -> 90
    - n>=3 -> 100 (capped)

    Formula: ``min(100, 50 + 20 * n)`` when n >= 1.
    """

    def __init__(self) -> None:
        """Load and compile patterns from YAML at initialization."""
        self._sanitizer = InputSanitizer()
        self._input_patterns: dict[str, list[re.Pattern[str]]] = {}
        self._output_patterns: dict[str, dict[str, re.Pattern[str]]] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load injection and output patterns from YAML and compile to re.Pattern."""
        with _PATTERNS_FILE.open(encoding="utf-8") as f:
            data: dict[str, dict[str, object]] = yaml.safe_load(f)

        raw_patterns: dict[str, list[str]] = data.get("patterns") or {}  # type: ignore[assignment]
        for category, patterns in raw_patterns.items():
            self._input_patterns[category] = [
                re.compile(p, _RE_FLAGS) for p in patterns
            ]

        raw_output: dict[str, dict[str, str]] = data.get("output_patterns") or {}  # type: ignore[assignment]
        for group, group_patterns in raw_output.items():
            self._output_patterns[group] = {
                name: re.compile(p, _RE_FLAGS) for name, p in group_patterns.items()
            }

    @staticmethod
    def _compute_risk_score(matched_categories: list[str]) -> int:
        """Compute risk_score from the number of matched input categories.

        Args:
            matched_categories: Categories that had at least one pattern match.

        Returns:
            Integer 0-100. Formula: min(100, 50 + 20 * n) for n >= 1, else 0.
        """
        n = len(matched_categories)
        if n == 0:
            return 0
        return min(100, 50 + 20 * n)

    async def validate_input(self, text: str) -> GuardrailResult:
        """Apply L1 sanitization and L2 pattern matching to user input.

        Args:
            text: Raw user input text.

        Returns:
            GuardrailResult with safe flag, risk_score, reasons, and sanitized text.
        """
        sanitized = self._sanitizer.sanitize(text)
        settings = _config_module.get_settings()

        matched_categories: list[str] = [
            category
            for category, patterns in self._input_patterns.items()
            if any(p.search(sanitized) for p in patterns)
        ]
        reasons = [f"injection:{cat}" for cat in matched_categories]
        risk_score = self._compute_risk_score(matched_categories)

        mode = settings.security_mode
        if mode in ("audit-only", "permissive"):
            safe = True
        else:  # strict
            safe = risk_score < settings.risk_threshold

        if not safe:
            logger.warning(
                "input_blocked",
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
            )

        return GuardrailResult(
            safe=safe,
            risk_score=risk_score,
            reasons=reasons,
            sanitized_text=sanitized,
        )

    async def validate_output(
        self, text: str, canary: str | None = None
    ) -> GuardrailResult:
        """Scan LLM output for PII, API key leaks, and canary token leakage.

        Args:
            text: LLM output text to validate.
            canary: Optional canary string to check for leakage in output.

        Returns:
            GuardrailResult; safe=False if any pattern matched.
        """
        reasons: list[str] = []

        for group, patterns in self._output_patterns.items():
            for name, pattern in patterns.items():
                if pattern.search(text):
                    reasons.append(f"output:{group}:{name}")

        if canary and canary in text:
            reasons.append("output:canary_leak")

        risk_score = min(100, len(reasons) * 30)
        safe = len(reasons) == 0

        if _config_module.get_settings().security_mode == "audit-only":
            safe = True

        if not safe:
            logger.warning("output_flagged", reasons=reasons, risk_score=risk_score)

        return GuardrailResult(
            safe=safe,
            risk_score=risk_score,
            reasons=reasons,
            sanitized_text=text,
        )


__all__ = ["GuardrailResult", "GuardrailsEngine"]
