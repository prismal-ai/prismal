"""Official protocol conformance suites (ADR-PRT-001 §2.4, PRT6-06; ``interop`` marker).

* AG-UI — validate every golden transcript event against the official 1.0 JSON schema
  (``PRISMAL_INTEROP_AGUI_SCHEMA`` = path to ``spec/1.0/schema.json``).
* A2A — run ``a2aproject/a2a-tck`` against a running SUT
  (``PRISMAL_INTEROP_A2A_TCK`` = TCK checkout, ``PRISMAL_INTEROP_A2A_SUT`` = SUT URL).
* MCP — run ``@modelcontextprotocol/conformance`` (``npx``) against a server URL
  (``PRISMAL_INTEROP_MCP_SERVER``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - opt-in conformance runners, fixed argv
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.interop

_AGUI_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protocols" / "agui"


def _env_path(name: str) -> Path:
    raw = os.environ.get(name, "")
    if not raw or not Path(raw).exists():
        pytest.skip(f"{name} not set or missing")
    return Path(raw)


def test_agui_golden_transcripts_match_official_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema: dict[str, Any] = json.loads(
        _env_path("PRISMAL_INTEROP_AGUI_SCHEMA").read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    checked = 0
    for path in sorted(_AGUI_DIR.glob("*.json")):
        transcript = json.loads(path.read_text(encoding="utf-8"))
        for event in transcript.get("expected", []):
            errors = list(validator.iter_errors(event))
            assert not errors, f"{path.name}: {event.get('type')}: {errors[0].message}"
            checked += 1
    assert checked > 0


def test_a2a_tck_against_running_sut() -> None:
    tck = _env_path("PRISMAL_INTEROP_A2A_TCK")
    sut = os.environ.get("PRISMAL_INTEROP_A2A_SUT", "")
    if not sut:
        pytest.skip("PRISMAL_INTEROP_A2A_SUT not set")
    proc = subprocess.run(  # nosec B603 - fixed argv, opt-in
        [sys.executable, "run_tck.py", "--sut-url", sut, "--category", "mandatory"],
        cwd=tck,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


def test_mcp_conformance_against_server() -> None:
    server = os.environ.get("PRISMAL_INTEROP_MCP_SERVER", "")
    npx = shutil.which("npx")
    if not server or npx is None:
        pytest.skip("PRISMAL_INTEROP_MCP_SERVER not set or npx unavailable")
    proc = subprocess.run(  # nosec B603 - fixed argv, opt-in
        [npx, "--yes", "@modelcontextprotocol/conformance", "server", "--url", server],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
