"""Runtime composition root (Phase R).

Public facade re-exporting the composition root (:mod:`prismal.composition.runtime`)
and the config loaders (:mod:`prismal.composition.config_sources`). Import from
here::

    from prismal.composition import build_runtime, RuntimeContext

See :func:`build_runtime` for the host lifespan contract and ``docs/composition-root.md``.
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
from prismal.composition.runtime import (
    RuntimeConfig,
    RuntimeContext,
    VectorStoreProvider,
    build_runtime,
    build_test_runtime,
)

__all__ = [
    "DEFAULT_COLLECTION_BASE",
    "McpConfig",
    "McpServerInfo",
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
