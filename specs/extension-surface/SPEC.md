# Prismal Extension Surface — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **PLAN** | `specs/extension-surface/PLAN.md` |
| **Architecture** | `specs/extension-surface/ARCHITECTURE.md` |
| **TASKS** | `specs/extension-surface/TASKS.md` |

---

## Conventions

- `from __future__ import annotations` in all modules.
- Async where applicable; sync only for pure utilities (sniff, validation).
- `frozen` dataclasses where applicable; public types exported from `__init__.py`.
- No new mandatory dependencies — everything built on the existing stack.
- Public API versioned with SemVer; breaking changes require a deprecation cycle.
- Custom exceptions inherit from `PrismalError` (extension to `core/exceptions.py`).

---

## Module Summary

| SPEC | File | Components |
|---|---|---|
| SPEC-EXT-001 | `prismal/langgraph.py` | Re-exports + `VERSION` |
| SPEC-EXT-002 | `prismal/agents/extension/decorators.py` | `@prismal_node`, `NodeMetadata`, `list_registered_nodes()` |
| SPEC-EXT-003 | `prismal/agents/extension/builder.py` | `PrismalStateGraphBuilder` |
| SPEC-EXT-004 | `prismal/agents/extension/plugins.py` | `discover_plugins()`, `DiscoveryReport`, `PluginRegistry` |
| SPEC-EXT-005 | `prismal/agents/extension/adapters.py` | `LangChainRunnableAdapter` |
| SPEC-EXT-006 | `prismal/agents/extension/ports.py` | `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` |
| SPEC-EXT-007 | `prismal/agents/extension/_middleware.py` | Middleware chain (internal) |
| SPEC-EXT-008 | `prismal/plugins.py` | CLI `python -m prismal.plugins` |

---

## SPEC-EXT-001: LangGraph Re-export

**File:** `prismal/langgraph.py`

```python
"""Official LangGraph passthrough for prismal extensions.

This module re-exports the LangGraph symbols that prismal supports as a
public extension surface. Importing from here (rather than directly from
``langgraph.*``) guarantees:

* Version compatibility — ``VERSION`` is the LangGraph version prismal
  was tested against.
* Stable contract — only the symbols below are part of the prismal
  extension API; other LangGraph internals may change without notice.

Example::

    from prismal.langgraph import StateGraph, START, END, Send, add_messages
    from prismal.agents.state import AgentState

    graph = StateGraph(AgentState)
    graph.add_node("my_node", my_node)
    graph.add_edge(START, "my_node")
    graph.add_edge("my_node", END)
    compiled = graph.compile()
"""
from __future__ import annotations

from importlib.metadata import version as _pkg_version

from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from prismal.agents.state import AgentState
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

VERSION: str = _pkg_version("langgraph")
"""Installed LangGraph version, resolved at import time."""

__all__ = [
    "StateGraph",
    "START",
    "END",
    "Send",
    "interrupt",
    "add_messages",
    "CompiledStateGraph",
    "AgentState",
    "SubgraphDefinition",
    "SubgraphRegistry",
    "VERSION",
]
```

---

## SPEC-EXT-002: `@prismal_node` Decorator

**File:** `prismal/agents/extension/decorators.py`

### Types

```python
from typing import Callable, Awaitable, Literal, TypedDict

SecurityLevel = Literal["off", "standard", "strict"]
"""
'off'      — no security middleware (max performance, max risk).
'standard' — InputSanitizer + SecurePromptBuilder on last user message.
'strict'   — standard + ActionInterceptor.check() on any tool_call in state_update.
"""

@dataclass(frozen=True)
class NodeMetadata:
    """Metadata of a node registered via @prismal_node."""
    name: str
    capabilities: tuple[str, ...]
    security: SecurityLevel
    audit: bool
    retry: RetryPolicy | None
    timeout_s: float | None
    raise_on_error: bool
    registered_at: str          # ISO timestamp
    source_module: str          # module where it was defined

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_s: tuple[float, ...] = (0.1, 0.5, 1.0)
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)

NodeFn = Callable[[AgentState], Awaitable[dict]]
"""Signature of a node: async (state) → state_update."""
```

