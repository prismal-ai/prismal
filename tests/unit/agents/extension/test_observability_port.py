"""Conformance tests for ObservabilityPort (OBS2-01 — SPEC-OBS-PRT-001)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from prismal.agents.extension.ports import ObservabilityPort, conforms_to
from prismal.budget.types import Usage
from prismal.monitoring.observability_types import (
    DatasetFormat,
    RunSummary,
    ToolCallRecord,
)


class _ConformingStub:
    def record_node(
        self,
        *,
        run_id: str,
        node_name: str,
        session_id: str,
        status: Literal["ok", "error"],
        duration_ms: float,
        tool_calls: Sequence[ToolCallRecord] = (),
        usage: Usage | None = None,
    ) -> None:
        pass

    def record_score(
        self,
        *,
        run_id: str,
        name: str,
        value: float,
        comment: str = "",
        source: Literal["human", "llm_judge", "system"] = "system",
    ) -> None:
        pass

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        return None

    def export_dataset(
        self, run_ids: Sequence[str], *, fmt: DatasetFormat
    ) -> list[dict[str, Any]]:
        return []


class _MissingMethod:
    def record_node(self, **kwargs: Any) -> None:
        pass


def test_runtime_checkable() -> None:
    assert ObservabilityPort.__module__.endswith("ports")


def test_conforming_stub_passes() -> None:
    assert conforms_to(_ConformingStub(), ObservabilityPort) is True


def test_incomplete_object_fails() -> None:
    assert conforms_to(_MissingMethod(), ObservabilityPort) is False


def test_exported_from_extension_package() -> None:
    from prismal.agents.extension import ObservabilityPort as Exported

    assert Exported is ObservabilityPort
