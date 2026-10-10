"""Hash-only decision audit records (Phase DEC — SPEC-DEC-AUD-001).

One ``decision.made`` record per question per decision via ``AuditPort.log_event``.
Never raw state text, option descriptions or secrets — only hashes, ids, numbers
(probabilities to 4 dp). Fail-open: an audit failure never breaks a decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.agents.extension.ports import AuditPort
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import (
        Decision,
        DecisionMode,
        Outcome,
        QuestionSpec,
        Reason,
        RiskTier,
    )

logger = get_logger(__name__)


@dataclass(frozen=True)
class DecisionAuditRecord:
    """Forensic record of one decision (no content)."""

    event: Literal["decision.made"]
    integration: str
    mode: DecisionMode
    question_id: str
    question_schema_sha256: str
    state_sha256: str
    option_order: tuple[str, ...]
    shuffle_order: tuple[str, ...] | None
    backend: str
    model: str
    calibration_version: str | None
    probabilities: Mapping[str, float]
    answer_confidence: float
    spread_confidence: float
    margin: float
    tau: float | None
    risk_tier: RiskTier
    outcome: Outcome
    reason: Reason
    escalated_from: tuple[str, ...]
    tainted: bool
    injection_flags: tuple[str, ...]
    language: str
    latency_ms: float
    shadow_reference: str | None
    shadow_agree: bool | None
    session_id: str | None
    run_id: str | None
    org_id: str | None
    agent_did: str | None
    timestamp: str

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe payload (enums as values, tuples as lists, 4 dp probabilities)."""
        data = asdict(self)
        data.pop("event")
        for key in ("mode", "risk_tier", "outcome"):
            data[key] = getattr(self, key).value
        data["probabilities"] = {k: round(v, 4) for k, v in self.probabilities.items()}
        for key in ("answer_confidence", "spread_confidence", "margin"):
            data[key] = round(data[key], 4)
        data["latency_ms"] = round(self.latency_ms, 3)
        for key in ("option_order", "shuffle_order", "escalated_from", "injection_flags"):
            if data[key] is not None:
                data[key] = list(data[key])
        return data


def build_audit_record(
    *,
    decision: Decision,
    question: QuestionSpec,
    state: DecisionState,
    integration: str,
    mode: DecisionMode,
    tau: float | None,
    shuffle_order: tuple[str, ...] | None = None,
    shadow_reference: str | None = None,
    shadow_agree: bool | None = None,
    context: Mapping[str, str | None] | None = None,
) -> DecisionAuditRecord:
    ctx = context or {}
    return DecisionAuditRecord(
        event="decision.made",
        integration=integration,
        mode=mode,
        question_id=question.id,
        question_schema_sha256=question.schema_hash(),
        state_sha256=state.sha256,
        option_order=question.labels(),
        shuffle_order=shuffle_order,
        backend=decision.backend,
        model=decision.model,
        calibration_version=decision.calibration_version,
        probabilities=dict(decision.probabilities),
        answer_confidence=decision.answer_confidence,
        spread_confidence=decision.spread_confidence,
        margin=decision.margin,
        tau=tau,
        risk_tier=question.risk_tier,
        outcome=decision.outcome,
        reason=decision.reason,
        escalated_from=decision.escalated_from,
        tainted=state.tainted,
        injection_flags=state.injection_flags,
        language=state.language,
        latency_ms=decision.latency_ms,
        shadow_reference=shadow_reference,
        shadow_agree=shadow_agree,
        session_id=ctx.get("session_id"),
        run_id=ctx.get("run_id"),
        org_id=ctx.get("org_id"),
        agent_did=ctx.get("agent_did"),
        timestamp=datetime.now(UTC).isoformat(),
    )


def audit_decision(audit: AuditPort | Any, record: DecisionAuditRecord) -> None:
    """Log ``record`` via ``audit.log_event``; never raises."""
    try:
        audit.log_event(record.event, record.to_payload())
    except Exception as exc:
        logger.warning("decision_audit_failed", error_type=type(exc).__name__)
