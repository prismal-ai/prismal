"""Plugin discovery via ``importlib.metadata`` entry points (SPEC-EXT-004).

Third-party packages expose extensions by declaring entry points in one of
four groups: ``prismal.subgraphs``, ``prismal.nodes``, ``prismal.tools``,
``prismal.rag_engines``. :func:`discover_plugins` loads each in isolation —
a failing plugin never aborts the rest.

Plugin author contract (see ``docs/extension.md``):

* ``prismal.subgraphs`` — ``register(registry) -> None | SubgraphDefinition``.
  Either self-register via ``registry.register_sync(...)`` or return a
  ``SubgraphDefinition`` for the discoverer to register.
* ``prismal.nodes`` — a callable already decorated with ``@prismal_node``.
* ``prismal.tools`` — a ``BaseTool`` instance or a zero-arg factory returning one.
* ``prismal.rag_engines`` — a class implementing the RAG engine protocol.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any, Literal, get_args

import structlog

from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from prismal.core.config import get_settings
from prismal.core.exceptions import PluginConflictError, PluginLoadError

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = structlog.get_logger("prismal.ext.plugins")

PluginGroup = Literal["subgraphs", "nodes", "tools", "rag_engines"]
_ALL_GROUPS: tuple[PluginGroup, ...] = get_args(PluginGroup)
_GROUP_PREFIX = "prismal."

LoadStatus = Literal["loaded", "error", "skipped_by_denylist", "skipped_not_in_allowlist"]


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginInfo:
    """Static information about a discovered plugin (no loading required)."""

    name: str
    group: PluginGroup
    module: str
    object_name: str
    dist_name: str
    dist_version: str


@dataclass(frozen=True)
class PluginLoadResult:
    """Outcome of attempting to load a single plugin."""

    info: PluginInfo
    status: LoadStatus
    error: str | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True)
class DiscoveryReport:
    """Aggregate result of :func:`discover_plugins`."""

    loaded: list[PluginLoadResult] = field(default_factory=list)
    failed: list[PluginLoadResult] = field(default_factory=list)
    skipped: list[PluginLoadResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def loaded_count(self) -> int:
        return len(self.loaded)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


# ── RAG engine registry ───────────────────────────────────────────────────────


class RAGEngineRegistry:
    """Process-wide registry of RAG engine classes contributed by plugins."""

    _instance: RAGEngineRegistry | None = None

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._engines: dict[str, type] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> RAGEngineRegistry:
        """Return the process-wide singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, name: str, engine_cls: type) -> None:
        """Register a RAG engine class under ``name``.

        Raises:
            ValueError: If ``name`` is already registered.
        """
        with self._lock:
            if name in self._engines:
                raise ValueError(f"RAG engine '{name}' already registered")
            self._engines[name] = engine_cls

    def get(self, name: str) -> type | None:
        """Return the engine class for ``name`` or ``None``."""
        return self._engines.get(name)

    def list(self) -> list[str]:
        """Return sorted registered engine names."""
        return sorted(self._engines)


# ── Plugin tool registry (respects the global tool cap) ───────────────────────

_PLUGIN_TOOLS: dict[str, Any] = {}


def get_plugin_tools() -> list[Any]:
    """Return tools contributed by plugins (in registration order)."""
    return list(_PLUGIN_TOOLS.values())


# ── Entry-point access (seam for tests) ───────────────────────────────────────


def _entry_points(group: str) -> list[EntryPoint]:
    """Return entry points for a fully-qualified group (overridable in tests)."""
    return list(entry_points(group=group))


def _plugin_info(ep: EntryPoint, group: PluginGroup) -> PluginInfo:
    dist = getattr(ep, "dist", None)
    dist_name = getattr(dist, "name", "") or ""
    dist_version = getattr(dist, "version", "") or ""
    return PluginInfo(
        name=ep.name,
        group=group,
        module=ep.module,
        object_name=ep.attr,
        dist_name=dist_name,
        dist_version=dist_version,
    )


# ── Loaders ───────────────────────────────────────────────────────────────────


def _load_subgraph(ep: EntryPoint, registry: SubgraphRegistry) -> None:
    obj = ep.load()
    result = obj(registry)
    if isinstance(result, SubgraphDefinition):
        registry.register_sync(result.name, result)


def _load_node(ep: EntryPoint, _registry: SubgraphRegistry) -> None:
    obj = ep.load()
    if getattr(obj, "__prismal_node__", None) is None:
        raise TypeError(f"Entry point '{ep.name}' is not decorated with @prismal_node")
    # Importing the object already registered it via the decorator side effect.


def _load_tool(ep: EntryPoint, _registry: SubgraphRegistry) -> None:
    from prismal.agents.tool_registry import _MAX_TOTAL_TOOLS

    obj = ep.load()
    tool = obj() if callable(obj) and not _looks_like_tool(obj) else obj
    tool_name = getattr(tool, "name", ep.name)
    if tool_name in _PLUGIN_TOOLS:
        raise PluginConflictError(tool_name, ["<existing>", ep.name])
    if len(_PLUGIN_TOOLS) >= _MAX_TOTAL_TOOLS:
        raise RuntimeError(f"Plugin tool cap reached ({_MAX_TOTAL_TOOLS}); '{tool_name}' dropped")
    _PLUGIN_TOOLS[tool_name] = tool


