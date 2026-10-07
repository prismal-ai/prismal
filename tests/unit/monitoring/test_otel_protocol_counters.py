"""Every PRT metric is emitted by production code (registration: test_config_protocol_upgrades; (SPEC-PRT-OTEL-001, PRT6-01)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3] / "prismal"

PRT_COUNTERS = (
    "a2a_requests",
    "a2a_oauth_tokens",
    "mcp_protocol_negotiations",
    "mcp_tool_cache_hits",
    "mcp_tool_cache_misses",
    "mcp_tool_list_changes",
    "mcp_tasks",
    "mcp_input_requests",
    "agui_events",
    "agui_translation_errors",
    "host_surface_errors",
)


def _production_sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in _ROOT.rglob("*.py")
        if p.name != "otel.py" and "__pycache__" not in p.parts
    )


@pytest.mark.parametrize("key", [*PRT_COUNTERS, "mcp_task_wait_s"])
def test_metric_is_emitted_somewhere(key: str) -> None:
    assert re.search(rf"[\"']{key}[\"']", _production_sources()), f"{key} is never emitted"
