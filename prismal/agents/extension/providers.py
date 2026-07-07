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
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.tools import BaseTool

    from prismal.agents.extension.ports import ToolProviderPort
    from prismal.core.config import Settings
    from prismal.mcp.client import MCPClientManager
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
    """

    def __init__(self, manager: MCPClientManager, *, max_tools: int = _MAX_MCP_TOOLS) -> None:
        self._manager = manager
        self._max_tools = max_tools

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
        return tools[: self._max_tools]


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
    ) -> None:
        self._providers: tuple[ToolProviderPort, ...] = tuple(providers)
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
        """Return the merged, deduplicated, capped tool list for *agent_name*."""
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
        settings: Reserved for host-level toggles (Fase Y5/Y6); currently
            unused so callers can already pass it forward-compatibly.
        mcp_config_path: Override for the MCP server config. ``None`` uses
            the default ``config/mcp_servers.yaml``.
    """
    del settings  # reserved — Y5 toggles consume it
    providers: list[ToolProviderPort] = []

    try:
        from prismal.mcp.client import _DEFAULT_CONFIG_PATH, MCPClientManager

        path = mcp_config_path if mcp_config_path is not None else _DEFAULT_CONFIG_PATH
        if path.exists():
            manager = MCPClientManager(path)
            await manager.load_from_config()
            connected = [s.name for s in manager.get_server_status() if s.connected]
            logger.info(
                "tool_provider.mcp_ready",
                servers=connected,
                count=len(connected),
            )
            providers.append(McpToolProvider(manager))
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
    return CompositeToolProvider(providers)


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
