"""Concrete tool providers for :class:`ToolProviderPort` (Fase Y2, SPEC-TPI-002..007).

Host-facing module: like ``plugins.py``, it is allowed to (lazily) import
``prismal.mcp`` and ``prismal.skills`` — the pure agent core is not
(DD-TPI-003). The host composes these providers and injects the result via
``tool_registry.set_tool_provider()``; the core only ever calls
``provider.get_tools()``.

Merge policy parity: ``CompositeToolProvider`` reproduces the historical
``get_tools_for_agent`` strategy exactly — priority order (MCP → Skills →
stubs), name-based stub dedupe, the per-source MCP cap, the global total cap,
and the fixed-tool-agent exemption (DD-TPI-004).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.tools import BaseTool

    from prismal.agents.extension.ports import ToolProviderPort
    from prismal.core.config import Settings
    from prismal.mcp.client import MCPClientManager
    from prismal.mcp.tool_cache import McpToolListCache
    from prismal.skills.manager import SkillsManager

logger = get_logger("prismal.agents.extension.providers")

# Platform policy caps — parity with tool_registry._MAX_MCP_TOOLS /
# _MAX_TOTAL_TOOLS / _FIXED_TOOL_AGENTS (DD-TPI-004). They live here so the
# registry can drop its MCP/Skills wiring without losing the limits.
_MAX_MCP_TOOLS: int = 60
_MAX_TOTAL_TOOLS: int = 120
_FIXED_TOOL_AGENTS: frozenset[str] = frozenset({"cron_manager", "critic"})


class StubToolProvider:
    """Static fallback stubs from ``tools.py``, selected per agent (SPEC-TPI-004).

    Encapsulates the historical ``stub_map`` of ``get_tools_for_agent``.
    Always returns the agent's stub set — the fixed-tool-agent exemption is
    enforced by :class:`CompositeToolProvider`, which reads
    :attr:`fixed_tool_agents`.
    """

    def __init__(
        self,
        *,
        fixed_tool_agents: frozenset[str] = _FIXED_TOOL_AGENTS,
    ) -> None:
        self.fixed_tool_agents = fixed_tool_agents

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        """Return the static stub set for *agent_name* (unknown agents → ``[]``)."""
        del capabilities, phase  # parity: stubs are never capability-filtered
        from prismal.agents.subgraphs.ml_pipeline.tools_ml import ML_PIPELINE_TOOLS
        from prismal.agents.tools import (
            CODER_TOOLS,
            CRITIC_TOOLS,
            CRON_MANAGER_TOOLS,
            DATA_ANALYST_TOOLS,
            FILE_MANAGER_TOOLS,
            RAG_AGENT_TOOLS,
            RESEARCHER_TOOLS,
            read_file,
            write_file,
        )
        from prismal.sandbox.tools import SANDBOX_TOOLS

        stub_map: dict[str, list[BaseTool]] = {
            "researcher": RESEARCHER_TOOLS,
            "coder": CODER_TOOLS + SANDBOX_TOOLS,
            "rag_agent": RAG_AGENT_TOOLS,
            "critic": CRITIC_TOOLS,
            "data_analyst": DATA_ANALYST_TOOLS + SANDBOX_TOOLS,
            "file_manager": FILE_MANAGER_TOOLS,
            # Planner needs file I/O to persist generated specs to disk;
            # cron tools let it schedule recurring agent tasks.
            "planner": [read_file, write_file, *CRON_MANAGER_TOOLS],
            "cron_manager": CRON_MANAGER_TOOLS,
            # ml_pipeline subgraph agents share the same ML tool set.
            "data_ingester": ML_PIPELINE_TOOLS,
            "eda_analyst": ML_PIPELINE_TOOLS,
            "feature_engineer": ML_PIPELINE_TOOLS,
            "model_trainer": ML_PIPELINE_TOOLS,
            "model_evaluator": ML_PIPELINE_TOOLS,
            "model_exporter": ML_PIPELINE_TOOLS,
            # financial_analyst subgraph agents — LLM-only nodes (no tools).
            "market_data_collector": [],
            "technical_analyst": [],
            "fundamental_analyst": [],
            "risk_sentiment_analyst": [],
            "report_generator": [],
        }
        return stub_map.get(agent_name, [])


class McpToolProvider:
    """Tools from connected MCP servers, capped at *max_tools* (SPEC-TPI-002).

    Parity with the historical ``get_mcp_tools(capabilities=...)[:60]``:
    any manager error is caught and yields ``[]``. ``agent_name`` is ignored.

    Phase PRT (opt-in; defaults ⇒ output byte-for-byte unchanged):

    * ``mcp_tool_cache_enabled`` — each server's list goes through a
      :class:`~prismal.mcp.tool_cache.McpToolListCache` keyed by server / *org_id* /
      *principal*; while the TTL is valid, or the content hash is unchanged, the **same**
      ``BaseTool`` objects are returned (stable tool block for prompt caching).
    * The PRT MCP layer is active when the cache is on or ``mcp_protocol_mode != "legacy"``;
      then, with ``mcp_tasks_enabled=False``, task-only tools
      (``execution.taskSupport == "required"``) are filtered and counted
      (``prismal.mcp_tasks_total{status="unavailable"}``).
    """

    def __init__(
        self,
        manager: MCPClientManager,
        *,
        max_tools: int = _MAX_MCP_TOOLS,
        settings: Settings | None = None,
        tool_cache: McpToolListCache | None = None,
        org_id: str | None = None,
        principal: str | None = None,
        metrics: Any = None,
    ) -> None:
        self._manager = manager
        self._max_tools = max_tools
        self._cache_enabled = bool(settings is not None and settings.mcp_tool_cache_enabled)
        self._prt_active = bool(
            settings is not None and (self._cache_enabled or settings.mcp_protocol_mode != "legacy")
        )
        self._tasks_enabled = bool(settings is not None and settings.mcp_tasks_enabled)
        self._settings = settings
        self._tool_cache = tool_cache
        self._org_id = org_id
        self._principal = principal
        self._metrics = metrics
        # (scope_key, content_hash, id(connection)) → (connection, the BaseTool objects
        # first built for that content on that connection). The connection is part of the
        # key because a reconnect replaces it while the tool list (hash) stays the same —
        # reusing the old adapters would bind every call to a dead connection.
        self._tool_memo: dict[tuple[str, str, int], tuple[object, list[BaseTool]]] = {}

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        """Return MCP tools filtered by *capabilities* and capped at *max_tools*."""
        del agent_name, phase  # MCP pool is agent-agnostic
        try:
            tools = self._manager.get_all_langchain_tools(capabilities=capabilities)
        except Exception as exc:
            logger.warning("tool_provider.mcp_error", error=str(exc))
            return []
        if self._prt_active:
            tools = self._apply_protocol_layer(tools)
        return tools[: self._max_tools]

    # -- Phase PRT (opt-in) ---------------------------------------------------

    def _metrics_sink(self) -> Any:
        if self._metrics is None:
            from prismal.monitoring.otel import OTelManager

            self._metrics = OTelManager()
        return self._metrics

    def _apply_protocol_layer(self, tools: list[BaseTool]) -> list[BaseTool]:
        if not self._tasks_enabled:
            kept = [t for t in tools if not _is_task_only(t)]
            dropped = len(tools) - len(kept)
            if dropped:
                self._metrics_sink().increment_counter(
                    "mcp_tasks", value=dropped, attributes={"status": "unavailable"}
                )
            tools = kept
        if not self._cache_enabled:
            return tools
        try:
            return self._through_cache(tools)
        except Exception as exc:  # get_tools must never raise (SPEC-TPI-007)
            logger.warning("tool_provider.mcp_cache_error", error=str(exc))
            return tools

    def _through_cache(self, tools: list[BaseTool]) -> list[BaseTool]:
        from prismal.mcp.protocol import ToolListPage
        from prismal.mcp.tool_cache import McpToolListCache

        if self._tool_cache is None:
            self._tool_cache = McpToolListCache(
                settings=self._settings, metrics=self._metrics_sink()
            )
        cache = self._tool_cache
        groups: dict[str | None, list[BaseTool]] = {}
        for tool in tools:
            groups.setdefault(_server_of(tool), []).append(tool)

        out: list[BaseTool] = []
        for server, group in groups.items():
            if server is None:
                out.extend(group)
                continue
            cached = cache.get(server, org_id=self._org_id, principal=self._principal)
            if cached is None:
                hints = _tool_list_page_of(group[0])
                page = ToolListPage(
                    tools=tuple(_tool_spec(t) for t in group),
                    ttl_ms=hints.ttl_ms if hints is not None else None,
                    cache_scope=hints.cache_scope if hints is not None else None,
                )
                cached = cache.put(server, page, org_id=self._org_id, principal=self._principal)
            connection = getattr(group[0], "_connection", None)
            key = (cached.scope_key, cached.content_hash, id(connection))
            memo = self._tool_memo.get(key)
            # ``is`` guards against id() reuse after the old connection is collected.
            if memo is None or memo[0] is not connection:
                self._tool_memo = {
                    k: v for k, v in self._tool_memo.items() if k[0] != cached.scope_key
                }
                memo = self._tool_memo[key] = (connection, group)
            out.extend(memo[1])
        return out


def _server_of(tool: BaseTool) -> str | None:
    """Server name of an ``MCPToolAdapter`` (``None`` for anything else)."""
    connection = getattr(tool, "_connection", None)
    config = getattr(connection, "_config", None)
    name = getattr(config, "name", None)
    return name if isinstance(name, str) else None


def _tool_list_page_of(tool: BaseTool) -> Any:
    """The stateless ``ToolListPage`` (``ttlMs`` / ``cacheScope``) of the tool's connection."""
    return getattr(getattr(tool, "_connection", None), "tool_list_page", None)


