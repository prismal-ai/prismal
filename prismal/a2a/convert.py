"""v1 ⇄ internal codecs (DD-PRT-002: A2A v1 is an edge codec over the v0.3 model).

The internal canonical shapes stay :mod:`prismal.a2a.types` (v0.3) until
``4.0.0``; v1 payloads are decoded here at the edge, run through the unchanged
``_run_task`` (sanitize → graph → audit) and re-encoded.

Lossy fields (documented, intentional):

- ``Part``: v1 ``metadata`` is dropped on decode; non-object ``data`` values
  (lists, scalars) are wrapped as ``{"value": ...}`` internally; an internal
  part with no content encodes as ``text=""`` / ``data={}``.
- ``Message``: ``contextId``, ``taskId``, ``metadata``, ``extensions`` and
  ``referenceTaskIds`` are dropped on decode (``contextId``/``taskId`` are
  re-supplied on encode by the caller).
- ``TaskState``: v1-only states have no internal literal — ``REJECTED`` →
  ``failed``, ``AUTH_REQUIRED`` → ``input-required``, ``UNSPECIFIED`` →
  ``submitted``.
- ``Task``: ``contextId``, status ``message``/``timestamp`` and ``metadata``
  are not stored on the internal model (the task store keeps ``contextId``).
- ``Artifact``: ``name``, ``description``, ``metadata``, ``extensions`` dropped.
- ``AgentCard``: the internal card keeps only the first JSON-RPC v1 interface
  URL (``protocolVersion="1.0"`` marks it as a v1 peer); ``tenant``,
  ``securityRequirements``, ``extensions``, ``signatures`` and icons are dropped.
"""

from __future__ import annotations

from typing import Any, cast

from prismal.a2a.types import (
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ATask,
    AgentCard,
    AgentSkill,
    TaskStatus,
)
from prismal.a2a.v1 import constants as c
from prismal.a2a.v1.types import (
    AgentCardV1,
    ArtifactV1,
    MessageV1,
    PartV1,
    Role,
    TaskState,
    TaskStatusV1,
    TaskV1,
)

_V1_ONLY_TO_INTERNAL: dict[TaskState, TaskStatus] = {
    TaskState.REJECTED: "failed",
    TaskState.AUTH_REQUIRED: "input-required",
    TaskState.UNSPECIFIED: "submitted",
}


# ── parts ────────────────────────────────────────────────────────────────────


def part_to_internal(part: PartV1) -> A2APart:
    """Decode a v1 ``Part`` (oneof) into the internal ``kind``-discriminated part."""
    if part.text is not None:
        return A2APart(kind="text", text=part.text)
    if part.data is not None:
        data = part.data if isinstance(part.data, dict) else {"value": part.data}
        return A2APart(kind="data", data=data)
    file: dict[str, Any] = {"bytes": part.raw} if part.raw is not None else {"uri": part.url}
    if part.media_type is not None:
        file["mimeType"] = part.media_type
    if part.filename is not None:
        file["name"] = part.filename
    return A2APart(kind="file", file=file)


def part_from_internal(part: A2APart) -> PartV1:
    """Encode an internal part as a v1 ``Part``."""
    if part.kind == "data":
        return PartV1(data=part.data if part.data is not None else {})
    if part.kind == "file":
        file = part.file or {}
        media_type = file.get("mimeType")
        filename = file.get("name")
        if file.get("bytes") is not None:
            return PartV1(raw=file["bytes"], media_type=media_type, filename=filename)
        if file.get("uri") is not None:
            return PartV1(url=file["uri"], media_type=media_type, filename=filename)
        return PartV1(text="")
    return PartV1(text=part.text or "")


# ── messages ─────────────────────────────────────────────────────────────────


def message_to_internal(message: MessageV1) -> A2AMessage:
    """Decode a v1 message into the internal :class:`A2AMessage` (v0.3 roles)."""
    role = cast("Any", c.V1_TO_LEGACY_ROLE[message.role.value])
    return A2AMessage(
        role=role,
        parts=[part_to_internal(p) for p in message.parts],
        message_id=message.message_id,
    )


def message_from_internal(
    message: A2AMessage, *, context_id: str | None = None, task_id: str | None = None
) -> MessageV1:
    """Encode an internal message as v1, optionally bound to *context_id* / *task_id*."""
    return MessageV1(
        message_id=message.message_id,
        role=Role(c.LEGACY_TO_V1_ROLE[message.role]),
        parts=[part_from_internal(p) for p in message.parts],
        context_id=context_id,
        task_id=task_id,
    )


