"""Unit tests for AuditLogger — L5 immutable append-only audit log."""

import json
from pathlib import Path

import pytest

from lightagent.security.audit import AuditLogger


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    """Return a temporary path for the audit JSONL file."""
    return tmp_path / "audit.jsonl"


@pytest.fixture
def audit(audit_path: Path) -> AuditLogger:
    """Return an AuditLogger writing to a temporary file."""
    return AuditLogger(log_path=audit_path)


def _read_entries(path: Path) -> list[dict[str, object]]:
    """Parse all non-empty JSONL lines from the given path."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_log_input_creates_file(audit: AuditLogger, audit_path: Path) -> None:
    """Logging an input event must create the JSONL file on disk."""
    audit.log_input("hello", risk_score=0, session_id="s1")
    assert audit_path.exists()


def test_log_input_entry_fields(audit: AuditLogger, audit_path: Path) -> None:
    """A logged input entry must contain all required fields."""
    audit.log_input("test input", risk_score=15, session_id="sess-123")
    entries = _read_entries(audit_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "input"
    assert entry["session_id"] == "sess-123"
    assert entry["risk_score"] == 15
    assert "timestamp" in entry
    assert "entry_hash" in entry
    assert "prev_hash" in entry


def test_log_input_genesis_prev_hash(audit: AuditLogger, audit_path: Path) -> None:
    """First entry must have prev_hash of 64 zeros."""
    audit.log_input("first", risk_score=0, session_id="s1")
    entries = _read_entries(audit_path)
    assert entries[0]["prev_hash"] == "0" * 64


def test_log_blocked_entry(audit: AuditLogger, audit_path: Path) -> None:
    """A blocked event must record the event type and reasons list."""
    audit.log_blocked(
        "bad input", reasons=["injection:override_instructions"], session_id="s1"
    )
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "blocked"
    assert entries[0]["reasons"] == ["injection:override_instructions"]


def test_log_tool_call_entry(audit: AuditLogger, audit_path: Path) -> None:
    """A tool_call event must record the tool name and duration."""
    audit.log_tool_call(
        name="web_search",
        params={"query": "hello"},
        result="results...",
        duration_ms=123,
    )
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "tool_call"
    assert entries[0]["tool_name"] == "web_search"
    assert entries[0]["duration_ms"] == 123


def test_log_llm_io_entry(audit: AuditLogger, audit_path: Path) -> None:
    """An llm_io event must record the model name and token count."""
    audit.log_llm_io(
        input_text="user question",
        output_text="answer",
        model="claude-sonnet-4-5",
        tokens=150,
        cost_usd=0.001,
    )
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "llm_io"
    assert entries[0]["model"] == "claude-sonnet-4-5"
    assert entries[0]["tokens"] == 150


def test_log_permission_entry(audit: AuditLogger, audit_path: Path) -> None:
    """A permission event must record the granted flag."""
    audit.log_permission(
        permission_type="filesystem_write",
        resource="/tmp/file.txt",  # noqa: S108
        granted=True,
        reason="user approved",
    )
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "permission"
    assert entries[0]["granted"] is True


def test_hash_chain_links_entries(audit: AuditLogger, audit_path: Path) -> None:
    """Each entry's prev_hash must equal the previous entry's entry_hash."""
    audit.log_input("a", risk_score=0, session_id="s1")
    audit.log_input("b", risk_score=0, session_id="s1")
    audit.log_input("c", risk_score=0, session_id="s1")
    entries = _read_entries(audit_path)
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    assert entries[2]["prev_hash"] == entries[1]["entry_hash"]


def test_default_log_path_is_data_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default log path must resolve to data/logs/audit.jsonl relative to cwd."""
    monkeypatch.chdir(tmp_path)
    audit_logger = AuditLogger()
    audit_logger.log_input("test", risk_score=0, session_id="s1")
    assert (tmp_path / "data" / "logs" / "audit.jsonl").exists()


def test_corrupted_log_warns_and_falls_back_to_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupted log file must log a warning and fall back to genesis hash."""
    import lightagent.security.audit as audit_module

    warnings_emitted: list[str] = []
    original_logger = audit_module.logger

    class _FakeLogger:
        def warning(self, event: str, **kw: object) -> None:
            warnings_emitted.append(event)

    monkeypatch.setattr(audit_module, "logger", _FakeLogger())
    bad_path = tmp_path / "bad.jsonl"
    bad_path.write_text("not valid json\n", encoding="utf-8")
    audit_logger = AuditLogger(log_path=bad_path)
    monkeypatch.setattr(audit_module, "logger", original_logger)
    assert "audit_log_load_error" in warnings_emitted
    audit_logger.log_input("after corruption", risk_score=0, session_id="s1")
    # Skip the original corrupt line; read only valid JSON lines.
    valid_entries = [
        json.loads(line)
        for line in bad_path.read_text().splitlines()
        if line.strip() and line.strip() != "not valid json"
    ]
    assert valid_entries[0]["prev_hash"] == "0" * 64


def test_log_nemo_event_fields(audit: AuditLogger, audit_path: Path) -> None:
    """log_nemo_event() writes a nemo_rail entry with all required fields."""
    audit.log_nemo_event(
        direction="input",
        blocked=True,
        category="violence",
        text_length=42,
    )
    entries = _read_entries(audit_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "nemo_rail"
    assert entry["direction"] == "input"
    assert entry["blocked"] is True
    assert entry["category"] == "violence"
    assert entry["text_length"] == 42
    assert "entry_hash" in entry
    assert "prev_hash" in entry


def test_log_nemo_event_not_blocked(audit: AuditLogger, audit_path: Path) -> None:
    """log_nemo_event() records non-blocked events correctly."""
    audit.log_nemo_event(
        direction="output",
        blocked=False,
        category="",
        text_length=100,
    )
    entries = _read_entries(audit_path)
    assert entries[0]["blocked"] is False
    assert entries[0]["category"] == ""


def test_audit_resumes_hash_chain_from_existing_file(
    tmp_path: Path,
) -> None:
    """A new AuditLogger instance continues the hash chain from an existing file."""
    path = tmp_path / "chain_resume.jsonl"

    # Write first entry with logger #1
    logger1 = AuditLogger(log_path=path)
    logger1.log_input("first entry", risk_score=0, session_id="s1")

    # Read the written hash
    entries = _read_entries(path)
    first_hash = entries[0]["entry_hash"]

    # Create logger #2 — _load_last_hash() must read the existing hash
    logger2 = AuditLogger(log_path=path)
    logger2.log_input("second entry", risk_score=0, session_id="s1")

    entries_after = _read_entries(path)
    assert len(entries_after) == 2
    # The second entry's prev_hash must link to the first entry's hash
    assert entries_after[1]["prev_hash"] == first_hash