def _mcp_tool_of(tool: BaseTool) -> Any:
    return getattr(tool, "_mcp_tool", None)


def _is_task_only(tool: BaseTool) -> bool:
    """``execution.taskSupport == "required"`` (MCP 2025-11-25+ ``Tool.execution``)."""
    execution = getattr(_mcp_tool_of(tool), "execution", None)
    return getattr(execution, "taskSupport", None) == "required"


def _tool_spec(tool: BaseTool) -> dict[str, Any]:
    """The cacheable spec of one adapter: the MCP tool + the description the LLM sees."""
    mcp_tool = _mcp_tool_of(tool)
    spec: dict[str, Any] = (
        mcp_tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        if mcp_tool is not None
        else {"name": tool.name}
    )
    spec["description"] = tool.description
    return spec


class SkillToolProvider:
    """Tools from currently active skills (SPEC-TPI-003).

    Parity with the historical ``get_skill_tools()``: a fresh
    ``SkillsManager`` is constructed per call when none was injected, and
    any error yields ``[]``. ``agent_name``/``capabilities`` are ignored
    (skills are not filtered today — PA-3).
    """

    def __init__(self, manager: SkillsManager | None = None) -> None:
        self._manager = manager

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        """Return tools from active skills (``[]`` on any manager failure)."""
        del agent_name, capabilities, phase
        try:
            manager = self._manager
            if manager is None:
                from prismal.skills.manager import SkillsManager

                manager = SkillsManager()
            return manager.get_active_tools()
        except Exception as exc:
            logger.warning("tool_provider.skill_error", error=str(exc))
            return []


