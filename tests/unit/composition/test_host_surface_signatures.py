"""Host read surface stability snapshot (HRS — SPEC-HRS-SUR-001).

Any change to these symbols or signatures is a coordinated minor in engine +
``prismal-server``: update this snapshot only together with the host guard.
``a2a_routes`` (HRS1-08) is added to the snapshot when it lands.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

import prismal.composition as composition
from prismal.composition import host_surface
from prismal.composition.runtime import RuntimeContext

pytestmark = pytest.mark.unit

VIEW_FIELDS: dict[str, tuple[str, ...]] = {
    "BudgetAmounts": ("tokens", "cost_usd", "calls", "wall_clock_s"),
    "BudgetStatusView": (
        "enabled",
        "scope",
        "status",
        "breached_dimension",
        "soft_ratio",
        "hard_cap",
        "limits",
        "consumed",
        "remaining",
    ),
    "QuotaWindowView": (
        "gateway",
        "window",
        "unit",
        "limit",
        "used",
        "remaining",
        "resets_at",
        "source",
        "as_of",
    ),
    "AuditEntryView": (
        "seq",
        "ts",
        "event",
        "actor",
        "org_id",
        "hash",
        "prev_hash",
        "fields",
    ),
    "AuditPageView": ("page", "page_size", "total", "entries"),
    "AuditChainStatus": ("status", "checked", "first_broken_seq"),
    "ReportWindow": ("since", "until"),
    "DecisionIntegrationStats": (
        "integration",
        "mode",
        "samples",
        "agreement_ratio",
        "accepted",
        "escalated",
        "abstained",
        "llm_calls_avoided",
        "window",
    ),
    "DecisionsReportView": ("enabled", "integrations"),
    "RoutingPolicyStats": (
        "policy",
        "decisions",
        "decisions_by_tier",
        "escalations",
        "escalations_by_reason",
        "plan_cache_hits",
        "estimated_savings_usd",
        "window",
    ),
    "RoutingReportView": ("enabled", "mode", "policies"),
    "A2ARouteSpec": ("card_path", "rpc_path", "tenant_scoped"),
}

METHOD_SIGNATURES: dict[str, str] = {
    "bind_org": "(self) -> 'Iterator[None]'",
    "budget_status": "(self, *, thread_id: 'str | None' = None) -> 'BudgetStatusView'",
    "quota_windows": "(self) -> 'list[QuotaWindowView]'",
    "audit_page": (
        "(self, *, page: 'int' = 1, page_size: 'int' = 50, "
        "since: 'datetime | None' = None) -> 'AuditPageView'"
    ),
    "verify_audit_chain": "(self, *, max_records: 'int' = 10000) -> 'AuditChainStatus'",
    "decisions_report": (
        "(self, *, since: 'datetime | None' = None, "
        "until: 'datetime | None' = None) -> 'DecisionsReportView'"
    ),
    "routing_report": (
        "(self, *, since: 'datetime | None' = None, "
        "until: 'datetime | None' = None) -> 'RoutingReportView'"
    ),
    "a2a_routes": (
        "(self, *, protocol_version: \"Literal['0.3', '1.0'] | None\" = None) -> 'A2ARouteSpec'"
    ),
}

BUILD_RUNTIME_SIGNATURE = (
    "(settings: 'Settings | None' = None, *, org_id: 'str | None' = None, "
    "config_source: 'ConfigSourcePort | None' = None, "
    "overrides: 'dict[str, Any] | None' = None, "
    "mode: \"Literal['global', 'context'] | None\" = None, "
    "collection_base: 'str' = 'default', mcp_config_path: 'Path | None' = None, "
    "graph: 'Any | None' = None, a2a_agents: 'list[Any] | None' = None, "
    "gateway_vault: 'Any | None' = None, decision_vault: 'Any | None' = None, "
    "memory_backend: 'MemoryBackendPort | None' = None, "
    "memory_graph: 'MemoryGraphPort | None' = None, "
    "memory_hooks: 'MemoryHooks | None' = None) "
    "-> 'RuntimeContext'"
)


@pytest.mark.parametrize(("name", "fields"), sorted(VIEW_FIELDS.items()))
def test_view_types_are_frozen_with_wire_fields(name: str, fields: tuple[str, ...]) -> None:
    cls = getattr(host_surface, name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert tuple(f.name for f in dataclasses.fields(cls)) == fields


@pytest.mark.parametrize("name", sorted(VIEW_FIELDS))
def test_view_types_reexported_from_composition(name: str) -> None:
    assert getattr(composition, name) is getattr(host_surface, name)
    assert name in composition.__all__


def test_entry_points_present() -> None:
    assert {"build_runtime", "build_test_runtime"} <= set(composition.__all__)


@pytest.mark.parametrize(("method", "signature"), sorted(METHOD_SIGNATURES.items()))
def test_runtime_context_method_signatures(method: str, signature: str) -> None:
    assert str(inspect.signature(getattr(RuntimeContext, method))) == signature


def test_quota_windows_is_async_and_others_sync() -> None:
    assert inspect.iscoroutinefunction(RuntimeContext.quota_windows)
    for name in set(METHOD_SIGNATURES) - {"quota_windows"}:
        assert not inspect.iscoroutinefunction(getattr(RuntimeContext, name))


def test_build_runtime_signature() -> None:
    assert str(inspect.signature(composition.build_runtime)) == BUILD_RUNTIME_SIGNATURE


def test_runtime_context_has_settings_field() -> None:
    names = {f.name for f in dataclasses.fields(RuntimeContext)}
    assert "settings" in names