### Main Decorator

```python
def prismal_node(
    *,
    name: str | None = None,
    capabilities: list[str] | None = None,
    security: SecurityLevel = "standard",
    audit: bool = True,
    retry: RetryPolicy | None = None,
    timeout_s: float | None = None,
    raise_on_error: bool = False,
) -> Callable[[NodeFn], NodeFn]:
    """Decorator that wraps a LangGraph node with prismal cross-cutting concerns.

    Applies an ordered middleware chain:
        1. security (InputSanitizer + SecurePromptBuilder + ActionInterceptor)
        2. OTel span "prismal.ext.node.<name>"
        3. logger bind {node_name, session_id, capabilities}
        4. retry with exponential backoff
        5. asyncio.wait_for(timeout_s)
        6. user function
        7. audit log (hash of state_update + duration_ms)
        8. error mapping to NodeExecutionError

    Args:
        name: Node name (default: function name).
        capabilities: List of required MCP capabilities.
            Automatically registered in DEFAULT_CAPABILITY_MAP.
        security: Security middleware level.
        audit: If True, AuditLogger.log_node() per invocation.
        retry: Retry policy. None = no retries.
        timeout_s: Timeout per invocation. None = no timeout.
        raise_on_error: If True, propagate user exceptions.
            If False (default), captures them and returns
            {"metadata": {"error": {...}}}.

    Returns:
        Decorator that wraps `NodeFn`.

    Example::

        @prismal_node(name="my_classifier", capabilities=["general"])
        async def my_classifier(state: AgentState) -> dict:
            last = state["messages"][-1].content
            label = await classify(last)
            return {"metadata": {"my_classifier": {"label": label}}}
    """
    ...


def list_registered_nodes() -> list[NodeMetadata]:
    """Return the list of nodes registered via @prismal_node.

    Useful for introspection, debugging, and for the supervisor to
    discover custom nodes dynamically.
    """
    ...


def get_node_metadata(name: str) -> NodeMetadata | None:
    """Return metadata for a registered node, or None."""
    ...
```

### Attribute of the wrapped callable

```python
# The decorator adds to the returned callable:
wrapped.__prismal_node__: NodeMetadata    # introspection and deduplication by the builder
wrapped.__wrapped__: NodeFn                 # original function (via functools.wraps)
```

---

## SPEC-EXT-003: `PrismalStateGraphBuilder`

**File:** `prismal/agents/extension/builder.py`

### Types

```python
@dataclass(frozen=True)
class BuilderDefaults:
    """Defaults applied by add_node if the callable lacks @prismal_node."""
    security: SecurityLevel = "standard"
    audit: bool = True
    timeout_s: float | None = None
    retry: RetryPolicy | None = None
```

### Main Class

