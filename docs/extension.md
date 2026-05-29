# Prismal Extension Surface (Fase X)

The extension surface lets you build LangGraph nodes, subgraphs, and plugins on
top of prismal **without forking the repo**. It is opt-in and additive: existing
nodes/subgraphs are unaffected.

All public symbols are importable from `prismal.agents.extension` (plus the
LangGraph re-export at `prismal.langgraph`). Runnable copies of every snippet
below live under [`examples/extension/`](../examples/extension/).

---

## 1. Quickstart — a custom node in < 15 minutes

```python
from prismal.agents.extension import prismal_node

@prismal_node(name="sentiment_classifier", capabilities=["general"])
async def sentiment_classifier(state):
    text = state["messages"][-1].content.lower()
    label = "positive" if "love" in text else "neutral"
    return {"metadata": {"sentiment_classifier": {"label": label}}}
```

`@prismal_node` wraps your `async (state) -> state_update` function with a
middleware chain (security → tracing → logging → retry → timeout → audit →
error mapping) and registers its metadata + capabilities. The returned callable
carries `__prismal_node__` (its `NodeMetadata`) and `__wrapped__` (your
original function).

### Decorator options

| Argument | Default | Purpose |
|----------|---------|---------|
| `name` | function name | Node name + audit/trace label |
| `capabilities` | `None` | MCP capabilities; registered in `DEFAULT_CAPABILITY_MAP` |
| `security` | `"standard"` | `"off"` / `"standard"` (sanitize input) / `"strict"` (+ tool-call permission check) |
| `audit` | `True` | Emit `AuditLogger.log_node` per invocation |
| `retry` | `None` | `RetryPolicy(max_attempts, backoff_s, retry_on)` |
| `timeout_s` | `None` | Per-invocation `asyncio.wait_for` timeout → `NodeTimeoutError` |
| `raise_on_error` | `False` | If `True`, propagate `NodeExecutionError`; else return `{"metadata": {"error": {...}}}` |

> **Middleware order note.** The chain is, outermost→innermost:
> `error_mapping → otel → logger → security → audit → retry → timeout → user fn`.
> This is a deliberate clarification of the SPEC's contradictory listing, chosen
> so retries run before the error is mapped and the audit duration spans all
> attempts.

---

## 2. Recipes — building subgraphs

```python
from prismal.agents.extension import PrismalStateGraphBuilder

builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn, capabilities=["general"])  # auto-@prismal_node
builder.add_node("respond", respond_fn)
builder.add_edge("classify", "respond")
builder.add_edge("respond", "__end__")          # "__end__" → LangGraph END
builder.set_entry_point("classify")

subgraph = builder.compile()        # -> SubgraphDefinition (register in SubgraphRegistry)
# compiled = builder.compile_raw()  # -> CompiledStateGraph (escape hatch)
```

- `add_node` auto-wraps plain callables with `@prismal_node` using the builder
  `defaults` (override per node via keyword args). Pre-decorated callables are
  used unchanged.
- `add_supervisor_node(routing_fn, valid_next=[...])` adds a router that raises
  `ValueError` at runtime if it routes outside `valid_next`.
- `add_security_layer(at="entry"|"exit")` inserts a dedicated sanitisation node
  at the subgraph border.
- `add_conditional_edges(from_, decision_fn, mapping)` for branching.

---

## 3. Plugin lifecycle

Plugins are ordinary Python distributions that declare **entry points**. After
`pip install`, `discover_plugins()` finds and registers them.

```toml
# prismal-x-healthcare/pyproject.toml
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare.plugin:register_healthcare_pipeline"
```

```python
from prismal.agents.extension import discover_plugins

report = discover_plugins()   # reads settings for allow/deny + enabled groups
print(report.loaded_count, report.failed_count, report.skipped_count)
```

### Groups & contracts

| Group | Export | Contract |
|-------|--------|----------|
| `prismal.subgraphs` | `register(registry)` | self-register via `registry.register_sync(...)` **or** return a `SubgraphDefinition` |
| `prismal.nodes` | a `@prismal_node` callable | importing it registers it |
| `prismal.tools` | a `BaseTool` or zero-arg factory | added to the plugin tool pool (respects the 120-tool cap) |
| `prismal.rag_engines` | a RAG engine class | registered in `RAGEngineRegistry` |

Each plugin loads in isolation — one failure never aborts the rest; failures are
collected in `DiscoveryReport.failed`.

### Settings (env vars)

```
PRISMAL_PLUGINS_AUTODISCOVER=true
PRISMAL_PLUGINS_ALLOWLIST=["prismal_x_finance"]   # if set, only these load
PRISMAL_PLUGINS_DENYLIST=["broken_plugin"]        # takes precedence over allowlist
PRISMAL_PLUGINS_GROUPS_ENABLED=["subgraphs","nodes"]
PRISMAL_EXTENSION_DEFAULT_SECURITY=standard
```

### CLI

```bash
python -m prismal.plugins list           # installed plugins (no loading)
python -m prismal.plugins info <name>    # details
python -m prismal.plugins doctor         # try loading all; exit 3 on any failure
python -m prismal.plugins enable <name>  # prints the allowlist env-var change
python -m prismal.plugins disable <name> # prints the denylist env-var change
```

A starter package lives in [`examples/plugin_template/`](../examples/plugin_template/).

---

## 4. LangChain migration

Wrap any existing `Runnable` / `AgentExecutor` as a prismal node:

```python
from prismal.agents.extension import LangChainRunnableAdapter

adapter = LangChainRunnableAdapter(my_agent_executor)   # input_mapping="auto"
node = adapter.as_node(name="legacy_research", capabilities=["research"])
builder.add_node("legacy_research", node)
```

Input mapping: `"messages"` passes `state["messages"]`; `"input_dict"` passes
`{"input": last_user_msg, "chat_history": prior_msgs}`; `"auto"` picks
`input_dict` for AgentExecutor-like runnables (those exposing `input_keys`),
else `messages`. Output is mapped from `AIMessage` / `str` / `dict`
(`output_key`) back to `{"messages": [...]}`.

---

## 5. Ports & adapters (hexagonal)

`prismal.agents.extension.ports` declares `@runtime_checkable` Protocols you can
substitute your own implementations for:

| Port | Conforming implementation |
|------|---------------------------|
| `CheckpointPort` | `AsyncSqliteSaver`, `AsyncPostgresSaver` |
| `AuditPort` | `prismal.security.AuditLogger` |
| `EmbeddingsPort` | any `langchain_core.embeddings.Embeddings` |
| `ToolPort` | `langchain_core.tools.BaseTool` |

```python
from prismal.agents.extension import conforms_to, CheckpointPort

assert conforms_to(my_redis_saver, CheckpointPort)   # structural, no base class
```

---

## 6. Version compatibility

Import LangGraph symbols from `prismal.langgraph` (not `langgraph.*` directly):

```python
from prismal.langgraph import StateGraph, START, END, Send, add_messages, VERSION
```

`prismal.langgraph.VERSION` is the LangGraph version prismal was tested against.
