"""A2A protocol names — the single source of truth (ADR-PRT-001 §2.1).

Every A2A protocol name (JSON-RPC methods, the version header, task states,
roles, error codes, bindings, version strings) used by the dual-stack layer
lives here, pinned from ``a2aproject/A2A`` ``specification/a2a.proto`` v1.0.0
(2026-03-12; v1.0.1 changed comments only) and the v0.3.x JSON-RPC names.
"""

from __future__ import annotations

from typing import Final, Literal

A2AProtocolVersion = Literal["0.3", "1.0"]

# ── versions ────────────────────────────────────────────────────────────────
A2A_V1_PROTOCOL_VERSION: Final = "1.0"
A2A_LEGACY_PROTOCOL_VERSION: Final = "0.3"
SUPPORTED_VERSIONS: Final = frozenset({A2A_V1_PROTOCOL_VERSION, A2A_LEGACY_PROTOCOL_VERSION})

# ``A2A-Version: <Major.Minor>`` (also ``?A2A-Version=``); empty ⇒ 0.3.
VERSION_HEADER: Final = "A2A-Version"
CONTENT_TYPE: Final = "application/a2a+json"

# ── v1 JSON-RPC methods (PascalCase) ───────────────────────────────────────
METHOD_SEND_MESSAGE: Final = "SendMessage"
METHOD_SEND_STREAMING_MESSAGE: Final = "SendStreamingMessage"
METHOD_GET_TASK: Final = "GetTask"
METHOD_LIST_TASKS: Final = "ListTasks"
METHOD_CANCEL_TASK: Final = "CancelTask"
METHOD_SUBSCRIBE_TO_TASK: Final = "SubscribeToTask"
METHOD_CREATE_PUSH_CONFIG: Final = "CreateTaskPushNotificationConfig"
METHOD_GET_PUSH_CONFIG: Final = "GetTaskPushNotificationConfig"
METHOD_LIST_PUSH_CONFIGS: Final = "ListTaskPushNotificationConfigs"
METHOD_DELETE_PUSH_CONFIG: Final = "DeleteTaskPushNotificationConfig"
METHOD_GET_EXTENDED_AGENT_CARD: Final = "GetExtendedAgentCard"

V1_METHODS: Final = frozenset(
    {
        METHOD_SEND_MESSAGE,
        METHOD_SEND_STREAMING_MESSAGE,
        METHOD_GET_TASK,
        METHOD_LIST_TASKS,
        METHOD_CANCEL_TASK,
        METHOD_SUBSCRIBE_TO_TASK,
        METHOD_CREATE_PUSH_CONFIG,
        METHOD_GET_PUSH_CONFIG,
        METHOD_LIST_PUSH_CONFIGS,
        METHOD_DELETE_PUSH_CONFIG,
        METHOD_GET_EXTENDED_AGENT_CARD,
    }
)

# ── v0.3 JSON-RPC methods (slash names) ────────────────────────────────────
LEGACY_METHOD_SEND: Final = "message/send"
LEGACY_METHOD_STREAM: Final = "message/stream"
LEGACY_METHOD_GET: Final = "tasks/get"
LEGACY_METHOD_CANCEL: Final = "tasks/cancel"
LEGACY_METHOD_RESUBSCRIBE: Final = "tasks/resubscribe"
LEGACY_METHOD_PUSH_SET: Final = "tasks/pushNotificationConfig/set"
LEGACY_METHOD_PUSH_GET: Final = "tasks/pushNotificationConfig/get"
LEGACY_METHOD_PUSH_LIST: Final = "tasks/pushNotificationConfig/list"
LEGACY_METHOD_PUSH_DELETE: Final = "tasks/pushNotificationConfig/delete"
LEGACY_METHOD_EXTENDED_CARD: Final = "agent/getAuthenticatedExtendedCard"