```python
class PrismalStateGraphBuilder:
    """Fluent builder over StateGraph[AgentState] with prismal defaults.

    Each call to add_node() detects whether the callable already has
    @prismal_node applied (via the __prismal_node__ attribute) and, if not,
    wraps it with the builder's defaults.

    Args:
        name: Subgraph name (for SubgraphDefinition and traces).
        defaults: BuilderDefaults applied to nodes without a decorator.
        settings: Prismal settings.

    Example::

        builder = PrismalStateGraphBuilder("my_pipeline")
        builder.add_node("classify", classify_fn, capabilities=["general"])
        builder.add_node("respond", respond_fn)
        builder.add_edge("classify", "respond")
        builder.set_entry_point("classify")
        subgraph = builder.compile()
        register_my_pipeline(registry, subgraph)
    """

    def __init__(
        self,
        name: str,
        *,
        defaults: BuilderDefaults | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    def add_node(
        self,
        name: str,
        fn: NodeFn,
        *,
        capabilities: list[str] | None = None,
        security: SecurityLevel | None = None,
        audit: bool | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
    ) -> PrismalStateGraphBuilder:
        """Add a node with auto-wrap if it lacks @prismal_node.

        Args:
            name: Node identifier.
            fn: Callable async (state) → state_update.
            capabilities, security, audit, timeout_s, retry:
                Override the builder's defaults.

        Returns:
            self (fluent).

        Raises:
            ValueError: If name is already registered in this builder.
        """
        ...

    def add_supervisor_node(
        self,
        routing_fn: Callable[[AgentState], Awaitable[str]],
        *,
        valid_next: list[str],
        name: str = "supervisor",
    ) -> PrismalStateGraphBuilder:
        """Add a supervisor node with routing validation.

        Args:
            routing_fn: async (state) → next_node_name.
            valid_next: List of valid nodes as destination.
            name: Node name (default "supervisor").

        Raises:
            ValueError at runtime if routing_fn returns a name not in valid_next.
        """
        ...

    def add_security_layer(
        self,
        *,
        at: Literal["entry", "exit"] = "entry",
        sanitizer: InputSanitizer | None = None,
    ) -> PrismalStateGraphBuilder:
        """Add a dedicated sanitization node (entry or exit of the subgraph).

        Useful when there are nodes with security='off' but you want to
        guarantee sanitization at least at the subgraph boundaries.
        """
        ...

    def add_edge(self, from_: str, to: str) -> PrismalStateGraphBuilder: ...

    def add_conditional_edges(
        self,
        from_: str,
        decision_fn: Callable[[AgentState], str],
        mapping: dict[str, str],
    ) -> PrismalStateGraphBuilder: ...

    def set_entry_point(self, name: str) -> PrismalStateGraphBuilder: ...

    def compile(self) -> SubgraphDefinition:
        """Compile and return a SubgraphDefinition (registrable in SubgraphRegistry).

        Returns:
            SubgraphDefinition with name, compiled_graph, metadata.
        """
        ...

    def compile_raw(self) -> CompiledStateGraph:
        """Escape hatch: return CompiledStateGraph without wrapping in SubgraphDefinition."""
        ...
```

---

## SPEC-EXT-004: Plugin Discovery

**File:** `prismal/agents/extension/plugins.py`

### Types

```python
PluginGroup = Literal["subgraphs", "nodes", "tools", "rag_engines"]
"""Supported entry point groups:
- prismal.subgraphs: callables register_<name>(registry).
- prismal.nodes: callables decorated with @prismal_node.
- prismal.tools: LangChain BaseTool.
- prismal.rag_engines: classes with the RAGEngineProtocol protocol.
"""

@dataclass(frozen=True)
class PluginInfo:
    """Information about a discovered plugin."""
    name: str                    # entry point name
    group: PluginGroup
    module: str                  # source module
    object_name: str             # exported callable/class
    dist_name: str               # distribution package name
    dist_version: str            # package version

@dataclass(frozen=True)
class PluginLoadResult:
    info: PluginInfo
    status: Literal["loaded", "error", "skipped_by_denylist", "skipped_not_in_allowlist"]
    error: str | None = None
    duration_ms: float = 0.0

@dataclass(frozen=True)
class DiscoveryReport:
    """Aggregate report from discover_plugins()."""
    loaded: list[PluginLoadResult]
    failed: list[PluginLoadResult]
    skipped: list[PluginLoadResult]
    total_duration_ms: float

    @property
    def loaded_count(self) -> int: ...
    @property
    def failed_count(self) -> int: ...
```

### Main Function

