"""Host read surface fail-open contract (HRS — SPEC-HRS-SUR-002)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from prismal.composition import build_test_runtime
from prismal.composition import host_surface as hs
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


def _boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("internal failure")


async def _aboom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("internal failure")


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager.increment_counter", mock)
    return mock


CASES: list[tuple[str, Callable[[Any], Any], Any]] = [
    (
        "budget_status",
        lambda ctx: ctx.budget_status(thread_id="t"),
        hs.BudgetStatusView(
            enabled=False,
            scope="turn",
            status="unknown",
            breached_dimension=None,
            soft_ratio=None,
            hard_cap=None,
            limits=None,
            consumed=None,
            remaining=None,
        ),
    ),
    (
        "audit_page",
        lambda ctx: ctx.audit_page(page=2, page_size=500),
        hs.AuditPageView(page=2, page_size=200, total=None, entries=()),
    ),
    (
        "verify_audit_chain",
        lambda ctx: ctx.verify_audit_chain(),
        hs.AuditChainStatus(status="unverified", checked=0, first_broken_seq=None),
    ),
    (
        "decisions_report",
        lambda ctx: ctx.decisions_report(),
        hs.DecisionsReportView(enabled=False, integrations=()),
    ),
    (
        "routing_report",
        lambda ctx: ctx.routing_report(),
        hs.RoutingReportView(enabled=False, mode="shadow", policies=()),
    ),
]


@pytest.mark.parametrize(("method", "call", "fallback"), CASES, ids=[c[0] for c in CASES])
def test_sync_method_failure_returns_fallback_and_counts(
    monkeypatch: pytest.MonkeyPatch,
    counter: MagicMock,
    method: str,
    call: Callable[[Any], Any],
    fallback: Any,
) -> None:
    monkeypatch.setattr(hs, method, _boom)
    ctx = build_test_runtime(settings=Settings())
    assert call(ctx) == fallback
    counter.assert_called_once_with("host_surface_errors", attributes={"method": method})


async def test_quota_windows_failure_returns_empty_and_counts(
    monkeypatch: pytest.MonkeyPatch, counter: MagicMock
) -> None:
    monkeypatch.setattr(hs, "quota_windows", _aboom)
    assert await build_test_runtime(settings=Settings()).quota_windows() == []
    counter.assert_called_once_with("host_surface_errors", attributes={"method": "quota_windows"})


def test_settings_resolution_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch, counter: MagicMock
) -> None:
    monkeypatch.setattr("prismal.core.config.get_settings", _boom)
    ctx = build_test_runtime()  # no settings ⇒ falls back to the process settings
    assert ctx.budget_status().status == "unknown"
    assert ctx.decisions_report().enabled is False
    assert ctx.routing_report().enabled is False
    assert counter.call_count == 3


def test_without_settings_reads_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prismal.core.config.get_settings", lambda: Settings(adaptive_routing_enabled=True)
    )
    assert build_test_runtime().routing_report().enabled is True


def test_error_counter_failure_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hs, "verify_audit_chain", _boom)
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager.increment_counter", _boom)
    assert build_test_runtime().verify_audit_chain().status == "unverified"