LEGACY_METHODS: Final = frozenset(
    {
        LEGACY_METHOD_SEND,
        LEGACY_METHOD_STREAM,
        LEGACY_METHOD_GET,
        LEGACY_METHOD_CANCEL,
        LEGACY_METHOD_RESUBSCRIBE,
        LEGACY_METHOD_PUSH_SET,
        LEGACY_METHOD_PUSH_GET,
        LEGACY_METHOD_PUSH_LIST,
        LEGACY_METHOD_PUSH_DELETE,
        LEGACY_METHOD_EXTENDED_CARD,
    }
)

# ── TaskState / Role ───────────────────────────────────────────────────────
TASK_STATE_UNSPECIFIED: Final = "TASK_STATE_UNSPECIFIED"
TASK_STATE_SUBMITTED: Final = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING: Final = "TASK_STATE_WORKING"
TASK_STATE_COMPLETED: Final = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED: Final = "TASK_STATE_FAILED"
TASK_STATE_CANCELED: Final = "TASK_STATE_CANCELED"
TASK_STATE_INPUT_REQUIRED: Final = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_REJECTED: Final = "TASK_STATE_REJECTED"
TASK_STATE_AUTH_REQUIRED: Final = "TASK_STATE_AUTH_REQUIRED"

TERMINAL_STATES: Final = frozenset(
    {TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELED, TASK_STATE_REJECTED}
)

# v0.3 lowercase ⇄ v1. The legacy names of the v1-only states exist in v0.3's
# spec but not in prismal's internal ``TaskStatus`` literal (see convert.py).
LEGACY_TO_V1_STATE: Final[dict[str, str]] = {
    "submitted": TASK_STATE_SUBMITTED,
    "working": TASK_STATE_WORKING,
    "completed": TASK_STATE_COMPLETED,
    "failed": TASK_STATE_FAILED,
    "canceled": TASK_STATE_CANCELED,
    "input-required": TASK_STATE_INPUT_REQUIRED,
    "rejected": TASK_STATE_REJECTED,
    "auth-required": TASK_STATE_AUTH_REQUIRED,
    "unknown": TASK_STATE_UNSPECIFIED,
}
V1_TO_LEGACY_STATE: Final[dict[str, str]] = {v: k for k, v in LEGACY_TO_V1_STATE.items()}

ROLE_USER: Final = "ROLE_USER"
ROLE_AGENT: Final = "ROLE_AGENT"
LEGACY_TO_V1_ROLE: Final[dict[str, str]] = {"user": ROLE_USER, "agent": ROLE_AGENT}
V1_TO_LEGACY_ROLE: Final[dict[str, str]] = {v: k for k, v in LEGACY_TO_V1_ROLE.items()}

# ── protocol bindings ──────────────────────────────────────────────────────
BINDING_JSONRPC: Final = "JSONRPC"
BINDING_GRPC: Final = "GRPC"
BINDING_HTTP_JSON: Final = "HTTP+JSON"

# ── JSON-RPC error codes (A2A -32001..-32009 + JSON-RPC standard) ──────────
ERR_TASK_NOT_FOUND: Final = -32001
ERR_TASK_NOT_CANCELABLE: Final = -32002
ERR_PUSH_NOTIFICATION_NOT_SUPPORTED: Final = -32003
ERR_UNSUPPORTED_OPERATION: Final = -32004
ERR_CONTENT_TYPE_NOT_SUPPORTED: Final = -32005
ERR_INVALID_AGENT_RESPONSE: Final = -32006
ERR_EXTENDED_AGENT_CARD_NOT_CONFIGURED: Final = -32007
ERR_EXTENSION_SUPPORT_REQUIRED: Final = -32008
ERR_VERSION_NOT_SUPPORTED: Final = -32009
ERR_INVALID_PARAMS: Final = -32602
ERR_METHOD_NOT_FOUND: Final = -32601

# ── ListTasks bounds ───────────────────────────────────────────────────────
LIST_TASKS_DEFAULT_PAGE_SIZE: Final = 50
LIST_TASKS_MAX_PAGE_SIZE: Final = 100

WELL_KNOWN_CARD_PATH: Final = "/.well-known/agent-card.json"
