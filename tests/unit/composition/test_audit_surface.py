"""``RuntimeContext.audit_page`` / ``verify_audit_chain`` (HRS — SPEC-HRS-AUD-001/002)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from prismal.composition import build_test_runtime
from prismal.composition.host_surface import AuditChainStatus, AuditEntryView, AuditPageView
from prismal.core.config import Settings
from prismal.security.audit import AuditLogger

pytestmark = pytest.mark.unit


def _ctx(audit: Any, org_id: str | None = None) -> Any:
    ctx = build_test_runtime(settings=Settings(), org_id=org_id)
    ctx.audit = audit
    return ctx


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def audit(log_path: Path) -> AuditLogger:
    logger = AuditLogger(log_path)
    for i in range(5):
        logger.log_event("evt", {"org_id": "acme", "n": i, "actor": "agent-a"})
    logger.log_event("evt", {"org_id": "beta", "n": 99})
    logger.log_event("global_evt", {"n": 100})  # no org_id
    return logger


# ── audit_page ────────────────────────────────────────────────────────────────


def test_page_is_newest_first_with_total(audit: AuditLogger) -> None:
    page = _ctx(audit).audit_page(page=1, page_size=3)
    assert isinstance(page, AuditPageView)
    assert (page.page, page.page_size, page.total) == (1, 3, 7)
    assert [e.seq for e in page.entries] == [7, 6, 5]
    first = page.entries[0]
    assert isinstance(first, AuditEntryView)
    assert first.event == "global_evt" and first.org_id is None
    assert len(first.hash) == 64 and len(first.prev_hash) == 64
    assert first.ts is not None and first.ts.tzinfo is not None


def test_second_page_and_past_the_end(audit: AuditLogger) -> None:
    ctx = _ctx(audit)
    assert [e.seq for e in ctx.audit_page(page=3, page_size=3).entries] == [1]
    assert ctx.audit_page(page=9, page_size=3).entries == ()


def test_page_size_capped_at_200_and_page_at_least_one(audit: AuditLogger) -> None:
    page = _ctx(audit).audit_page(page=0, page_size=10_000)
    assert (page.page, page.page_size) == (1, 200)


def test_tenant_filter(audit: AuditLogger) -> None:
    acme = _ctx(audit, org_id="acme").audit_page()
    assert acme.total == 5
    assert {e.org_id for e in acme.entries} == {"acme"}
    assert acme.entries[0].actor == "agent-a"
    # entries without org_id are only visible to an org_id=None context
    beta = _ctx(audit, org_id="beta").audit_page()
    assert [e.event for e in beta.entries] == ["evt"]


def test_since_filter(audit: AuditLogger) -> None:
    ctx = _ctx(audit)
    assert ctx.audit_page(since=datetime.now(UTC) + timedelta(hours=1)).total == 0
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    assert ctx.audit_page(since=naive_past).total == 7


CANARY = "CANARY-7f3a-secret-user-content"


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 23


def test_only_allowlisted_scalars_pass_everything_else_is_hashed(log_path: Path) -> None:
    logger = AuditLogger(log_path)
    logger.log_tool_call(name="shell", params={"cmd": CANARY, "k": 1}, result=CANARY, duration_ms=3)
    logger.log_event(
        "x",
        {
            "text": "user said hi",
            "prompt": "p",
            "text_length": 12,
            "status": "ok",
            "agent": "coder",
            "goal_hash": "abc123",
            "blob": "z" * 300,
            "nested": {"preview": "p", "ok": 1, "items": ["y", 2]},
            "reasons": ["injection"],
        },
    )
    entries = _ctx(logger).audit_page().entries
    x, tool = entries[0].fields, entries[1].fields
    # allowlisted scalar fields pass as-is
    assert tool["tool_name"] == "shell" and tool["duration_ms"] == 3
    assert x["text_length"] == 12 and x["status"] == "ok" and x["agent"] == "coder"
    assert x["goal_hash"] == "abc123"
    # every other value — strings, nested dicts, lists — is reduced to a digest
    for key in ("params", "result_preview"):
        assert _is_digest(tool[key])
    for key in ("text", "prompt", "blob", "nested", "reasons"):
        assert _is_digest(x[key])
    assert "timestamp" not in x and "entry_hash" not in x


@pytest.mark.parametrize("key", ["params", "args", "messages", "inputs", "metadata", "error"])
def test_canary_under_any_non_allowlisted_key_never_appears(log_path: Path, key: str) -> None:
    logger = AuditLogger(log_path)
    logger.log_event("evt", {key: CANARY})
    logger.log_event("evt", {key: {"q": CANARY, "n": [CANARY, {"deep": CANARY}]}})
    logger.log_event("evt", {key: [CANARY]})
    logger.log_tool_call(name="t", params={key: CANARY}, result="r", duration_ms=1)
    page = _ctx(logger).audit_page()
    assert page.total == 4
    assert CANARY not in repr(page)


def test_allowlisted_key_holding_a_container_or_long_string_is_hashed(log_path: Path) -> None:
    logger = AuditLogger(log_path)
    logger.log_event("evt", {"status": {"leak": CANARY}, "agent": [CANARY], "tool": "t" * 300})
    fields = _ctx(logger).audit_page().entries[0].fields
    assert _is_digest(fields["status"]) and _is_digest(fields["agent"])
    assert _is_digest(fields["tool"])
    assert CANARY not in repr(fields)


# ── bounded scan (HRS review finding 8) ───────────────────────────────────────


def test_scan_cap_bounds_parsing_and_reports_unknown_total(
    audit: AuditLogger, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismal.composition import host_surface as hs

    parsed: list[str] = []
    real_loads = hs.json.loads

    def counting_loads(raw: str, *a: Any, **kw: Any) -> Any:
        parsed.append(raw)
        return real_loads(raw, *a, **kw)

    monkeypatch.setattr(hs.json, "loads", counting_loads)
    page = hs.audit_page(audit, org_id=None, page_size=10, max_records=3)
    assert len(parsed) == 3  # only the newest max_records lines are parsed
    # truncated ⇒ total unknown; seq numbering stays absolute (newest = 7)
    assert page.total is None
    assert [e.seq for e in page.entries] == [7, 6, 5]
    assert hs.audit_page(audit, org_id=None, page=2, page_size=3, max_records=3).entries == ()


def test_scan_cap_not_hit_keeps_exact_total(audit: AuditLogger) -> None:
    from prismal.composition import host_surface as hs

    page = hs.audit_page(audit, org_id=None, max_records=7)
    assert page.total == 7


def test_runtime_audit_page_uses_the_default_scan_cap(
    audit: AuditLogger, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismal.composition import host_surface as hs

    assert hs.AUDIT_SCAN_MAX_RECORDS == 100_000
    monkeypatch.setattr(hs, "AUDIT_SCAN_MAX_RECORDS", 2)
    page = _ctx(audit).audit_page()
    assert page.total is None and [e.seq for e in page.entries] == [7, 6]


def test_unparseable_line_is_skipped_in_page(audit: AuditLogger, log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write("not json\n\n")
    page = _ctx(audit).audit_page()
    assert page.total == 7


def test_non_logger_audit_gives_empty_page() -> None:
    page = build_test_runtime(settings=Settings()).audit_page()
    assert (page.total, page.entries) == (None, ())


def test_missing_file_gives_empty_page(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    assert _ctx(logger).audit_page().total == 0


# ── verify_audit_chain ────────────────────────────────────────────────────────


def test_valid_chain(audit: AuditLogger) -> None:
    assert _ctx(audit).verify_audit_chain() == AuditChainStatus("valid", 7, None)


def test_empty_log_is_valid(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    assert _ctx(logger).verify_audit_chain() == AuditChainStatus("valid", 0, None)


def test_tampered_record_is_broken_at_its_seq(audit: AuditLogger, log_path: Path) -> None:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[2])
    entry["n"] = 12345  # tamper with record seq=3
    lines[2] = json.dumps(entry)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = _ctx(audit).verify_audit_chain()
    assert (status.status, status.first_broken_seq) == ("broken", 3)
    assert status.checked == 3


def test_broken_prev_link_detected(audit: AuditLogger, log_path: Path) -> None:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # removing a record breaks the next prev_hash link
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = _ctx(audit).verify_audit_chain()
    assert (status.status, status.first_broken_seq) == ("broken", 2)


def test_garbage_line_is_broken(audit: AuditLogger, log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write("{oops\n")
    status = _ctx(audit).verify_audit_chain()
    assert (status.status, status.first_broken_seq) == ("broken", 8)


def test_oversize_log_is_unverified(audit: AuditLogger) -> None:
    status = _ctx(audit).verify_audit_chain(max_records=3)
    assert status == AuditChainStatus("unverified", 0, None)


def test_non_logger_audit_is_unverified() -> None:
    status = build_test_runtime(settings=Settings()).verify_audit_chain()
    assert status == AuditChainStatus("unverified", 0, None)


def test_unreadable_log_is_unverified(audit: AuditLogger, log_path: Path) -> None:
    log_path.unlink()
    log_path.mkdir()  # a directory where the file should be → unreadable
    assert _ctx(audit).verify_audit_chain().status == "unverified"
