"""A2A v1.0 wire layer (Phase PRT — ADR-PRT-001 §2.1).

``constants`` pins every v1/v0.3 protocol name; ``types`` holds the v1 wire
models. The internal canonical model stays the v0.3 one in
:mod:`prismal.a2a.types` (DD-PRT-002); :mod:`prismal.a2a.convert` bridges them.
"""

from __future__ import annotations

from prismal.a2a.v1.constants import A2A_V1_PROTOCOL_VERSION, A2AProtocolVersion
from prismal.a2a.v1.types import (
    AgentCardV1,
    AgentInterface,
    ArtifactV1,
    ListTasksParams,
    ListTasksRequest,
    ListTasksResult,
    MessageV1,
    PartV1,
    SendMessageRequest,
    TaskState,
    TaskV1,
)

__all__ = [
    "A2A_V1_PROTOCOL_VERSION",
    "A2AProtocolVersion",
    "AgentCardV1",
    "AgentInterface",
    "ArtifactV1",
    "ListTasksParams",
    "ListTasksRequest",
    "ListTasksResult",
    "MessageV1",
    "PartV1",
    "SendMessageRequest",
    "TaskState",
    "TaskV1",
]