def _looks_like_tool(obj: Any) -> bool:
    return hasattr(obj, "name") and hasattr(obj, "description") and not isinstance(obj, type)


def _load_rag_engine(ep: EntryPoint, _registry: SubgraphRegistry) -> None:
    obj = ep.load()
    RAGEngineRegistry.get_instance().register(ep.name, obj)


_LOADERS: dict[PluginGroup, Any] = {
    "subgraphs": _load_subgraph,
    "nodes": _load_node,
    "tools": _load_tool,
    "rag_engines": _load_rag_engine,
}


# ── Public API ────────────────────────────────────────────────────────────────


def _resolve_groups(groups: list[PluginGroup] | None, settings: Settings) -> list[PluginGroup]:
    if groups is not None:
        return groups
    if not settings.plugins_autodiscover:
        return []
    enabled = settings.plugins_groups_enabled
    return [g for g in _ALL_GROUPS if g in enabled]


def discover_plugins(
    *,
    settings: Settings | None = None,
    registry: SubgraphRegistry | None = None,
    groups: list[PluginGroup] | None = None,
) -> DiscoveryReport:
    """Discover and install plugins from entry points.

    Applies allowlist/denylist (denylist wins), isolates each load in a
    try/except, and audits every outcome. See the module docstring for the
    per-group plugin author contract.

    Args:
        settings: Prismal settings. Defaults to :func:`get_settings`.
        registry: Target subgraph registry. Defaults to the global singleton.
        groups: Groups to discover. ``None`` uses the enabled groups from
            settings (or nothing when ``plugins_autodiscover`` is False).

    Returns:
        A :class:`DiscoveryReport` partitioning results into loaded / failed /
        skipped.
    """
    settings = settings or get_settings()
    registry = registry or SubgraphRegistry.get_instance()
    target_groups = _resolve_groups(groups, settings)

    loaded: list[PluginLoadResult] = []
    failed: list[PluginLoadResult] = []
    skipped: list[PluginLoadResult] = []
    audit = _audit_logger()
    started = time.monotonic()

    for group in target_groups:
        for ep in _entry_points(f"{_GROUP_PREFIX}{group}"):
            info = _plugin_info(ep, group)
            skip_status = _skip_status(ep.name, settings)
            if skip_status is not None:
                skipped.append(PluginLoadResult(info=info, status=skip_status))
                continue
            t0 = time.monotonic()
            try:
                _LOADERS[group](ep, registry)
            except Exception as exc:  # isolation is the whole point of discovery
                duration = (time.monotonic() - t0) * 1000.0
                err = str(PluginLoadError(ep.name, ep.value, exc))
                failed.append(
                    PluginLoadResult(info=info, status="error", error=err, duration_ms=duration)
                )
                logger.warning("plugin.load_failed", plugin=ep.name, group=group, error=str(exc))
                audit.log_event(
                    "plugin_loaded",
                    {"name": ep.name, "group": group, "status": "error"},
                )
            else:
                duration = (time.monotonic() - t0) * 1000.0
                loaded.append(PluginLoadResult(info=info, status="loaded", duration_ms=duration))
                logger.info("plugin.loaded", plugin=ep.name, group=group)
                audit.log_event(
                    "plugin_loaded",
                    {"name": ep.name, "group": group, "status": "loaded"},
                )

    return DiscoveryReport(
        loaded=loaded,
        failed=failed,
        skipped=skipped,
        total_duration_ms=(time.monotonic() - started) * 1000.0,
    )


def _skip_status(name: str, settings: Settings) -> LoadStatus | None:
    if name in settings.plugins_denylist:
        return "skipped_by_denylist"
    if settings.plugins_allowlist and name not in settings.plugins_allowlist:
        return "skipped_not_in_allowlist"
    return None


def list_plugins(*, settings: Settings | None = None) -> list[PluginInfo]:
    """List installed plugins across enabled groups without loading them."""
    settings = settings or get_settings()
    groups = _resolve_groups(None, settings) or list(_ALL_GROUPS)
    infos: list[PluginInfo] = []
    for group in groups:
        for ep in _entry_points(f"{_GROUP_PREFIX}{group}"):
            infos.append(_plugin_info(ep, group))
    return infos


def get_plugin_info(name: str, *, settings: Settings | None = None) -> PluginInfo | None:
    """Return :class:`PluginInfo` for a plugin by entry-point name, or ``None``."""
    for info in list_plugins(settings=settings):
        if info.name == name:
            return info
    return None


def _audit_logger() -> Any:
    from prismal.security.audit import AuditLogger

    return AuditLogger()


__all__ = [
    "DiscoveryReport",
    "PluginGroup",
    "PluginInfo",
    "PluginLoadResult",
    "RAGEngineRegistry",
    "discover_plugins",
    "get_plugin_info",
    "get_plugin_tools",
    "list_plugins",
]
