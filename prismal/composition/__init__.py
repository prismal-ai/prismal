"""Runtime composition root (Phase R).

Public facade re-exporting the composition root (:mod:`prismal.composition.runtime`)
and the config loaders (:mod:`prismal.composition.config_sources`). Import from
here::

    from prismal.composition import build_runtime, RuntimeContext

See :func:`build_runtime` for the host lifespan contract and ``docs/composition-root.md``.
The host read surface view types (HRS) are re-exported from
:mod:`prismal.composition.host_surface` for annotations / ``isinstance``.
"""

from __future__ import annotations

from prismal.composition.config_sources import (
    DEFAULT_COLLECTION_BASE,
    McpConfig,
    McpServerInfo,
    SkillsSource,
    apply_org_overrides,
    collection_for,
    load_mcp_config,
    resolve_skills_source,
    resolve_vector_store,
)
from prismal.composition.host_surface import (
    A2ARouteSpec,
    AuditChainStatus,
    AuditEntryView,
    AuditPageView,
    BudgetAmounts,
    BudgetStatusView,
    DecisionIntegrationStats,
    DecisionsReportView,
    QuotaWindowView,
    ReportWindow,
    RoutingPolicyStats,
    RoutingReportView,
)
from prismal.composition.runtime import (
    RuntimeConfig,
    RuntimeContext,
    VectorStoreProvider,
    build_runtime,
    build_test_runtime,
)

__all__ = [
    "DEFAULT_COLLECTION_BASE",
    "A2ARouteSpec",
    "AuditChainStatus",
    "AuditEntryView",
    "AuditPageView",
    "BudgetAmounts",
    "BudgetStatusView",
    "DecisionIntegrationStats",
    "DecisionsReportView",
    "McpConfig",
    "McpServerInfo",
    "QuotaWindowView",
    "ReportWindow",
    "RoutingPolicyStats",
    "RoutingReportView",
    "RuntimeConfig",
    "RuntimeContext",
    "SkillsSource",
    "VectorStoreProvider",
    "apply_org_overrides",
    "build_runtime",
    "build_test_runtime",
    "collection_for",
    "load_mcp_config",
    "resolve_skills_source",
    "resolve_vector_store",
]