```python
def discover_plugins(
    *,
    settings: Settings | None = None,
    registry: SubgraphRegistry | None = None,
    groups: list[PluginGroup] | None = None,
) -> DiscoveryReport:
    """Discover and install plugins from entry points.

    Iterates over the specified groups (default: all), applies
    allowlist/denylist from settings, and calls the entry point's
    callable with the correct signature for its group:

        - subgraphs: callable(registry: SubgraphRegistry) -> None
        - nodes: callable is already a @prismal_node (introspected and registered)
        - tools: callable returns a BaseTool, added to the tool_registry
        - rag_engines: callable is a RAGEngineProtocol class

    Each load is isolated in try/except; individual failures do not
    abort the rest.

    Args:
        settings: Prismal settings.
        registry: Target SubgraphRegistry. None uses the global one.
        groups: Groups to discover. None = all.

    Returns:
        DiscoveryReport with loaded/failed/skipped + total duration.

    Side effects:
        - Registers subgraphs in SubgraphRegistry.
        - Registers nodes in _REGISTERED_NODES.
        - Registers tools in tool_registry.
        - Registers rag_engines in RAGEngineRegistry (new in X4).
        - Emits OTel spans and metrics.
        - AuditLogger.log_event("plugin_loaded", ...) per load.
    """
    ...


def list_plugins(*, settings: Settings | None = None) -> list[PluginInfo]:
    """List installed plugins (without loading them)."""
    ...


def get_plugin_info(name: str) -> PluginInfo | None:
    """Detailed information about a plugin by name."""
    ...
```

---

## SPEC-EXT-005: LangChain Runnable Adapter

**File:** `prismal/agents/extension/adapters.py`

### Types

```python
from langchain_core.runnables import Runnable
from langchain_core.agents import AgentExecutor   # subset of Runnable

InputMapping = Literal["auto", "messages", "input_dict"]
"""
'auto'       — detects the Runnable's signature and maps accordingly.
'messages'   — passes state["messages"] as List[BaseMessage].
'input_dict' — passes {"input": last_user_message_content,
                     "chat_history": state["messages"][:-1]}.
"""
```

### Main Class

```python
class LangChainRunnableAdapter:
    """Converts a LangChain Runnable / AgentExecutor into a prismal node.

    Automatically maps state["messages"] to the Runnable's input, and the
    output to a valid state_update. Applies @prismal_node with standard
    security to the resulting wrapper.

    Args:
        runnable: Runnable or AgentExecutor to adapt.
        input_mapping: How to map state to the Runnable's input.
        output_key: If the Runnable returns a dict, the output key
            (default "output" for AgentExecutor, None auto-detects).
        settings: Settings.

    Example::

        from langchain.agents import AgentExecutor

        my_agent = AgentExecutor(agent=..., tools=...)
        adapter = LangChainRunnableAdapter(my_agent)
        node = adapter.as_node(name="legacy_research", capabilities=["research"])

        builder = PrismalStateGraphBuilder("hybrid")
        builder.add_node("classify", classify_fn)
        builder.add_node("legacy_research", node)
        builder.add_edge("classify", "legacy_research")
        subgraph = builder.compile()
    """

    def __init__(
        self,
        runnable: Runnable,
        *,
        input_mapping: InputMapping = "auto",
        output_key: str | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    def as_node(
        self,
        *,
        name: str,
        capabilities: list[str] | None = None,
        security: SecurityLevel = "standard",
        timeout_s: float | None = None,
    ) -> NodeFn:
        """Return an async callable (state) → state_update with @prismal_node applied.

        Args:
            name: Node name.
            capabilities: MCP capabilities.
            security: Security middleware level.
            timeout_s: Timeout.

        Returns:
            NodeFn ready for add_node().

        Raises:
            LangChainAdapterError: If the Runnable has an incompatible signature.
        """
        ...

    async def ainvoke(self, state: AgentState) -> dict:
        """Direct invocation (for tests; normally as_node() is used)."""
        ...
```

---

## SPEC-EXT-006: Ports (Hexagonal)

**File:** `prismal/agents/extension/ports.py`

