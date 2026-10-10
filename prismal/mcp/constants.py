"""MCP protocol names pinned for Phase PRT (ADR-PRT-001 §2.2).

Every wire name used by the stateless adapter, the tool-list cache and the tasks
extension lives here — and only here — so a protocol revision touches one module.
The legacy path (``mcp`` SDK 1.28.1 ``ClientSession``) does not use these names.
"""

from __future__ import annotations

from typing import Final

# ── Versions ─────────────────────────────────────────────────────────────────
STATELESS_PROTOCOL_VERSION: Final = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS: Final = ("2025-11-25", "2025-06-18")
LEGACY_PROTOCOL_VERSION: Final = LEGACY_PROTOCOL_VERSIONS[0]

# ── Methods ──────────────────────────────────────────────────────────────────
METHOD_INITIALIZE: Final = "initialize"
METHOD_SERVER_DISCOVER: Final = "server/discover"
METHOD_TOOLS_LIST: Final = "tools/list"
METHOD_TOOLS_CALL: Final = "tools/call"
METHOD_ELICITATION_CREATE: Final = "elicitation/create"
METHOD_SAMPLING_CREATE_MESSAGE: Final = "sampling/createMessage"
METHOD_ROOTS_LIST: Final = "roots/list"

# ── Per-request ``_meta`` keys (stateless wire) ─────────────────────────────
META_PROTOCOL_VERSION: Final = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES: Final = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO: Final = "io.modelcontextprotocol/clientInfo"

# ── HTTP headers (stateless wire) ────────────────────────────────────────────
HEADER_PROTOCOL_VERSION: Final = "MCP-Protocol-Version"
HEADER_METHOD: Final = "Mcp-Method"
HEADER_NAME: Final = "Mcp-Name"

# ── Cache hints on list results ──────────────────────────────────────────────
CACHE_SCOPE_PUBLIC: Final = "public"
CACHE_SCOPE_PRIVATE: Final = "private"
#: MCP ``cacheScope`` → prismal :data:`~prismal.mcp.protocol.CacheScope`.
CACHE_SCOPE_MAP: Final = {CACHE_SCOPE_PUBLIC: "shared", CACHE_SCOPE_PRIVATE: "principal"}

# ── ``resultType`` values (missing ⇒ complete) ───────────────────────────────
RESULT_TYPE_COMPLETE: Final = "complete"
RESULT_TYPE_INPUT_REQUIRED: Final = "input_required"
RESULT_TYPE_TASK: Final = "task"

# ── Tasks extension (SEP-2663) ───────────────────────────────────────────────
TASKS_EXTENSION_ID: Final = "io.modelcontextprotocol/tasks"
METHOD_TASKS_GET: Final = "tasks/get"
METHOD_TASKS_UPDATE: Final = "tasks/update"
METHOD_TASKS_CANCEL: Final = "tasks/cancel"
NOTIFICATION_TASKS: Final = "notifications/tasks"

TASK_STATUS_WORKING: Final = "working"
TASK_STATUS_INPUT_REQUIRED: Final = "input_required"
TASK_STATUS_COMPLETED: Final = "completed"
TASK_STATUS_FAILED: Final = "failed"
TASK_STATUS_CANCELLED: Final = "cancelled"
TERMINAL_TASK_STATUSES: Final = frozenset(
    {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}
)

#: ``Tool.execution.taskSupport`` value marking a task-only tool.
TASK_SUPPORT_REQUIRED: Final = "required"

# ── JSON-RPC error codes ─────────────────────────────────────────────────────
ERROR_METHOD_NOT_FOUND: Final = -32601
ERROR_HEADER_MISMATCH: Final = -32020
ERROR_MISSING_CLIENT_CAPABILITY: Final = -32021
ERROR_UNSUPPORTED_PROTOCOL_VERSION: Final = -32022
ERROR_URL_ELICITATION_REQUIRED: Final = -32042

__all__ = [
    "CACHE_SCOPE_MAP",
    "CACHE_SCOPE_PRIVATE",
    "CACHE_SCOPE_PUBLIC",
    "ERROR_HEADER_MISMATCH",
    "ERROR_METHOD_NOT_FOUND",
    "ERROR_MISSING_CLIENT_CAPABILITY",
    "ERROR_UNSUPPORTED_PROTOCOL_VERSION",
    "ERROR_URL_ELICITATION_REQUIRED",
    "HEADER_METHOD",
    "HEADER_NAME",
    "HEADER_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSIONS",
    "META_CLIENT_CAPABILITIES",
    "META_CLIENT_INFO",
    "META_PROTOCOL_VERSION",
    "METHOD_ELICITATION_CREATE",
    "METHOD_INITIALIZE",
    "METHOD_ROOTS_LIST",
    "METHOD_SAMPLING_CREATE_MESSAGE",
    "METHOD_SERVER_DISCOVER",
    "METHOD_TASKS_CANCEL",
    "METHOD_TASKS_GET",
    "METHOD_TASKS_UPDATE",
    "METHOD_TOOLS_CALL",
    "METHOD_TOOLS_LIST",
    "NOTIFICATION_TASKS",
    "RESULT_TYPE_COMPLETE",
    "RESULT_TYPE_INPUT_REQUIRED",
    "RESULT_TYPE_TASK",
    "STATELESS_PROTOCOL_VERSION",
    "TASKS_EXTENSION_ID",
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_INPUT_REQUIRED",
    "TASK_STATUS_WORKING",
    "TASK_SUPPORT_REQUIRED",
    "TERMINAL_TASK_STATUSES",
]
