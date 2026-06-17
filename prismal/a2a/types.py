"""A2A domain types — Phase I (SPEC-A2A-001).

Pydantic v2 models compliant with the A2A Protocol v0.3.x. The wire format is
JSON with **camelCase** keys (``protocolVersion``, ``inputModes``,
``messageId``, …); the Python attributes stay snake_case. Every model accepts
either form on input (``populate_by_name``) and serializes camelCase with
``model_dump(by_alias=True)``.

These are pure value objects — no network, no provider imports. They are safe
to import without the ``[a2a]`` extra.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.alias_generators import to_camel

PROTOCOL_VERSION = "0.3.0"

TaskStatus = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "failed",
    "canceled",
]


class _A2ABase(BaseModel):
    """Shared config: camelCase aliases, populate-by-name, strict extras off."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class A2APart(_A2ABase):
    """A single content part of a message or artifact (``text`` | ``file`` | ``data``)."""

    kind: Literal["text", "file", "data"]
    text: str | None = None
    data: dict[str, Any] | None = None
    file: dict[str, Any] | None = None


class AgentSkill(_A2ABase):
    """A discrete capability published on the Agent Card."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])


class AgentCard(_A2ABase):
    """The Agent Card served at ``/.well-known/agent-card.json``."""

    name: str
    description: str
    url: str
    version: str
    protocol_version: str = PROTOCOL_VERSION
    skills: list[AgentSkill] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=lambda: {"streaming": True})
    security_schemes: dict[str, Any] = Field(default_factory=dict)
    provider: dict[str, str] | None = None
    did: str | None = None


class A2AMessage(_A2ABase):
    """A turn in an A2A task conversation."""

    role: Literal["user", "agent"]
    parts: list[A2APart] = Field(default_factory=list)
    message_id: str


class A2AArtifact(_A2ABase):
    """A produced output of an A2A task."""

    artifact_id: str
    parts: list[A2APart] = Field(default_factory=list)


class A2ATask(_A2ABase):
    """The stateful unit of work exchanged over A2A."""

    id: str
    status: TaskStatus
    history: list[A2AMessage] = Field(default_factory=list)
    artifacts: list[A2AArtifact] = Field(default_factory=list)


class A2AAuth(_A2ABase):
    """Outbound auth configuration for a remote A2A agent (SPEC-A2A-006).

    ``client_secret`` is a :class:`~pydantic.SecretStr` so it never leaks into
    logs or ``repr``.
    """

    scheme: Literal["none", "oauth2_client_credentials", "mtls", "bearer"] = "none"
    token_url: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    bearer_token: SecretStr | None = None
    cert_path: str | None = None
    key_path: str | None = None
    did: str | None = None


__all__ = [
    "PROTOCOL_VERSION",
    "A2AArtifact",
    "A2AAuth",
    "A2AMessage",
    "A2APart",
    "A2ATask",
    "AgentCard",
    "AgentSkill",
    "TaskStatus",
]
