"""A2A v1.0 wire models (SPEC-PRT-A2A-001, amended by ADR-PRT-001 §2.1).

Pydantic v2 mirrors of the proto3-JSON shapes in ``specification/a2a.proto``
v1.0: camelCase on the wire, snake_case in Python, ``extra="allow"`` so fields
added by later minors survive a parse → dump round trip. Optional fields
default to ``None`` and are dropped with ``model_dump(by_alias=True,
exclude_none=True)`` so official payloads reproduce exactly.

Pure value objects: no network, no provider imports, safe without ``[a2a]``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from prismal.a2a.v1 import constants as c


class A2AModelV1(BaseModel):
    """Shared config: camelCase aliases, populate-by-name, unknown fields kept."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")


class TaskState(StrEnum):
    """v1 ``TaskState`` (proto enum names on the JSON wire)."""

    UNSPECIFIED = c.TASK_STATE_UNSPECIFIED
    SUBMITTED = c.TASK_STATE_SUBMITTED
    WORKING = c.TASK_STATE_WORKING
    COMPLETED = c.TASK_STATE_COMPLETED
    FAILED = c.TASK_STATE_FAILED
    CANCELED = c.TASK_STATE_CANCELED
    INPUT_REQUIRED = c.TASK_STATE_INPUT_REQUIRED
    REJECTED = c.TASK_STATE_REJECTED
    AUTH_REQUIRED = c.TASK_STATE_AUTH_REQUIRED


class Role(StrEnum):
    """v1 ``Role``."""

    USER = c.ROLE_USER
    AGENT = c.ROLE_AGENT


# ── content ──────────────────────────────────────────────────────────────────

_PART_CONTENT = ("text", "raw", "url", "data")


class PartV1(A2AModelV1):
    """A content part — ``oneof content {text, raw (base64), url, data}``; no ``kind``."""

    text: str | None = None
    raw: str | None = None
    url: str | None = None
    data: Any = None
    media_type: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one_content(self) -> PartV1:
        present = [f for f in _PART_CONTENT if getattr(self, f) is not None]
        if len(present) != 1:
            raise ValueError(f"Part needs exactly one of {_PART_CONTENT}, got {present}")
        return self


class MessageV1(A2AModelV1):
    """One turn. ``tenant`` lives on request params, not here."""

    message_id: str
    role: Role
    parts: list[PartV1]
    context_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None
    reference_task_ids: list[str] | None = None


class TaskStatusV1(A2AModelV1):
    """Task status: the ``state`` plus an optional status ``message`` and timestamp."""

    state: TaskState
    message: MessageV1 | None = None
    timestamp: str | None = None


class ArtifactV1(A2AModelV1):
    """An output artifact of a task: an id and its content ``parts``."""

    artifact_id: str
    parts: list[PartV1]
    name: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None


class TaskV1(A2AModelV1):
    """A v1 task: id, ``contextId``, status, optional artifacts and message history."""

    id: str
    context_id: str
    status: TaskStatusV1
    artifacts: list[ArtifactV1] | None = None
    history: list[MessageV1] | None = None
    metadata: dict[str, Any] | None = None


class TaskStatusUpdateEvent(A2AModelV1):
    """Streaming event: the status of task ``task_id`` changed."""

    task_id: str
    context_id: str
    status: TaskStatusV1
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(A2AModelV1):
    """Streaming event: an artifact, or a chunk of it (``append``/``lastChunk``), was produced."""

    task_id: str
    context_id: str
    artifact: ArtifactV1
    append: bool | None = None
    last_chunk: bool | None = None
    metadata: dict[str, Any] | None = None


# ── requests / responses (JSON-RPC ``params`` / ``result``) ─────────────────


class SendMessageConfiguration(A2AModelV1):
    """Per-request options of ``SendMessage`` (output modes, history length, blocking)."""

    accepted_output_modes: list[str] | None = None
    history_length: int | None = None
    blocking: bool | None = None
    push_notification_config: dict[str, Any] | None = None


class SendMessageRequest(A2AModelV1):
    """``SendMessage`` params: the message plus optional tenant and configuration."""

    message: MessageV1
    tenant: str | None = None
    configuration: SendMessageConfiguration | None = None
    metadata: dict[str, Any] | None = None


class SendMessageResponse(A2AModelV1):
    """``oneof payload {task, message}``."""

    task: TaskV1 | None = None
    message: MessageV1 | None = None


class StreamResponse(A2AModelV1):
    """``oneof payload {task, message, statusUpdate, artifactUpdate}``."""

    task: TaskV1 | None = None
    message: MessageV1 | None = None
    status_update: TaskStatusUpdateEvent | None = None
    artifact_update: TaskArtifactUpdateEvent | None = None


class GetTaskRequest(A2AModelV1):
    """``GetTask`` params: task id, optional tenant and history length."""

    id: str
    tenant: str | None = None
    history_length: int | None = None


class CancelTaskRequest(A2AModelV1):
    """``CancelTask`` params: task id plus optional tenant and metadata."""

    id: str
    tenant: str | None = None
    metadata: dict[str, Any] | None = None


class ListTasksRequest(A2AModelV1):
    """``ListTasks`` params. ``pageSize`` default 50, clamped server-side to ≤ 100."""

    tenant: str | None = None
    context_id: str | None = None
    status: TaskState | None = None
    page_size: int | None = Field(default=None, ge=1)
    page_token: str | None = None
    history_length: int | None = None
    status_timestamp_after: str | None = None
    include_artifacts: bool | None = None


ListTasksParams = ListTasksRequest


class ListTasksResult(A2AModelV1):
    """All four fields are required; the last page has ``nextPageToken == ""``."""

    tasks: list[TaskV1]
    next_page_token: str
    page_size: int
    total_size: int


