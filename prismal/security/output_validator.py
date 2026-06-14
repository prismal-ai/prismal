"""Output validator — improper-output-handling defense (Phase H — SPEC-HRD-OUT-001).

The model's output is consumed downstream — as tool arguments, a filesystem
path, a shell command, or rendered HTML. ``OutputValidator`` validates/escapes
that output **before** it is used, closing the OWASP LLM05 gap.

The verdict is mode-agnostic: it reports ``ok`` + a ``coerced`` value, and the
caller decides whether to skip the tool (``enforce``) or only audit (``warn``).
Pure and never raises on the hot path.
"""

from __future__ import annotations

import html
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from prismal.core.config import Settings

logger = get_logger("prismal.security.output_validator")

# Shell metacharacters that turn a single command into a chain / injection.
_COMMAND_INJECTION_RE = re.compile(r"[;&|`]|\$\(|\$\{|>>|<\(|\|\||&&")


@dataclass(frozen=True)
class OutputVerdict:
    """Outcome of validating one piece of model output."""

    ok: bool
    reason: str = ""
    coerced: Any | None = None  # validated/escaped value


class OutputValidator:
    """Validates structured tool args and escapes free-form output before use."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        """Initialize the validator (settings reserved for future tuning)."""
        self._settings = settings

    def validate_tool_args(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        schema: type[BaseModel] | None = None,
    ) -> OutputVerdict:
        """Validate structured tool arguments against a Pydantic *schema*.

        With no schema the args pass through unchanged. On a schema mismatch the
        verdict is ``ok=False`` (the caller skips the tool in ``enforce`` mode).
        """
        if schema is None:
            return OutputVerdict(ok=True, coerced=dict(args))
        try:
            model = schema.model_validate(dict(args))
        except Exception as exc:  # pydantic ValidationError (+ defensive catch-all)
            logger.warning("output_validation_failed", tool=tool_name, error=str(exc)[:200])
            return OutputVerdict(ok=False, reason=f"invalid args for {tool_name}: {exc}")
        return OutputVerdict(ok=True, coerced=model.model_dump())

    def validate_freeform(
        self,
        text: str,
        *,
        kind: Literal["path", "command", "html", "text"],
        workspace_root: str | None = None,
    ) -> OutputVerdict:
        """Escape/format-check free-form output before it is used.

        - ``path`` — delegated to :class:`FilesystemGuard` (blocked prefixes +
          optional workspace confinement).
        - ``command`` — rejected when it contains shell-chaining metacharacters.
        - ``html`` — HTML-escaped (returned as ``coerced``).
        - ``text`` — passthrough.
        """
        if kind == "path":
            return self._validate_path(text, workspace_root)
        if kind == "command":
            if _COMMAND_INJECTION_RE.search(text):
                return OutputVerdict(ok=False, reason="command contains shell metacharacters")
            return OutputVerdict(ok=True, coerced=text)
        if kind == "html":
            return OutputVerdict(ok=True, coerced=html.escape(text))
        return OutputVerdict(ok=True, coerced=text)

    # ── internals ──────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_path(text: str, workspace_root: str | None) -> OutputVerdict:
        from prismal.security.filesystem_guard import FilesystemGuard, PathViolation

        guard = FilesystemGuard(workspace_root=workspace_root)
        try:
            resolved = guard.validate(text, write=True)
        except PathViolation as exc:
            return OutputVerdict(ok=False, reason=str(exc))
        except Exception as exc:  # never raise on the hot path
            return OutputVerdict(ok=False, reason=f"invalid path: {exc}")
        return OutputVerdict(ok=True, coerced=str(resolved))


def validate_output_message(text: str, *, settings: Settings | None = None) -> str:
    """Apply optional PII redaction to a final agent OUTPUT message.

    Thin convenience used at the node seam; PII logic lives in ``pii_sanitizer``.
    """
    resolved = settings
    if resolved is None:
        with suppress(Exception):
            from prismal.core.config import get_settings

            resolved = get_settings()
    if resolved is None or not getattr(resolved, "hardening_pii_output", False):
        return text
    from prismal.security.pii_sanitizer import redact_output

    return redact_output(text, settings=resolved)


__all__ = ["OutputValidator", "OutputVerdict", "validate_output_message"]
