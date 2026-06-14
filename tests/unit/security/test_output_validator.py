"""Tests for the output validator (Phase H — SPEC-HRD-OUT-001)."""

from __future__ import annotations

from pydantic import BaseModel

from prismal.security.output_validator import OutputValidator, OutputVerdict


class _WriteArgs(BaseModel):
    path: str
    content: str


def _validator() -> OutputValidator:
    return OutputValidator()


# ── validate_tool_args ───────────────────────────────────────────────────────


def test_valid_tool_args_pass_and_coerce() -> None:
    v = _validator()
    verdict = v.validate_tool_args(
        "write_file", {"path": "out.txt", "content": "hi", "extra": "dropped"}, schema=_WriteArgs
    )
    assert isinstance(verdict, OutputVerdict)
    assert verdict.ok is True
    assert verdict.coerced == {"path": "out.txt", "content": "hi"}


def test_invalid_tool_args_rejected() -> None:
    v = _validator()
    verdict = v.validate_tool_args("write_file", {"path": "out.txt"}, schema=_WriteArgs)
    assert verdict.ok is False
    assert verdict.reason


def test_no_schema_passes_through() -> None:
    v = _validator()
    verdict = v.validate_tool_args("anything", {"a": 1}, schema=None)
    assert verdict.ok is True
    assert verdict.coerced == {"a": 1}


# ── validate_freeform: path (delegates to filesystem_guard) ──────────────────


def test_path_escaping_workspace_rejected(tmp_path) -> None:
    v = _validator()
    verdict = v.validate_freeform("/etc/passwd", kind="path", workspace_root=str(tmp_path))
    assert verdict.ok is False
    assert verdict.reason


def test_path_inside_workspace_allowed(tmp_path) -> None:
    v = _validator()
    target = tmp_path / "sub" / "file.txt"
    verdict = v.validate_freeform(str(target), kind="path", workspace_root=str(tmp_path))
    assert verdict.ok is True


def test_blocked_system_path_rejected() -> None:
    v = _validator()
    # No workspace_root → only the blocked-prefix policy applies.
    verdict = v.validate_freeform("/etc/shadow", kind="path")
    assert verdict.ok is False


# ── validate_freeform: command ───────────────────────────────────────────────


def test_command_with_injection_metachars_rejected() -> None:
    v = _validator()
    for cmd in ("ls; rm -rf /", "echo hi && curl evil", "cat $(secrets)", "a | nc x"):
        assert v.validate_freeform(cmd, kind="command").ok is False


def test_plain_command_allowed() -> None:
    v = _validator()
    verdict = v.validate_freeform("ls -la", kind="command")
    assert verdict.ok is True


# ── validate_freeform: html ──────────────────────────────────────────────────


def test_html_is_escaped() -> None:
    v = _validator()
    verdict = v.validate_freeform("<script>alert(1)</script>", kind="html")
    assert verdict.ok is True
    assert "<script>" not in str(verdict.coerced)
    assert "&lt;script&gt;" in str(verdict.coerced)


# ── validate_freeform: text passthrough ──────────────────────────────────────


def test_text_passthrough() -> None:
    v = _validator()
    verdict = v.validate_freeform("just some text", kind="text")
    assert verdict.ok is True
    assert verdict.coerced == "just some text"