# ── Agent Card ───────────────────────────────────────────────────────────────


class AgentInterface(A2AModelV1):
    """One endpoint of the agent: URL, protocol binding and protocol version."""

    url: str
    protocol_binding: str
    protocol_version: str
    tenant: str | None = None


class AgentProvider(A2AModelV1):
    """The organization that provides the agent."""

    organization: str
    url: str


class AgentExtension(A2AModelV1):
    """A protocol extension the agent supports (by ``uri``)."""

    uri: str
    description: str | None = None
    required: bool | None = None
    params: dict[str, Any] | None = None


class AgentCapabilities(A2AModelV1):
    """Optional features the agent supports (streaming, push, extensions)."""

    streaming: bool | None = None
    push_notifications: bool | None = None
    extensions: list[AgentExtension] | None = None
    extended_agent_card: bool | None = None


class StringList(A2AModelV1):
    """proto ``StringList {repeated string list}`` (Python name ``values``)."""

    values: list[str] = Field(default_factory=list, alias="list")


class SecurityRequirement(A2AModelV1):
    """Scheme name → required scopes, as listed on the card or a skill."""

    schemes: dict[str, StringList] = Field(default_factory=dict)


class AuthorizationCodeOAuthFlow(A2AModelV1):
    """OAuth 2.0 authorization-code flow (optionally PKCE-required)."""

    authorization_url: str
    token_url: str
    scopes: dict[str, str] = Field(default_factory=dict)
    refresh_url: str | None = None
    pkce_required: bool | None = None


class ClientCredentialsOAuthFlow(A2AModelV1):
    """OAuth 2.0 client-credentials flow."""

    token_url: str
    scopes: dict[str, str] = Field(default_factory=dict)
    refresh_url: str | None = None


class DeviceCodeOAuthFlow(A2AModelV1):
    """OAuth 2.0 device-authorization flow."""

    device_authorization_url: str
    token_url: str
    scopes: dict[str, str] = Field(default_factory=dict)
    refresh_url: str | None = None


class OAuthFlows(A2AModelV1):
    """``oneof flow {authorizationCode, clientCredentials, deviceCode}``."""

    authorization_code: AuthorizationCodeOAuthFlow | None = None
    client_credentials: ClientCredentialsOAuthFlow | None = None
    device_code: DeviceCodeOAuthFlow | None = None


class APIKeySecurityScheme(A2AModelV1):
    """API key passed in ``location`` (header/query/cookie) under ``name``."""

    location: str
    name: str
    description: str | None = None


class HTTPAuthSecurityScheme(A2AModelV1):
    """HTTP authentication scheme (e.g. ``Bearer``)."""

    scheme: str
    bearer_format: str | None = None
    description: str | None = None


class OAuth2SecurityScheme(A2AModelV1):
    """OAuth 2.0 security scheme with its supported flows."""

    flows: OAuthFlows
    description: str | None = None
    oauth2_metadata_url: str | None = None


class OpenIdConnectSecurityScheme(A2AModelV1):
    """OpenID Connect security scheme (discovery URL)."""

    open_id_connect_url: str
    description: str | None = None


class MutualTlsSecurityScheme(A2AModelV1):
    """Mutual TLS client authentication."""

    description: str | None = None


class SecuritySchemeV1(A2AModelV1):
    """``oneof scheme`` — exactly one member is set on the wire."""

    api_key_security_scheme: APIKeySecurityScheme | None = None
    http_auth_security_scheme: HTTPAuthSecurityScheme | None = None
    oauth2_security_scheme: OAuth2SecurityScheme | None = None
    open_id_connect_security_scheme: OpenIdConnectSecurityScheme | None = None
    mtls_security_scheme: MutualTlsSecurityScheme | None = None


class AgentSkillV1(A2AModelV1):
    """One skill advertised on the v1 Agent Card."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security_requirements: list[SecurityRequirement] | None = None


class AgentCardV1(A2AModelV1):
    """v1 Agent Card: no top-level ``url``/``protocolVersion`` — see ``supportedInterfaces``."""

    name: str
    description: str
    supported_interfaces: list[AgentInterface]
    version: str
    capabilities: AgentCapabilities
    default_input_modes: list[str]
    default_output_modes: list[str]
    skills: list[AgentSkillV1]
    provider: AgentProvider | None = None
    documentation_url: str | None = None
    security_schemes: dict[str, SecuritySchemeV1] | None = None
    security_requirements: list[SecurityRequirement] | None = None
    signatures: list[dict[str, Any]] | None = None
    icon_url: str | None = None


__all__ = [
    "A2AModelV1",
    "APIKeySecurityScheme",
    "AgentCapabilities",
    "AgentCardV1",
    "AgentExtension",
    "AgentInterface",
    "AgentProvider",
    "AgentSkillV1",
    "ArtifactV1",
    "AuthorizationCodeOAuthFlow",
    "CancelTaskRequest",
    "ClientCredentialsOAuthFlow",
    "DeviceCodeOAuthFlow",
    "GetTaskRequest",
    "HTTPAuthSecurityScheme",
    "ListTasksParams",
    "ListTasksRequest",
    "ListTasksResult",
    "MessageV1",
    "MutualTlsSecurityScheme",
    "OAuth2SecurityScheme",
    "OAuthFlows",
    "OpenIdConnectSecurityScheme",
    "PartV1",
    "Role",
    "SecurityRequirement",
    "SecuritySchemeV1",
    "SendMessageConfiguration",
    "SendMessageRequest",
    "SendMessageResponse",
    "StreamResponse",
    "StringList",
    "TaskArtifactUpdateEvent",
    "TaskState",
    "TaskStatusUpdateEvent",
    "TaskStatusV1",
    "TaskV1",
]