def message_text(message: MessageV1) -> str:
    """The text parts of a v1 message, newline-joined (what ``_run_task`` consumes)."""
    return "\n".join(p.text for p in message.parts if p.text is not None)


# ── states / artifacts / tasks ──────────────────────────────────────────────


def state_to_v1(status: str) -> TaskState:
    """Map an internal task status to the v1 ``TaskState`` (unknown ⇒ ``UNSPECIFIED``)."""
    return TaskState(c.LEGACY_TO_V1_STATE.get(status, c.TASK_STATE_UNSPECIFIED))


def state_to_internal(state: TaskState) -> TaskStatus:
    """Map a v1 ``TaskState`` to the internal status (v1-only states are folded in)."""
    if state in _V1_ONLY_TO_INTERNAL:
        return _V1_ONLY_TO_INTERNAL[state]
    return cast("TaskStatus", c.V1_TO_LEGACY_STATE[state.value])


def artifact_to_internal(artifact: ArtifactV1) -> A2AArtifact:
    """Decode a v1 artifact into the internal :class:`A2AArtifact`."""
    return A2AArtifact(
        artifact_id=artifact.artifact_id, parts=[part_to_internal(p) for p in artifact.parts]
    )


def artifact_from_internal(artifact: A2AArtifact) -> ArtifactV1:
    """Encode an internal artifact as a v1 :class:`ArtifactV1`."""
    return ArtifactV1(
        artifact_id=artifact.artifact_id, parts=[part_from_internal(p) for p in artifact.parts]
    )


def task_from_internal(
    task: A2ATask,
    *,
    context_id: str,
    timestamp: str | None = None,
    include_artifacts: bool = True,
    history_length: int | None = None,
) -> TaskV1:
    """Encode an internal task as v1 (``historyLength`` keeps the most recent N)."""
    history = list(task.history)
    if history_length is not None:
        history = history[-history_length:] if history_length > 0 else []
    return TaskV1(
        id=task.id,
        context_id=context_id,
        status=TaskStatusV1(state=state_to_v1(task.status), timestamp=timestamp),
        artifacts=(
            [artifact_from_internal(a) for a in task.artifacts] if include_artifacts else None
        ),
        history=(
            [message_from_internal(m, context_id=context_id, task_id=task.id) for m in history]
            or None
        ),
    )


def task_to_internal(task: TaskV1) -> A2ATask:
    """Decode a v1 task (status, history, artifacts) into the internal :class:`A2ATask`."""
    return A2ATask(
        id=task.id,
        status=state_to_internal(task.status.state),
        history=[message_to_internal(m) for m in task.history or []],
        artifacts=[artifact_to_internal(a) for a in task.artifacts or []],
    )


# ── agent card ──────────────────────────────────────────────────────────────


def card_to_internal(card: AgentCardV1) -> AgentCard:
    """Project a v1 card onto the internal ``AgentCard`` used by provider/node code."""
    url = card.supported_interfaces[0].url if card.supported_interfaces else ""
    for iface in card.supported_interfaces:
        if (
            iface.protocol_binding == c.BINDING_JSONRPC
            and iface.protocol_version.split(".")[0] == "1"
        ):
            url = iface.url
            break
    skills = [
        AgentSkill(
            id=s.id,
            name=s.name,
            description=s.description,
            tags=list(s.tags),
            input_modes=list(s.input_modes or card.default_input_modes),
            output_modes=list(s.output_modes or card.default_output_modes),
        )
        for s in card.skills
    ]
    schemes = {
        name: scheme.model_dump(by_alias=True, exclude_none=True)
        for name, scheme in (card.security_schemes or {}).items()
    }
    provider = card.provider.model_dump(by_alias=True) if card.provider is not None else None
    return AgentCard(
        name=card.name,
        description=card.description,
        url=url,
        version=card.version,
        protocol_version=c.A2A_V1_PROTOCOL_VERSION,
        skills=skills,
        capabilities={"streaming": bool(card.capabilities.streaming)},
        security_schemes=schemes,
        provider=provider,
    )


__all__ = [
    "artifact_from_internal",
    "artifact_to_internal",
    "card_to_internal",
    "message_from_internal",
    "message_text",
    "message_to_internal",
    "part_from_internal",
    "part_to_internal",
    "state_to_internal",
    "state_to_v1",
    "task_from_internal",
    "task_to_internal",
]