```python
from typing import Protocol, runtime_checkable, Any, AsyncIterator

@runtime_checkable
class CheckpointPort(Protocol):
    """Interface for graph state persistence.

    Existing implementations that conform:
        - langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver
        - langgraph.checkpoint.postgres.aio.AsyncPostgresSaver

    Users can implement their own checkpointers (Redis,
    DynamoDB, etc.) and substitute via build_checkpointer() in config.
    """
    async def aget(self, config: dict) -> Any | None: ...
    async def aput(self, config: dict, checkpoint: Any, metadata: dict) -> None: ...
    async def alist(self, config: dict, *, limit: int | None = None,
                    before: dict | None = None) -> AsyncIterator[Any]: ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only audit log interface.

    Existing implementation that conforms: prismal.security.AuditLogger.

    Users can send audit to external systems (Splunk, Datadog,
    CloudTrail) by implementing this protocol.
    """
    def log_event(self, event_type: str, payload: dict) -> None: ...
    def log_node(self, node_name: str, session_id: str, status: str,
                 state_hash: str, duration_ms: float) -> None: ...
    def log_media(self, event: str, sha256: str, modality: str,
                  size_bytes: int, duration_s: float | None) -> None: ...


@runtime_checkable
class EmbeddingsPort(Protocol):
    """Embeddings interface.

    Existing implementations that conform:
        - langchain_core.embeddings.Embeddings (all providers).
        - prismal.rag.embeddings.EmbeddingsFactory.
    """
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def aembed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class ToolPort(Protocol):
    """Executable tool interface.

    Existing implementation that conforms: langchain_core.tools.BaseTool.
    """
    name: str
    description: str
    async def ainvoke(self, args: dict) -> Any: ...


# Helpers to validate conformance
def conforms_to(obj: Any, port: type[Protocol]) -> bool:
    """Check whether obj conforms to a Protocol structurally."""
    return isinstance(obj, port)
```

---

## SPEC-EXT-007: Middleware Chain (internal)

**File:** `prismal/agents/extension/_middleware.py`

```python
# NOTE: this module is INTERNAL (_ prefix). It is not public API;
# it is documented only for prismal maintainers.

from typing import Callable, Awaitable

Middleware = Callable[
    [NodeFn, AgentState, NodeMetadata],
    Awaitable[dict],
]
"""Signature of a middleware: receives (next_fn, state, metadata) → state_update."""


# Ordered stack (order matters: security first, error mapping last)
DEFAULT_MIDDLEWARE_STACK: list[Middleware] = [
    security_middleware,       # InputSanitizer + SecurePromptBuilder + ActionInterceptor
    otel_middleware,           # span open/close
    logger_middleware,         # contextual bind
    retry_middleware,          # retry with backoff
    timeout_middleware,        # asyncio.wait_for
    # → user fn executes here ←
    audit_middleware,          # AuditLogger.log_node
    error_mapping_middleware,  # exceptions → state_update with error=True
]


def build_pipeline(
    user_fn: NodeFn,
    metadata: NodeMetadata,
    stack: list[Middleware] | None = None,
) -> NodeFn:
    """Compose the functional pipeline by applying middlewares in reverse order."""
    ...
```

---

## SPEC-EXT-008: CLI

**File:** `prismal/plugins.py`

```python
"""CLI: python -m prismal.plugins <subcommand>

Subcommands:
    list                 — list installed plugins (without loading them).
    info <name>          — details of a plugin: version, entry points, hash.
    doctor               — attempt to load all plugins and report errors.
    enable <name>        — add to allowlist (mutate config file if available).
    disable <name>       — add to denylist.

Exit codes:
    0 — success.
    1 — general error.
    2 — plugin not found.
    3 — load error (doctor).
"""

def main(argv: list[str] | None = None) -> int: ...
```

---

## Exceptions

**File:** `prismal/core/exceptions.py` (extension)

```python
class ExtensionError(PrismalError):
    """Base for extension surface errors."""

class NodeExecutionError(ExtensionError):
    """Error captured during execution of a decorated node."""
    node_name: str
    state_keys: list[str]
    cause: BaseException

class NodeTimeoutError(NodeExecutionError):
    """Timeout during node execution."""
    timeout_s: float

class NodeValidationError(NodeExecutionError):
    """The state_update returned by the node is not valid."""

class PluginLoadError(ExtensionError):
    """Error loading a plugin."""
    plugin_name: str
    entry_point: str
    cause: BaseException

class PluginConflictError(ExtensionError):
    """Two plugins attempted to register the same name."""
    conflicting_name: str
    plugins: list[str]

class AdapterError(ExtensionError):
    """Base for adapter errors."""

class LangChainAdapterError(AdapterError):
    """Error in LangChainRunnableAdapter."""
    runnable_type: str
```