class CompositeToolProvider:
    """Merge N providers reproducing the historical registry strategy (SPEC-TPI-005).

    Convention: the **last** :class:`StubToolProvider` in *providers* is the
    fallback source; every other provider is a "live" source consumed in
    order (MCP → Skills). Fixed-tool agents receive only their stubs; live
    names dedupe stubs; the merged list is truncated to *max_total*.
    """

    def __init__(
        self,
        providers: list[ToolProviderPort],
        *,
        max_total: int = _MAX_TOTAL_TOOLS,
        fixed_tool_agents: frozenset[str] = _FIXED_TOOL_AGENTS,
        phase_capability_map: Mapping[str, Mapping[str, list[str]]] | None = None,
        tool_gating_mode: str = "remove",
    ) -> None:
        self._providers: tuple[ToolProviderPort, ...] = tuple(providers)
        # Phase TKE (SPEC-TKE-GAT-001): "remove" = LH2 exactly; "mask" keeps the
        # exposed list phase-independent and exposes the allow-set separately.
        self._tool_gating_mode = tool_gating_mode
        self._max_total = max_total
        self._fixed_tool_agents = fixed_tool_agents
        self._phase_capability_map = phase_capability_map or {}
        # Last StubToolProvider wins as the fallback source; earlier stub
        # providers (unusual) are treated as live sources.
        stub: StubToolProvider | None = None
        for candidate in self._providers:
            if isinstance(candidate, StubToolProvider):
                stub = candidate
        self._stub_provider = stub

    @property
    def providers(self) -> tuple[ToolProviderPort, ...]:
        """The composed providers, in priority order."""
        return self._providers

    def _effective_capabilities(
        self, agent_name: str, capabilities: list[str] | None, phase: str | None
    ) -> list[str] | None:
        """Narrow *capabilities* per ``phase_capability_map[agent_name][phase]`` (SPEC-LH-GAT-002).

        Never returns a superset of what *capabilities* alone would allow —
        the override list is intersected with (not added to) the caller's
        capabilities, or used verbatim when *capabilities* is ``None`` (``all``).
        """
        if phase is None:
            return capabilities
        override = self._phase_capability_map.get(agent_name, {}).get(phase)
        if override is None:
            return capabilities
        if capabilities is None:
            return list(override)
        return [c for c in capabilities if c in override]

    @staticmethod
    def _call_provider(
        provider: ToolProviderPort,
        agent_name: str,
        capabilities: list[str] | None,
        phase: str | None,
    ) -> list[BaseTool]:
        """Call *provider*, falling open to a phase-less call on ``TypeError`` (DD-LH-006)."""
        if phase is None:
            return provider.get_tools(agent_name=agent_name, capabilities=capabilities)
        try:
            return provider.get_tools(agent_name=agent_name, capabilities=capabilities, phase=phase)
        except TypeError:
            return provider.get_tools(agent_name=agent_name, capabilities=capabilities)

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        """Return the merged, deduplicated, capped tool list for *agent_name*.

        In ``mask`` mode (Phase TKE) the phase is not used to narrow the list:
        the full list is returned **name-sorted** so it is byte-stable across
        phases; :meth:`allowed_tool_names` exposes the execution allow-set.
        """
        if self._tool_gating_mode == "mask":
            full = self._resolve(agent_name, capabilities, None)
            return sorted(full, key=lambda t: t.name)
        return self._resolve(agent_name, capabilities, phase)

    def allowed_tool_names(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None,
        phase: str | None,
    ) -> frozenset[str] | None:
        """Phase allow-set for mask mode; ``None`` = no restriction (SPEC-TKE-GAT-001)."""
        if phase is None or agent_name in self._fixed_tool_agents:
            return None
        if self._phase_capability_map.get(agent_name, {}).get(phase) is None:
            return None
        return frozenset(t.name for t in self._resolve(agent_name, capabilities, phase))

    def _resolve(
        self, agent_name: str, capabilities: list[str] | None, phase: str | None
    ) -> list[BaseTool]:
        """LH2 resolution: phase-narrowed, merged, deduplicated, capped."""
        stubs = (
            self._stub_provider.get_tools(agent_name=agent_name)
            if self._stub_provider is not None
            else []
        )

        # Fixed-tool-set agents skip live sources entirely — their prompts
        # must stay small to avoid rate-limit errors (parity).
        if agent_name in self._fixed_tool_agents:
            logger.debug(
                "tool_provider.tools_resolved",
                agent=agent_name,
                live=0,
                stubs_kept=len(stubs),
                total=len(stubs),
            )
            return stubs

        effective_capabilities = self._effective_capabilities(agent_name, capabilities, phase)
        if phase is not None and effective_capabilities != capabilities:
            from contextlib import suppress

            from prismal.monitoring.otel import OTelManager

            with suppress(Exception):
                OTelManager().increment_counter(
                    "tool_gate_narrowed", attributes={"agent": agent_name}
                )

        live_tools: list[BaseTool] = []
        for provider in self._providers:
            if provider is self._stub_provider:
                continue
            try:
                live_tools.extend(
                    self._call_provider(provider, agent_name, effective_capabilities, phase)
                )
            except Exception as exc:
                logger.warning(
                    "tool_provider.subprovider_error",
                    provider=type(provider).__name__,
                    agent=agent_name,
                    error=str(exc),
                )
                from prismal.monitoring.otel import OTelManager

                OTelManager().increment_counter(
                    "tool_provider_subprovider_errors",
                    attributes={"provider": type(provider).__name__},
                )

        live_names = {t.name for t in live_tools}
        filtered_stubs = [t for t in stubs if t.name not in live_names]
        merged = live_tools + filtered_stubs

        # Enforce the global provider cap (OpenAI rejects > 128 tool schemas).
        # The merge order is priority-ordered, so truncating from the tail
        # drops the lowest-value entries first (parity).
        if len(merged) > self._max_total:
            dropped = len(merged) - self._max_total
            merged = merged[: self._max_total]
            logger.warning(
                "tool_provider.tools_truncated",
                agent=agent_name,
                cap=self._max_total,
                dropped=dropped,
                live=len(live_tools),
                stubs=len(filtered_stubs),
            )

        logger.debug(
            "tool_provider.tools_resolved",
            agent=agent_name,
            live=len(live_tools),
            stubs_kept=len(filtered_stubs),
            total=len(merged),
        )
        return merged


