"""Unit tests: tool_registry phase threading + load_phase_capability_map (Phase LH — LH2-04/05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents.extension.providers import FakeToolProvider
from prismal.agents.tool_registry import (
    get_tools_for_agent,
    load_phase_capability_map,
    set_tool_provider,
)
from prismal.core.exceptions import ToolGatingConfigError


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_tool_provider(FakeToolProvider())


def test_get_tools_for_agent_no_phase_is_unchanged() -> None:
    provider = FakeToolProvider({"coder": []})
    set_tool_provider(provider)
    assert get_tools_for_agent("coder") == get_tools_for_agent("coder", phase=None)


def test_load_phase_capability_map_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_phase_capability_map(str(tmp_path / "nope.yaml")) == {}


def test_load_phase_capability_map_parses_valid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "phases.yaml"
    path.write_text(
        "coder:\n  planning:\n    - general\n    - file_management\n",
        encoding="utf-8",
    )
    result = load_phase_capability_map(str(path))
    assert result == {"coder": {"planning": ["general", "file_management"]}}


def test_load_phase_capability_map_malformed_raises() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("not_a_mapping: [1, 2, 3")  # malformed YAML
        path = f.name
    with pytest.raises(ToolGatingConfigError):
        load_phase_capability_map(path)
