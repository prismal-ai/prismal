"""Prismal extension surface (Fase X).

Public API for building LangGraph nodes and subgraphs on top of prismal
without forking the repo. Importing from here is the supported contract.
"""

from __future__ import annotations

from prismal.agents.extension.adapters import (
    InputMapping,
    LangChainRunnableAdapter,
)
from prismal.agents.extension.builder import (
    BuilderDefaults,
    PrismalStateGraphBuilder,
)
from prismal.agents.extension.decorators import (
    NodeFn,
    NodeMetadata,
    RetryPolicy,
    SecurityLevel,
    get_node_metadata,
    list_registered_nodes,
    prismal_node,
)
from prismal.agents.extension.plugins import (
    DiscoveryReport,
    PluginInfo,
    RAGEngineRegistry,
    discover_plugins,
    get_plugin_info,
    list_plugins,
)
from prismal.agents.extension.ports import (
    AuditPort,
    CheckpointPort,
    EmbeddingsPort,
    ToolPort,
    ToolProviderPort,
    VectorStorePort,
    VectorStoreProviderPort,
    conforms_to,
)
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    SkillToolProvider,
    StubToolProvider,
    build_default_tool_provider,
)

__all__ = [
    "AuditPort",
    "BuilderDefaults",
    "CheckpointPort",
    "CompositeToolProvider",
    "DiscoveryReport",
    "EmbeddingsPort",
    "FakeToolProvider",
    "InputMapping",
    "LangChainRunnableAdapter",
    "McpToolProvider",
    "NodeFn",
    "NodeMetadata",
    "PluginInfo",
    "PrismalStateGraphBuilder",
    "RAGEngineRegistry",
    "RetryPolicy",
    "SecurityLevel",
    "SkillToolProvider",
    "StubToolProvider",
    "ToolPort",
    "ToolProviderPort",
    "VectorStorePort",
    "VectorStoreProviderPort",
    "build_default_tool_provider",
    "conforms_to",
    "discover_plugins",
    "get_node_metadata",
    "get_plugin_info",
    "list_plugins",
    "list_registered_nodes",
    "prismal_node",
]