class FakeToolProvider:
    """Deterministic provider for tests — no I/O, no heavy imports (SPEC-TPI-006)."""

    def __init__(
        self,
        mapping: dict[str, list[BaseTool]] | None = None,
        *,
        default: list[BaseTool] | None = None,
    ) -> None:
        self._mapping = mapping or {}
        self._default = default or []

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,
    ) -> list[BaseTool]:
        """Return ``mapping.get(agent_name, default)``."""
        del capabilities, phase
        return self._mapping.get(agent_name, self._default)


async def build_default_tool_provider(
    settings: Settings | None = None,
    *,
    mcp_config_path: Path | None = None,
) -> CompositeToolProvider:
    """Assemble the standard host composite: MCP (optional) + Skills + Stubs.

    Intended for the ``prismal-sdk`` / ``prismal-web`` lifespan::

        provider = await build_default_tool_provider(settings)
        set_tool_provider(provider)

    When the MCP config file exists, an ``MCPClientManager`` is built and
    connected (``load_from_config``); a connection failure logs a warning and
    omits the MCP provider rather than aborting startup. This is the only
    async piece of the feature — every ``get_tools`` is sync (SPEC-TPI-007).

    Args:
        settings: Host-level toggles: tool gating (LH/TKE) and the opt-in Phase PRT
            MCP flags forwarded to :class:`McpToolProvider` (defaults ⇒ unchanged).
        mcp_config_path: Override for the MCP server config. ``None`` uses
            the default ``config/mcp_servers.yaml``.
    """
    providers: list[ToolProviderPort] = []

    try:
        from prismal.mcp.client import _DEFAULT_CONFIG_PATH, MCPClientManager

        path = mcp_config_path if mcp_config_path is not None else _DEFAULT_CONFIG_PATH
        if path.exists():
            prt_on = settings is not None and (
                settings.mcp_protocol_mode != "legacy" or settings.mcp_tasks_enabled
            )
            # Defaults keep the 3.16.0 construction byte-for-byte (no extra kwargs).
            manager = (
                MCPClientManager(path, settings=settings) if prt_on else MCPClientManager(path)
            )
            await manager.load_from_config()
            connected = [s.name for s in manager.get_server_status() if s.connected]
            logger.info(
                "tool_provider.mcp_ready",
                servers=connected,
                count=len(connected),
            )
            providers.append(McpToolProvider(manager, settings=settings))
        else:
            logger.info("tool_provider.mcp_config_missing", path=str(path))
    except Exception as exc:
        logger.warning(
            "tool_provider.mcp_init_failed",
            error=str(exc),
            hint="MCP tools will not be available",
        )

    providers.append(SkillToolProvider())
    providers.append(StubToolProvider())

    logger.info(
        "tool_provider.default_built",
        providers=[type(p).__name__ for p in providers],
    )
    return CompositeToolProvider(providers, **_gating_kwargs(settings))