---

## Settings (extension)

**File:** `prismal/core/config.py`

```python
# Plugin discovery
plugins_autodiscover: bool = Field(
    default=True,
    description="Enables auto-discovery of plugins via entry points at startup.",
)
plugins_allowlist: list[str] = Field(
    default=[],
    description="If non-empty, only plugins in this list are loaded. "
                "Recommended in production.",
)
plugins_denylist: list[str] = Field(
    default=[],
    description="Plugins to disable. Takes precedence over allowlist.",
)
plugins_groups_enabled: list[str] = Field(
    default=["subgraphs", "nodes", "tools", "rag_engines"],
    description="Entry point groups to discover. "
                "Default: all supported.",
)

# Decorator defaults
extension_default_security: str = Field(
    default="standard",
    description="Default for @prismal_node without an explicit security.",
)
extension_default_audit: bool = Field(
    default=True,
    description="Default for @prismal_node without an explicit audit.",
)
extension_default_timeout_s: float | None = Field(
    default=None,
    description="Default timeout for @prismal_node (None = no timeout).",
)
```

Env vars:

```
PRISMAL_PLUGINS_AUTODISCOVER=true
PRISMAL_PLUGINS_ALLOWLIST=["prismal_x_finance","prismal_x_healthcare"]
PRISMAL_PLUGINS_DENYLIST=["broken_plugin"]
PRISMAL_PLUGINS_GROUPS_ENABLED=["subgraphs","nodes"]
PRISMAL_EXTENSION_DEFAULT_SECURITY=strict
PRISMAL_EXTENSION_DEFAULT_AUDIT=true
PRISMAL_EXTENSION_DEFAULT_TIMEOUT_S=30
```

---

## Plugin Author Contract

For a plugin to be discovered and loaded, its `pyproject.toml` must declare entry points in one or more of the supported groups.

### Example: plugin that contributes a subgraph

```toml
# prismal-x-healthcare/pyproject.toml
[project]
name = "prismal-x-healthcare"
version = "0.1.0"
dependencies = ["prismal>=3.0.0"]

[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare.plugin:register_healthcare_pipeline"
```

```python
# prismal_x_healthcare/plugin.py
from prismal.agents.extension import PrismalStateGraphBuilder
from prismal.langgraph import SubgraphRegistry

def register_healthcare_pipeline(registry: SubgraphRegistry) -> None:
    builder = PrismalStateGraphBuilder("healthcare_triage")
    builder.add_node("intake", intake_fn)
    builder.add_node("triage", triage_fn)
    builder.add_node("response", response_fn)
    builder.add_edge("intake", "triage")
    builder.add_edge("triage", "response")
    builder.set_entry_point("intake")
    registry.register(builder.compile())
```

### Example: plugin that contributes standalone nodes

```toml
[project.entry-points."prismal.nodes"]
medical_classifier = "prismal_x_healthcare.nodes:medical_classifier"
```

```python
# prismal_x_healthcare/nodes.py
from prismal.agents.extension import prismal_node

@prismal_node(name="medical_classifier", capabilities=["general"])
async def medical_classifier(state):
    ...
    return {"metadata": {"medical": {"category": "..."}}}
```

### Example: plugin with multiple groups

```toml
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare.plugin:register_healthcare_pipeline"

[project.entry-points."prismal.nodes"]
medical_classifier = "prismal_x_healthcare.nodes:medical_classifier"

[project.entry-points."prismal.tools"]
fhir_lookup = "prismal_x_healthcare.tools:fhir_lookup_tool"
```

---

## Compatibility and Versioning

- The extension surface is public API with a SemVer commitment.
- Breaking changes require a minor bump (pre-1.0) or major (post-1.0).
- Deprecations via `warnings.warn(DeprecationWarning)` with a minimum of 1 minor of notice.
- The internal `@frozen_api` decorator marks the public contract's functions; CI validates that their signatures do not change without an entry in `CHANGELOG.md` with justification.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — contracts for 8 extension modules |
