"""Secure prompt builder — wraps user input in tagged sections with a canary token.

Never concatenates user input directly into the system prompt. All user text
is isolated in <user_input> tags and sanitized by InputSanitizer before use.

Two layouts (Phase TKE — SPEC-TKE-LAY-001):

- ``LEGACY`` (default): the per-request canary lives in the system prompt.
- ``CACHE_STABLE`` (opt-in): the system prompt carries only static instructions
  plus a tenant-scoped HMAC *prefix canary* (stable within a TTL bucket, so the
  provider prompt cache can hit), and the per-request canary moves to a
  ``<turn_context>`` block at the tail of the user turn. Both canaries are
  checked by :meth:`SecurePromptBuilder.detect_leak`. Any failure building the
  cache-stable layout falls back to ``LEGACY`` — the canary is never dropped.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger
from prismal.security.sanitizer import InputSanitizer

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from prismal.core.config import Settings

logger = get_logger(__name__)

# Rotation bucket for the prefix canary. Must be much longer than the provider
# cache TTL (5 min / 1 h) so rotation rarely invalidates a warm cache.
PREFIX_CANARY_TTL_S = 86_400

# Used when no ``prompt_canary_secret`` is configured: unpredictable across
# processes, stable within one process (so the prefix still caches).
_PROCESS_CANARY_SECRET = secrets.token_bytes(32)


class PromptLayout(StrEnum):
    """Where the per-request canary is placed."""

    LEGACY = "legacy"
    CACHE_STABLE = "cache_stable"


@dataclass(frozen=True)
class TurnContext:
    """Per-request values rendered at the tail of the user turn (cache-stable layout)."""

    canary: str
    request_id: str
    now_iso: str
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LeakVerdict:
    """Result of :meth:`SecurePromptBuilder.detect_leak`."""

    leaked: bool
    which: tuple[str, ...] = ()  # subset of ("request", "prefix")


class SecurePromptBuilder:
    """Builds safe ChatML-style message lists for LLM calls.

    Features:
    - Sanitizes all user input via InputSanitizer.
    - Wraps user input in ``<user_input>...</user_input>`` tags.
    - Wraps documents in ``<documents><document>...</document></documents>`` tags.
    - Embeds a per-call UUID canary (detects prompt leakage) — in the system
      prompt (``LEGACY``) or in a tail ``<turn_context>`` block next to a stable
      prefix canary (``CACHE_STABLE``).

    Usage::

        builder = SecurePromptBuilder()
        messages = builder.build(
            system="You are a helpful assistant.",
            user="What is the capital of France?",
            docs=["France is a country in Western Europe..."],
        )
        canaries = builder.canaries  # pass to GuardrailsEngine.validate_output()
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize with a fresh InputSanitizer.

        Args:
            settings: Resolves the default layout and the prefix-canary secret;
                ``None`` reads :func:`prismal.core.config.get_settings` lazily.
            clock: Wall clock for the prefix-canary TTL bucket (injectable).
        """
        self._sanitizer = InputSanitizer()
        self._settings = settings
        self._clock = clock
        self.canary: str = ""
        self.layout_used: PromptLayout = PromptLayout.LEGACY
        self._prefix_canary: str = ""

    def build(
        self,
        system: str,
        user: str,
        docs: list[str] | None = None,
        *,
        layout: PromptLayout | None = None,
        turn_context: TurnContext | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, str]]:
        """Build a safe message list for an LLM call.

        Args:
            system: System prompt text (trusted code — not sanitized).
            user: Raw user input (automatically sanitized).
            docs: Optional list of document strings to include as context.
                Treated as trusted content — NOT sanitized. Callers must ensure
                document content has been validated before passing here.
            layout: Canary placement; ``None`` resolves from settings
                (``CACHE_STABLE`` only when ``token_efficiency_enabled`` and
                ``prompt_layout == "cache_stable"``).
            turn_context: Explicit per-request values for ``CACHE_STABLE``;
                generated when omitted.
            tenant: Scopes the prefix canary (``CACHE_STABLE`` only).

        Returns:
            List of two dicts: system message followed by user message.
        """
        resolved = layout if layout is not None else self._default_layout()
        sanitized_user = self._sanitizer.sanitize(user)
        user_parts: list[str] = [f"<user_input>{sanitized_user}</user_input>"]
        if docs:
            doc_tags = "".join(f"<document>{d}</document>" for d in docs)
            user_parts.append(f"<documents>{doc_tags}</documents>")

        if resolved is PromptLayout.CACHE_STABLE:
            try:
                return self._build_cache_stable(system, user_parts, turn_context, tenant)
            except Exception as exc:
                logger.warning("prompt_layout_fallback", error=type(exc).__name__)
                with suppress(Exception):
                    from prismal.monitoring.otel import OTelManager

                    OTelManager().increment_counter("prompt_cache_layout_fallbacks")
        return self._build_legacy(system, user_parts)

    def prefix_canary(self, tenant: str | None) -> str:
        """Return the HMAC prefix canary for *tenant* in the current TTL bucket."""
        bucket = int(self._clock() // PREFIX_CANARY_TTL_S)
        message = f"{tenant or ''}:{bucket}".encode()
        digest = hmac.new(self._canary_secret(), message, hashlib.sha256).hexdigest()
        return f"pc-{digest[:32]}"

    @property
    def canaries(self) -> tuple[str, ...]:
        """Every canary embedded by the last :meth:`build` (request first)."""
        if self.layout_used is PromptLayout.CACHE_STABLE and self._prefix_canary:
            return (self.canary, self._prefix_canary)
        return (self.canary,)

    def detect_leak(self, output: str) -> LeakVerdict:
        """Report whether *output* contains the request and/or prefix canary."""
        which: list[str] = []
        if self.canary and self.canary in output:
            which.append("request")
        if (
            self.layout_used is PromptLayout.CACHE_STABLE
            and self._prefix_canary
            and self._prefix_canary in output
        ):
            which.append("prefix")
        return LeakVerdict(leaked=bool(which), which=tuple(which))

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_legacy(self, system: str, user_parts: list[str]) -> list[dict[str, str]]:
        canary = str(uuid.uuid4())
        self.canary = canary
        self._prefix_canary = ""
        self.layout_used = PromptLayout.LEGACY
        return [
            {"role": "system", "content": f"{system}\n\n<!-- canary:{canary} -->"},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    def _build_cache_stable(
        self,
        system: str,
        user_parts: list[str],
        turn_context: TurnContext | None,
        tenant: str | None,
    ) -> list[dict[str, str]]:
        prefix = self.prefix_canary(tenant)
        ctx = turn_context or TurnContext(
            canary=str(uuid.uuid4()),
            request_id=uuid.uuid4().hex,
            now_iso=datetime.now(UTC).isoformat(),
        )
        fields = [f"canary:{ctx.canary}", f"request_id:{ctx.request_id}", f"now:{ctx.now_iso}"]
        fields.extend(f"{key}:{value}" for key, value in sorted(ctx.extra.items()))
        tail = "<turn_context>" + " ".join(fields) + "</turn_context>"
        self.canary = ctx.canary
        self._prefix_canary = prefix
        self.layout_used = PromptLayout.CACHE_STABLE
        return [
            {"role": "system", "content": f"{system}\n\n<!-- canary:{prefix} -->"},
            {"role": "user", "content": "\n".join([*user_parts, tail])},
        ]

    def _resolved_settings(self) -> Settings:
        if self._settings is None:
            from prismal.core.config import get_settings

            self._settings = get_settings()
        return self._settings

    def _default_layout(self) -> PromptLayout:
        try:
            settings = self._resolved_settings()
        except Exception:  # settings unavailable ⇒ pre-TKE behaviour
            return PromptLayout.LEGACY
        if settings.token_efficiency_enabled and settings.prompt_layout == "cache_stable":
            return PromptLayout.CACHE_STABLE
        return PromptLayout.LEGACY

    def _canary_secret(self) -> bytes:
        secret = self._resolved_settings().prompt_canary_secret.get_secret_value()
        return secret.encode() if secret else _PROCESS_CANARY_SECRET


__all__ = ["LeakVerdict", "PromptLayout", "SecurePromptBuilder", "TurnContext"]
