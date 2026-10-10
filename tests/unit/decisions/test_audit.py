"""Hash-only decision audit (Phase DEC — SPEC-DEC-AUD-001, DEC3-05)."""

from __future__ import annotations

from typing import Any

from prismal.decisions.audit import audit_decision, build_audit_record
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    DecisionMode,
    OptionSpec,
    QuestionSpec,
    RiskTier,
)

Q = QuestionSpec(
    id="tool.proposal",
    kind=DecisionKind.CHOICE,
    instructions="Pick the tool for the confidential merger memo.",
    options=(OptionSpec("search", "Search the secret archive"), OptionSpec("none", "Nothing")),
    risk_tier=RiskTier.HIGH,
)


def _record(**kw: Any) -> Any:
    state = (
        DecisionStateBuilder()
        .add_untrusted("user", "my password is hunter2", source="user")
        .build()
    )
    d = Decision.from_probabilities(
        Q, {"search": 0.123456789, "none": 0.876543211}, backend="laya", model="laya-m"
    )
    return build_audit_record(
        decision=d,
        question=Q,
        state=state,
        integration="tool",
        mode=DecisionMode.ENFORCE,
        tau=0.97,
        shuffle_order=("none", "search"),
        context={"session_id": "s", "run_id": "r", "org_id": "o", "agent_did": "did:web:x"},
        **kw,
    )


def test_payload_is_hash_only_and_rounded() -> None:
    payload = _record().to_payload()
    blob = str(payload)
    for secret in ("hunter2", "merger", "secret archive"):
        assert secret not in blob
    assert payload["probabilities"] == {"search": 0.1235, "none": 0.8765}
    assert payload["option_order"] == ["search", "none"]
    assert payload["shuffle_order"] == ["none", "search"]
    assert payload["risk_tier"] == "high"
    assert payload["mode"] == "enforce"
    assert payload["tau"] == 0.97
    assert payload["agent_did"] == "did:web:x"
    assert len(payload["state_sha256"]) == 64
    assert "event" not in payload


def test_shadow_fields() -> None:
    payload = _record(shadow_reference="none", shadow_agree=True).to_payload()
    assert payload["shadow_reference"] == "none"
    assert payload["shadow_agree"] is True


def test_audit_decision_logs_and_fails_open() -> None:
    events: list[Any] = []

    class Audit:
        def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, payload))

    rec = _record()
    audit_decision(Audit(), rec)
    assert events[0][0] == "decision.made"

    class Broken:
        def log_event(self, *_: Any) -> None:
            raise OSError("disk full")

    audit_decision(Broken(), rec)  # must not raise
