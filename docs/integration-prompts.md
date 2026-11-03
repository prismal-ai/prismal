# Integration prompts — prismal (engine)

Part of the cross-project integration map (2026-10-31): #1 AG-UI prismal → agentkit,
**#2 OTel GenAI → agentlens**, #3 ACP/A2A Umbral ⇄ prismal, #4 JSON-RPC Umbral → xtermjs,
**#5 shared MCP host (mcphost)**. The engine touches #2 and #5; #1 and #3 live in
`prismal-server/docs/integration-prompts.md` because the engine stays a pure library.

Counterparts: `reflex-agentlens/docs/integration-prompts.md`, `reflex-mcphost/docs/integration-prompts.md`,
`umbral/docs/integration-prompts.md`.

---

## Prompt I2-A · OTel GenAI semantic conventions on every span

```
You are working in prismal (Python 3.13, namespace package, opt-in layers, graph
byte-for-byte unchanged when a feature is off). Read docs/observability-integration.md,
prismal/monitoring/otel.py, the ObservabilityPort (v3.9.0) and the OpenTelemetry GenAI
semantic conventions at their current version (they are still "in development":
pin the version you implement in a constant and in CHANGELOG).

Goal: make prismal's traces consumable by any OTel GenAI backend, reflex-agentlens
first, without changing what Langfuse/LangSmith receive today.

Scope:
1. Attribute mapping: every LLM call span carries gen_ai.operation.name,
   gen_ai.provider.name, gen_ai.request.model, gen_ai.response.model,
   gen_ai.usage.input_tokens / output_tokens, gen_ai.response.finish_reasons;
   every tool call span carries gen_ai.operation.name=execute_tool,
   gen_ai.tool.name, gen_ai.tool.call.id; agent/supervisor spans carry
   gen_ai.agent.name and the route chosen. Cost from CostMeter goes on a
   prismal.* namespaced attribute, never on a gen_ai.* one not in the spec.
2. Span tree: run → supervisor turn → route → (llm | tool | subagent)*, with
   the run_id, thread_id and tenant as resource/span attributes so a UI can
   group by conversation. Sub-agents (Skynet workers, parallel researcher)
   are child spans with gen_ai.agent.name of the worker.
3. Content capture is OFF by default (prompts and completions are PII):
   `otel_capture_content` setting, documented, following the semconv opt-in
   attribute names when on.
4. Export: unchanged OTLP exporter; add an `otel_export_fixture` dev helper
   that writes a run's spans to JSON so agentlens can test against a real
   prismal trace without running prismal.
5. Tests: one test per span kind asserting the attribute set; a snapshot test
   of the exported fixture; the "graph unchanged when off" test.

Acceptance (EARS):
- WHILE otel is disabled THE SYSTEM SHALL add zero overhead and zero attributes.
- THE SYSTEM SHALL keep the existing Langfuse/LangSmith adapters' output unchanged.
- IF the semconv version pinned here is bumped THEN CHANGELOG SHALL state which
  attributes were renamed.

Out of scope: metrics and logs signals (traces only); agentlens itself.
```

## Prompt I5-A · Reuse a shared MCP server config and consent policy

```
You are working in prismal. Read prismal/mcp/ (client, connection, tool_cache,
servers/), the PermissionManager and ActionInterceptor docs, and, in the
reflex-mcphost repository, specs/prd/mcphost-v0.1.md and specs/data-model/mcphost-schema.md
(server config format, consent policy format, audit record).

Goal: let prismal's MCP client read the same server configuration file that
reflex-mcphost (and Claude Desktop / Cursor / VS Code) use, and optionally honour
an exported mcphost consent policy, so that one config and one policy serve the
engine, the host and the terminal (Umbral reads the same file).

Scope:
1. A config loader for the standard `mcpServers` JSON shape (stdio and
   Streamable HTTP), merged with prismal's own settings; precedence documented.
2. A `ConsentPolicyPort` with one implementation that reads mcphost's exported
   policy file (allow / ask / deny per server+tool, with audit) and a default
   implementation that keeps today's behaviour. "ask" inside a headless engine
   run resolves to the existing interrupt mechanism, so a UI (agentkit) or a
   terminal (Umbral) can answer.
3. Audit: when a policy decides, append the decision to prismal's hash-chained
   AuditLogger with the policy's rule id, so mcphost's audit and prismal's
   audit can be correlated.
4. Tests: config fixtures copied from mcphost's test suite; one test per
   decision kind.

Acceptance (EARS):
- WHEN the shared config names a server prismal cannot start THE SYSTEM SHALL
  skip it with a logged reason and continue with the others.
- IF a policy denies a tool THEN THE SYSTEM SHALL not call it and SHALL record
  the denial.

Out of scope: sharing code with mcphost or Umbral (file formats only); OAuth
flows (mcphost owns them).
```