def _gating_kwargs(settings: Settings | None) -> dict[str, Any]:
    """LH phase map + TKE gating mode for the default composite (opt-in; ``{}`` when off)."""
    if settings is None or not settings.tool_gating_enabled:
        return {}
    mode = (
        "mask"
        if settings.token_efficiency_enabled and settings.tool_gating_mode == "mask"
        else "remove"
    )
    return {
        "phase_capability_map": load_phase_capability_map(settings.tool_gating_phase_map_path),
        "tool_gating_mode": mode,
    }


def load_phase_capability_map(path: str | None = None) -> dict[str, dict[str, list[str]]]:
    """Load + validate ``config/tool_gating_phases.yaml`` (SPEC-LH-GAT-002).

    A missing file yields an empty map (no overrides — phase gating is a
    pure no-op). A malformed file raises :class:`ToolGatingConfigError`
    (fail loud at startup, not at request time — mirrors ``load_tool_policies``).
    """
    import yaml

    from prismal.core.exceptions import ToolGatingConfigError

    resolved = Path(path) if path else Path("config/tool_gating_phases.yaml")
    if not resolved.exists():
        logger.info("tool_gating.no_file", path=str(resolved))
        return {}

    try:
        with resolved.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ToolGatingConfigError(f"tool_gating_phases.yaml is malformed: {exc}") from exc

    if not isinstance(data, dict):
        raise ToolGatingConfigError("tool_gating_phases.yaml must be a mapping")
    return data


__all__ = [
    "CompositeToolProvider",
    "FakeToolProvider",
    "McpToolProvider",
    "SkillToolProvider",
    "StubToolProvider",
    "build_default_tool_provider",
    "load_phase_capability_map",
]
