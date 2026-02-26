"""Unit tests for AuditLogger — L5 immutable append-only audit log."""
import json
from pathlib import Path

import pytest
from lightagent.security.audit import AuditLogger


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"

@pytest.fixture
def audit(audit_path: Path) -> AuditLogger:
    return AuditLogger(log_path=audit_path)

def _read_entries(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

def test_log_input_creates_file(audit: AuditLogger, audit_path: Path) -> None:
    audit.log_input("hello", risk_score=0, session_id="s1")
    assert audit_path.exists()

def test_log_input_entry_fields(audit: AuditLogger, audit_path: Path) -> None:
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
    audit.log_blocked("bad input", reasons=["injection:override_instructions"], session_id="s1")
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "blocked"
    assert entries[0]["reasons"] == ["injection:override_instructions"]

def test_log_tool_call_entry(audit: AuditLogger, audit_path: Path) -> None:
    audit.log_tool_call(name="web_search", params={"query": "hello"}, result="results...", duration_ms=123)
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "tool_call"
    assert entries[0]["tool_name"] == "web_search"
    assert entries[0]["duration_ms"] == 123

def test_log_llm_io_entry(audit: AuditLogger, audit_path: Path) -> None:
    audit.log_llm_io(
        input_text="user question", output_text="answer",
        model="claude-sonnet-4-5", tokens=150, cost_usd=0.001,
    )
    entries = _read_entries(audit_path)
    assert entries[0]["event"] == "llm_io"
    assert entries[0]["model"] == "claude-sonnet-4-5"
    assert entries[0]["tokens"] == 150

def test_log_permission_entry(audit: AuditLogger, audit_path: Path) -> None:
    audit.log_permission(
        permission_type="filesystem_write", resource="/tmp/file.txt",
        granted=True, reason="user approved",
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

def test_default_log_path_is_data_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    logger = AuditLogger()
    logger.log_input("test", risk_score=0, session_id="s1")
    assert (tmp_path / "data" / "logs" / "audit.jsonl").exists()