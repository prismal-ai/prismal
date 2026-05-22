"""Audit logger — Security Layer L5: immutable append-only JSONL with hash chaining.

Each log entry includes a SHA-256 hash of the previous entry, creating a
tamper-evident chain. The first entry uses '0' * 64 as the genesis hash.

Verification protocol:
    To verify an entry's integrity, a reader must:
    1. Remove the ``entry_hash`` field from the parsed dict.
    2. Re-serialize the remaining dict with ``json.dumps(entry, sort_keys=True)``.
    3. Compute SHA-256 of the resulting UTF-8 bytes.
    4. Compare the digest to the stored ``entry_hash`` value.
    The on-disk JSON lines are written without sort_keys. Verification MUST
    re-serialize with sort_keys=True to reproduce the original hash input.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

logger = get_logger("lightagent.security.audit")

_DEFAULT_LOG_PATH = Path("data/logs/audit.jsonl")
_GENESIS_HASH = "0" * 64


class AuditLogger:
    """Append-only audit log with SHA-256 hash chaining.

    Thread-safe via an internal lock. Each write is flushed immediately.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        """Initialize the audit logger.

        Args:
            log_path: Path to the JSONL file. Defaults to data/logs/audit.jsonl.
                Parent directories are created automatically.
        """
        self._path = log_path or _DEFAULT_LOG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._prev_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        """Read the last entry_hash from the log file, or return genesis hash."""
        if not self._path.exists():
            return _GENESIS_HASH
        last_hash = _GENESIS_HASH
        try:
            with self._path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entry = json.loads(line)
                        last_hash = entry.get("entry_hash", _GENESIS_HASH)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "audit_log_load_error",
                path=str(self._path),
                error=str(exc),
            )
        return last_hash

    def _write(self, event: str, data: dict[str, object]) -> None:
        """Append a new entry to the JSONL file with hash chaining.

        Args:
            event: Event type string.
            data: Event-specific payload fields.
        """
        with self._lock:
            entry: dict[str, object] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "prev_hash": self._prev_hash,
                **data,
            }
            entry_bytes = json.dumps(entry, sort_keys=True).encode()
            entry["entry_hash"] = hashlib.sha256(entry_bytes).hexdigest()

            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()

            self._prev_hash = str(entry["entry_hash"])

    def log_input(self, text: str, *, risk_score: int, session_id: str) -> None:
        """Log a validated user input event.

        Args:
            text: The sanitized input text.
            risk_score: Computed risk score (0-100).
            session_id: Caller session identifier.
        """
        self._write(
            "input",
            {
                "session_id": session_id,
                "risk_score": risk_score,
                "text_length": len(text),
            },
        )

    def log_blocked(self, text: str, *, reasons: list[str], session_id: str) -> None:
        """Log a blocked input event.

        Emits an OTEL event with metadata only — never logs the actual text content.

        Args:
            text: The blocked input text.
            reasons: List of reason tags from GuardrailResult.
            session_id: Caller session identifier.
        """
        self._write(
            "blocked",
            {"session_id": session_id, "reasons": reasons, "text_length": len(text)},
        )
        otel = OTelManager()
        with otel.start_span("security.audit.blocked") as span:
            span.set_attribute("lightagent.session_id", session_id)
            span.set_attribute("lightagent.text_length", len(text))
            span.set_attribute("lightagent.reason_count", len(reasons))
            span.add_event(
                "security.input_blocked",
                attributes={
                    "session_id": session_id,
                    "text_length": len(text),
                    "reason_count": len(reasons),
                },
            )

    def log_tool_call(
        self,
        *,
        name: str,
        params: dict[str, object],
        result: str,
        duration_ms: int,
    ) -> None:
        """Log a tool call event.

        Args:
            name: Tool name.
            params: Tool input parameters.
            result: Tool output (truncated to 512 chars).
            duration_ms: Execution time in milliseconds.
        """
        self._write(
            "tool_call",
            {
                "tool_name": name,
                "params": params,
                "result_preview": result[:512],
                "duration_ms": duration_ms,
            },
        )

    def log_llm_io(
        self,
        *,
        input_text: str,
        output_text: str,
        model: str,
        tokens: int,
        cost_usd: float,
    ) -> None:
        """Log an LLM input/output event.

        Args:
            input_text: Prompt sent to the LLM.
            output_text: LLM response.
            model: Model identifier string.
            tokens: Total tokens used.
            cost_usd: Estimated cost in US dollars.
        """
        self._write(
            "llm_io",
            {
                "model": model,
                "tokens": tokens,
                "cost_usd": cost_usd,
                "input_length": len(input_text),
                "output_length": len(output_text),
            },
        )

    def log_permission(
        self,
        *,
        permission_type: str,
        resource: str,
        granted: bool,
        reason: str,
    ) -> None:
        """Log a permission decision event.

        Args:
            permission_type: Permission category string.
            resource: Resource identifier.
            granted: True if the permission was granted.
            reason: Human-readable justification.
        """
        self._write(
            "permission",
            {
                "permission_type": permission_type,
                "resource": resource,
                "granted": granted,
                "reason": reason,
            },
        )

    def log_nemo_event(
        self,
        *,
        direction: str,
        blocked: bool,
        category: str,
        text_length: int,
    ) -> None:
        """Log a NeMo Guardrails Layer 3 event.

        Records whether NeMo blocked input or output and which category triggered.

        Args:
            direction: ``"input"`` or ``"output"`` indicating the rail direction.
            blocked: True if NeMo blocked the content.
            category: Colang category tag (e.g. ``"violence"``). Empty when not blocked.
            text_length: Length of the checked text in characters.
        """
        self._write(
            "nemo_rail",
            {
                "direction": direction,
                "blocked": blocked,
                "category": category,
                "text_length": text_length,
            },
        )


__all__ = ["AuditLogger"]
