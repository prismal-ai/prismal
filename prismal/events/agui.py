"""AG-UI 1.0 event models and the pure ``astream`` → AG-UI translator (Phase PRT5).

SPEC-PRT-AGU-001/002 + SPEC-PRT-USG-002, with the AG-UI pins of ADR-PRT-001 §2.3 overriding
the SPEC's ``[verify]`` names:

* protocol version ``"1.0"`` and exactly 31 event types;
* there is **no** ``USAGE`` event: usage travels as ``CUSTOM prismal.usage`` (the 12-key usage
  wire, SPEC-PRT-USG-001) **and** natively as ``RUN_FINISHED.usage`` / ``RUN_ERROR.usage``
  (``TokenUsage[]``);
* there is **no** ``INTERRUPT`` event: a LangGraph ``interrupt()`` ends the run with
  ``RUN_FINISHED.outcome = {"type": "interrupt", "interrupts": [...]}``;
* sub-agents map to ``SUBAGENT_STARTED`` / ``SUBAGENT_FINISHED`` / ``SUBAGENT_ERROR``.

The translator is pure (DD-PRT-011): sync, no I/O, no HTTP/SSE/WebSocket transport, and it
never raises — a translation error yields ``[]`` and increments
``prismal.agui_translation_errors_total``. Its only state is :class:`AgUiContext`, created per
run by the host and never checkpointed. Message content is passed through as produced; the
translator never reads or builds prompts.

No internal state leaks to clients:

* ``STATE_SNAPSHOT`` / ``STATE_DELTA`` expose only :data:`DEFAULT_STATE_KEYS`, and ``metadata``
  is an **allowlist** (:data:`ALLOWED_METADATA_KEYS`): ``budget`` projected to
  ``{enabled, scope}`` and ``intent`` as a scalar. Only scalar leaves are copied; nested
  mappings are never copied wholesale and unknown keys are dropped, so ``security``,
  ``hardening``, ``identity`` (on-behalf token id / subject / scopes / chain), ``mm`` (media
  paths), ``skynet``, ``kokoro`` and ``_``-prefixed internals never leave the process.
* ``routing`` / ``decision`` go out only as ``CUSTOM prismal.routing`` / ``prismal.decision``,
  gated by ``include_metadata``.
* ``RAW`` (unknown modes, ``values`` chunks, non-mapping ``updates``, non-sub-agent custom
  payloads, unparsable chunks) carries only ``{mode, payload_type}``; a mapping ``custom``
  payload may add ``payload`` = its allowlist projection (omitted when empty).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from prismal.monitoring.observability_types import RunSummary

AGUI_PROTOCOL_VERSION: Final = "1.0"


class AgUiEventType(StrEnum):
    """The 31 AG-UI 1.0 event types (ADR-PRT-001 §2.3); ``value == name``."""

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TEXT_MESSAGE_CHUNK = "TEXT_MESSAGE_CHUNK"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_CHUNK = "TOOL_CALL_CHUNK"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
    ACTIVITY_SNAPSHOT = "ACTIVITY_SNAPSHOT"
    ACTIVITY_DELTA = "ACTIVITY_DELTA"
    RAW = "RAW"
    CUSTOM = "CUSTOM"
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    REASONING_START = "REASONING_START"
    REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
    REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
    REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
    REASONING_MESSAGE_CHUNK = "REASONING_MESSAGE_CHUNK"
    REASONING_END = "REASONING_END"
    REASONING_ENCRYPTED_VALUE = "REASONING_ENCRYPTED_VALUE"
    SUBAGENT_STARTED = "SUBAGENT_STARTED"
    SUBAGENT_FINISHED = "SUBAGENT_FINISHED"
    SUBAGENT_ERROR = "SUBAGENT_ERROR"


_MODEL_CONFIG = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)


class _Wire(BaseModel):
    model_config = _MODEL_CONFIG

    def to_wire(self) -> dict[str, Any]:
        """camelCase JSON-ready dict, ``None`` fields omitted."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TokenUsage(_Wire):
    """AG-UI 1.0 native per-model usage entry (``RUN_FINISHED.usage[]``)."""

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0


class AgUiEvent(_Wire):
    """Base AG-UI event: ``type``, epoch-ms ``timestamp``, optional ``rawEvent``/``metadata``."""

    type: AgUiEventType | str
    timestamp: int | None = None
    raw_event: Any = None
    metadata: dict[str, Any] | None = None


class RunStarted(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.RUN_STARTED
    thread_id: str
    run_id: str
    protocol_version: str = AGUI_PROTOCOL_VERSION
    parent_run_id: str | None = None
    input: Any = None


class RunFinished(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.RUN_FINISHED
    thread_id: str
    run_id: str
    result: Any = None
    outcome: dict[str, Any] | None = None
    usage: list[TokenUsage] | None = None


class RunError(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.RUN_ERROR
    message: str
    code: str | None = None
    usage: list[TokenUsage] | None = None


class StepStarted(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.STEP_STARTED
    step_name: str


class StepFinished(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.STEP_FINISHED
    step_name: str


class TextMessageStart(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TEXT_MESSAGE_START
    message_id: str
    role: str | None = "assistant"
    name: str | None = None


class TextMessageContent(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TEXT_MESSAGE_CONTENT
    message_id: str
    delta: str


class TextMessageEnd(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TEXT_MESSAGE_END
    message_id: str


class ToolCallStart(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TOOL_CALL_START
    tool_call_id: str
    tool_call_name: str
    parent_message_id: str | None = None


class ToolCallArgs(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TOOL_CALL_ARGS
    tool_call_id: str
    delta: str


class ToolCallEnd(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TOOL_CALL_END
    tool_call_id: str


class ToolCallResult(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.TOOL_CALL_RESULT
    message_id: str
    tool_call_id: str
    content: str
    role: str = "tool"


class StateSnapshot(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.STATE_SNAPSHOT
    snapshot: Any


class StateDelta(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.STATE_DELTA
    delta: list[dict[str, Any]]  # RFC 6902 JSON Patch operations


class MessagesSnapshot(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.MESSAGES_SNAPSHOT
    messages: list[dict[str, Any]] = Field(default_factory=list)


class SubagentStarted(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.SUBAGENT_STARTED
    subagent_run_id: str
    name: str
    description: str | None = None
    parent_subagent_run_id: str | None = None
    parent_tool_call_id: str | None = None
    parent_message_id: str | None = None


class SubagentFinished(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.SUBAGENT_FINISHED
    subagent_run_id: str
    result: Any = None
    outcome: dict[str, Any] = Field(default_factory=lambda: {"type": "success"})


class SubagentError(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.SUBAGENT_ERROR
    subagent_run_id: str
    message: str
    code: str | None = None


class RawEvent(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.RAW
    event: Any
    source: str | None = None


class CustomEvent(AgUiEvent):
    type: AgUiEventType | str = AgUiEventType.CUSTOM
    name: str
    value: Any = None


EVENT_CLASSES: Final[dict[AgUiEventType, type[AgUiEvent]]] = {
    AgUiEventType.RUN_STARTED: RunStarted,
    AgUiEventType.RUN_FINISHED: RunFinished,
    AgUiEventType.RUN_ERROR: RunError,
    AgUiEventType.STEP_STARTED: StepStarted,
    AgUiEventType.STEP_FINISHED: StepFinished,
    AgUiEventType.TEXT_MESSAGE_START: TextMessageStart,
    AgUiEventType.TEXT_MESSAGE_CONTENT: TextMessageContent,
    AgUiEventType.TEXT_MESSAGE_END: TextMessageEnd,
    AgUiEventType.TOOL_CALL_START: ToolCallStart,
    AgUiEventType.TOOL_CALL_ARGS: ToolCallArgs,
    AgUiEventType.TOOL_CALL_END: ToolCallEnd,
    AgUiEventType.TOOL_CALL_RESULT: ToolCallResult,
    AgUiEventType.STATE_SNAPSHOT: StateSnapshot,
    AgUiEventType.STATE_DELTA: StateDelta,
    AgUiEventType.MESSAGES_SNAPSHOT: MessagesSnapshot,
    AgUiEventType.SUBAGENT_STARTED: SubagentStarted,
    AgUiEventType.SUBAGENT_FINISHED: SubagentFinished,
    AgUiEventType.SUBAGENT_ERROR: SubagentError,
    AgUiEventType.RAW: RawEvent,
    AgUiEventType.CUSTOM: CustomEvent,
}


def to_sse(event: AgUiEvent) -> str:
    """Encode one event as an SSE frame: ``data: <camelCase JSON>\\n\\n``.

    Encoding helper only — the host owns the transport (DD-PRT-011).
    """
    return f"data: {event.model_dump_json(by_alias=True, exclude_none=True)}\n\n"


# =============================================================================== translator

#: Top-level ``AgentState`` keys exposed in ``STATE_SNAPSHOT`` / ``STATE_DELTA``. Everything else
#: (``messages``, retrieved docs, tool outputs, security/risk fields, …) never leaves the engine.
DEFAULT_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "current_agent",
        "next_agent",
        "task_plan",
        "completed_tasks",
        "pending_tasks",
        "iteration_count",
        "token_count",
        "estimated_cost_usd",
        "metadata",
    }
)

#: ``metadata`` ALLOWLIST for the client state view (and sanitized custom ``RAW`` payloads).
#: Each allowed key maps to the sub-keys it is projected down to (a tuple), or ``None`` for a
#: scalar-only value (str / bool / int / finite float). Only scalar leaves are ever copied: a
#: nested mapping is never copied wholesale and unknown keys are never passed through, so
#: ``security`` / ``hardening`` / ``identity`` (on-behalf tokens) / ``mm`` (media paths) /
#: ``skynet`` / ``kokoro`` / ``_``-prefixed internals can never reach a client.
#: ``routing`` / ``decision`` travel only on their own ``CUSTOM`` channel (``include_metadata``).
ALLOWED_METADATA_KEYS: Final[dict[str, tuple[str, ...] | None]] = {
    "budget": ("enabled", "scope"),
    "intent": None,
}

_METADATA_CUSTOM: Final[tuple[tuple[str, str], ...]] = (
    ("routing", "prismal.routing"),
    ("decision", "prismal.decision"),
)

USAGE_EVENT_NAME: Final = "prismal.usage"
_SUBAGENT_PHASES: Final = frozenset({"start", "end", "error"})


class _Drop:
    """Sentinel: value is not JSON-representable and is omitted from the state view."""


_DROP: Final = _Drop()


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class AgUiContext:
    """Mutable per-run translation state (created by the host per run, never checkpointed).

    ``open_messages`` maps an open text message / tool-call parent id to the node ("owner")
    that streams it, so parallel ``Send`` branches close independently when their node's
    ``updates`` chunk arrives.
    """

    run_id: str
    thread_id: str
    include_state_deltas: bool = True
    include_usage: bool = True
    include_metadata: bool = True
    parent_run_id: str | None = None
    state_keys: frozenset[str] = DEFAULT_STATE_KEYS
    clock: Callable[[], int] = _now_ms
    open_messages: dict[str, str | None] = field(default_factory=dict)
    open_text: set[str] = field(default_factory=set)
    open_tool_calls: dict[str, str] = field(default_factory=dict)
    active_subagents: dict[str, str] = field(default_factory=dict)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    last_state: dict[str, Any] | None = None
    last_metadata: dict[str, Any] = field(default_factory=dict)
    tool_index: dict[tuple[str, Any], str] = field(default_factory=dict)
    anon_ids: dict[str | None, str] = field(default_factory=dict)
    seq: int = 0

    def next_id(self, kind: str) -> str:
        self.seq += 1
        return f"{self.run_id}-{kind}-{self.seq}"


# ------------------------------------------------------------------------------- helpers


def _increment(metric: str, attributes: dict[str, Any]) -> None:
    """Best-effort OTel counter (never raises; lazily imported to keep this module light)."""
    try:
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter(metric, attributes=attributes)
    except Exception:
        return


def _count(events: list[AgUiEvent]) -> list[AgUiEvent]:
    for ev in events:
        _increment("agui_events", {"type": str(ev.type)})
    return events


def _json_value(value: Any) -> Any:
    """Strict JSON view: non-JSON values (live objects, NaN) become :data:`_DROP`."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str):
                jv = _json_value(v)
                if jv is not _DROP:
                    out[k] = jv
        return out
    if isinstance(value, list | tuple):
        return [jv for jv in (_json_value(v) for v in value) if jv is not _DROP]
    return _DROP


def json_safe(value: Any) -> Any:
    """Lenient JSON view for ``RAW`` payloads: unknown objects become ``"<TypeName>"``."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, BaseModel):
        try:
            return json_safe(value.model_dump(mode="json"))
        except Exception:
            return f"<{type(value).__name__}>"
    return f"<{type(value).__name__}>"


def _scalar(value: Any) -> Any:
    """A JSON scalar leaf, else :data:`_DROP` (mappings / lists / objects never pass)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return _DROP


def sanitize_metadata(meta: Any) -> dict[str, Any]:
    """Project ``meta`` onto :data:`ALLOWED_METADATA_KEYS`; non-mappings yield ``{}``."""
    out: dict[str, Any] = {}
    if not isinstance(meta, Mapping):
        return out
    for key, subkeys in ALLOWED_METADATA_KEYS.items():
        if key not in meta:
            continue
        value = meta[key]
        if subkeys is None:
            leaf = _scalar(value)
            if leaf is not _DROP:
                out[key] = leaf
            continue
        if not isinstance(value, Mapping):
            continue
        projected = {}
        for sub in subkeys:
            if sub in value:
                leaf = _scalar(value[sub])
                if leaf is not _DROP:
                    projected[sub] = leaf
        if projected:
            out[key] = projected
    return out


def state_view(
    state: Mapping[str, Any] | None, keys: frozenset[str] = DEFAULT_STATE_KEYS
) -> dict[str, Any]:
    """Allowlisted, JSON-only projection of ``AgentState`` (no messages, no live objects)."""
    view: dict[str, Any] = {}
    for key, value in (state or {}).items():
        if not isinstance(key, str) or key not in keys or key.startswith("_"):
            continue
        if key == "metadata":
            if not isinstance(value, Mapping):
                continue
            meta = sanitize_metadata(value)
            if meta:
                view[key] = meta
            continue
        jv = _json_value(value)
        if jv is not _DROP:
            view[key] = jv
    return view


def _pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def json_patch_diff(
    old: Mapping[str, Any], new: Mapping[str, Any], path: str = ""
) -> list[dict[str, Any]]:
    """Minimal RFC 6902 patch turning ``old`` into ``new`` (objects recurse; lists replace).

    Deterministic: changed/added keys in ``new`` order are emitted after recursing, removed keys
    follow in ``old`` order at the position they are discovered (sorted union of keys).
    """
    ops: list[dict[str, Any]] = []
    for key in sorted(set(old) | set(new)):
        p = f"{path}/{_pointer(key)}"
        if key not in new:
            ops.append({"op": "remove", "path": p})
        elif key not in old:
            ops.append({"op": "add", "path": p, "value": new[key]})
        elif isinstance(old[key], Mapping) and isinstance(new[key], Mapping):
            ops.extend(json_patch_diff(old[key], new[key], p))
        elif old[key] != new[key] or type(old[key]) is not type(new[key]):
            ops.append({"op": "replace", "path": p, "value": new[key]})
    return ops


def _ev(ctx: AgUiContext, cls: type[AgUiEvent], **fields: Any) -> AgUiEvent:
    return cls(timestamp=ctx.clock(), **fields)


def _raw(ctx: AgUiContext, mode: str, payload: Any, source: str) -> list[AgUiEvent]:
    """``RAW`` that never carries state: only ``{mode, payload_type}``.

    A ``custom`` payload that is a plain mapping may add ``payload`` = its
    :func:`sanitize_metadata` projection (omitted when empty). Nothing else is ever copied.
    """
    event: dict[str, Any] = {"mode": mode, "payload_type": type(payload).__name__}
    if mode == "custom":
        sanitized = sanitize_metadata(payload)
        if sanitized:
            event["payload"] = sanitized
    return [_ev(ctx, RawEvent, event=event, source=source)]


def _owner(namespace: tuple[str, ...], node: Any) -> str | None:
    name = node if isinstance(node, str) else None
    if not namespace:
        return name
    return "|".join((*namespace, name or ""))


# ------------------------------------------------------------------------------- closing


def _close_message(ctx: AgUiContext, mid: str) -> list[AgUiEvent]:
    events: list[AgUiEvent] = []
    if mid in ctx.open_text:
        ctx.open_text.discard(mid)
        events.append(_ev(ctx, TextMessageEnd, message_id=mid))
    for cid, parent in list(ctx.open_tool_calls.items()):
        if parent == mid:
            del ctx.open_tool_calls[cid]
            events.append(_ev(ctx, ToolCallEnd, tool_call_id=cid))
    owner = ctx.open_messages.pop(mid, None)
    if ctx.anon_ids.get(owner) == mid:
        del ctx.anon_ids[owner]
    for key in [k for k in ctx.tool_index if k[0] == mid]:
        del ctx.tool_index[key]
    return events


def _close_owner(
    ctx: AgUiContext, owner: str | None, *, keep: str | None = None
) -> list[AgUiEvent]:
    events: list[AgUiEvent] = []
    for mid, mid_owner in list(ctx.open_messages.items()):
        if mid_owner == owner and mid != keep:
            events.extend(_close_message(ctx, mid))
    return events


def _close_all(ctx: AgUiContext) -> list[AgUiEvent]:
    events: list[AgUiEvent] = []
    for mid in list(ctx.open_messages):
        events.extend(_close_message(ctx, mid))
    for cid in list(ctx.open_tool_calls):
        del ctx.open_tool_calls[cid]
        events.append(_ev(ctx, ToolCallEnd, tool_call_id=cid))
    for sid in list(ctx.active_subagents):
        del ctx.active_subagents[sid]
        events.append(
            _ev(
                ctx,
                SubagentError,
                subagent_run_id=sid,
                message="sub-agent did not finish before the run ended",
                code="subagent_incomplete",
            )
        )
    return events


# ------------------------------------------------------------------------------- messages


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _message_id(ctx: AgUiContext, msg: BaseMessage, owner: str | None) -> str:
    if isinstance(msg.id, str) and msg.id:
        return msg.id
    if owner not in ctx.anon_ids:
        ctx.anon_ids[owner] = ctx.next_id("msg")
    return ctx.anon_ids[owner]


def _open(ctx: AgUiContext, mid: str, owner: str | None) -> list[AgUiEvent]:
    if mid in ctx.open_messages:
        return []
    events = _close_owner(ctx, owner)
    ctx.open_messages[mid] = owner
    return events


def _start_tool(ctx: AgUiContext, mid: str, cid: str, name: str, args: str) -> list[AgUiEvent]:
    ctx.open_tool_calls[cid] = mid
    events = [_ev(ctx, ToolCallStart, tool_call_id=cid, tool_call_name=name, parent_message_id=mid)]
    if args:
        events.append(_ev(ctx, ToolCallArgs, tool_call_id=cid, delta=args))
    return events


def _ai_chunk(ctx: AgUiContext, msg: AIMessageChunk, owner: str | None) -> list[AgUiEvent]:
    mid = _message_id(ctx, msg, owner)
    events = _open(ctx, mid, owner)
    text = _text_of(msg.content)
    if text:
        if mid not in ctx.open_text:
            ctx.open_text.add(mid)
            events.append(_ev(ctx, TextMessageStart, message_id=mid, role="assistant"))
        events.append(_ev(ctx, TextMessageContent, message_id=mid, delta=text))
    for tc in msg.tool_call_chunks:
        key = (mid, tc.get("index"))
        args = tc.get("args") or ""
        cid = tc.get("id") or ctx.tool_index.get(key)
        if cid is None or cid not in ctx.open_tool_calls:
            cid = cid or ctx.next_id("tool")
            ctx.tool_index[key] = cid
            events.extend(_start_tool(ctx, mid, cid, tc.get("name") or "unknown", args))
        elif args:
            events.append(_ev(ctx, ToolCallArgs, tool_call_id=cid, delta=args))
    finished = getattr(msg, "chunk_position", None) == "last" or bool(
        msg.response_metadata.get("finish_reason")
    )
    if finished:
        events.extend(_close_message(ctx, mid))
    return events


def _ai_message(ctx: AgUiContext, msg: AIMessage, owner: str | None) -> list[AgUiEvent]:
    mid = _message_id(ctx, msg, owner)
    events = _open(ctx, mid, owner)
    text = _text_of(msg.content)
    if text and mid not in ctx.open_text:
        ctx.open_text.add(mid)
        events.append(_ev(ctx, TextMessageStart, message_id=mid, role="assistant"))
        events.append(_ev(ctx, TextMessageContent, message_id=mid, delta=text))
    if mid in ctx.open_text:
        ctx.open_text.discard(mid)
        events.append(_ev(ctx, TextMessageEnd, message_id=mid))
    for tc in msg.tool_calls:
        cid = tc.get("id") or ctx.next_id("tool")
        if cid in ctx.open_tool_calls:
            continue
        args = json.dumps(json_safe(tc.get("args", {})), ensure_ascii=False)
        events.extend(_start_tool(ctx, mid, cid, tc.get("name") or "unknown", args))
        del ctx.open_tool_calls[cid]
        events.append(_ev(ctx, ToolCallEnd, tool_call_id=cid))
    events.extend(_close_message(ctx, mid))
    return events


def _tool_message(ctx: AgUiContext, msg: ToolMessage) -> list[AgUiEvent]:
    cid = msg.tool_call_id
    events: list[AgUiEvent] = []
    if cid in ctx.open_tool_calls:
        del ctx.open_tool_calls[cid]
        events.append(_ev(ctx, ToolCallEnd, tool_call_id=cid))
    content = msg.content
    text = (
        content if isinstance(content, str) else json.dumps(json_safe(content), ensure_ascii=False)
    )
    mid = msg.id if isinstance(msg.id, str) and msg.id else ctx.next_id("msg")
    events.append(
        _ev(ctx, ToolCallResult, message_id=mid, tool_call_id=cid, content=text, role="tool")
    )
    return events


def _messages(ctx: AgUiContext, payload: Any, namespace: tuple[str, ...]) -> list[AgUiEvent] | None:
    if not isinstance(payload, list | tuple) or len(payload) != 2:
        return None
    msg, meta = payload
    node = meta.get("langgraph_node") if isinstance(meta, Mapping) else None
    owner = _owner(namespace, node)
    if isinstance(msg, AIMessageChunk):
        return _ai_chunk(ctx, msg, owner)
    if isinstance(msg, AIMessage):
        return _ai_message(ctx, msg, owner)
    if isinstance(msg, ToolMessage):
        return _tool_message(ctx, msg)
    if isinstance(msg, BaseMessage):
        return []  # human / system messages are inputs, never echoed back
    return None


# ------------------------------------------------------------------------------- updates


def _record_interrupts(ctx: AgUiContext, value: Any) -> None:
    items = value if isinstance(value, list | tuple) else [value]
    for item in items:
        raw = getattr(item, "value", item)
        iid = getattr(item, "id", None)
        entry: dict[str, Any] = {
            "id": iid if isinstance(iid, str) and iid else ctx.next_id("interrupt"),
            "reason": "human_approval",
        }
        if isinstance(raw, str):
            entry["message"] = raw
        elif isinstance(raw, Mapping):
            reason = raw.get("reason")
            if isinstance(reason, str) and reason:
                entry["reason"] = reason
            message = raw.get("message") or raw.get("question")
            if isinstance(message, str):
                entry["message"] = message
            tool_call_id = raw.get("tool_call_id")
            if isinstance(tool_call_id, str):
                entry["toolCallId"] = tool_call_id
        entry["metadata"] = {"value": json_safe(raw)}
        ctx.interrupts.append(entry)


def _remember_metadata(ctx: AgUiContext, state: Mapping[str, Any] | None) -> None:
    meta = (state or {}).get("metadata")
    if isinstance(meta, Mapping):
        for key, _name in _METADATA_CUSTOM:
            if key in meta:
                ctx.last_metadata[key] = _json_value(meta[key])


def _metadata_customs(ctx: AgUiContext, update: Mapping[str, Any]) -> list[AgUiEvent]:
    meta = update.get("metadata")
    if not isinstance(meta, Mapping):
        return []
    events: list[AgUiEvent] = []
    for key, name in _METADATA_CUSTOM:
        if key not in meta:
            continue
        value = _json_value(meta[key])
        if value is _DROP or ctx.last_metadata.get(key, _DROP) == value:
            continue
        ctx.last_metadata[key] = value
        if ctx.include_metadata:
            events.append(_ev(ctx, CustomEvent, name=name, value=value))
    return events


def _state_delta(ctx: AgUiContext, update: Mapping[str, Any]) -> list[AgUiEvent]:
    old = ctx.last_state or {}
    new = dict(old)
    partial = state_view(update, ctx.state_keys)
    for key in update:
        if not isinstance(key, str) or key not in ctx.state_keys:
            continue
        if key in partial:
            new[key] = partial[key]
        else:
            new.pop(key, None)
    ctx.last_state = new
    if not ctx.include_state_deltas:
        return []
    patch = json_patch_diff(old, new)
    return [_ev(ctx, StateDelta, delta=patch)] if patch else []


def _updates(ctx: AgUiContext, payload: Any, namespace: tuple[str, ...]) -> list[AgUiEvent] | None:
    if not isinstance(payload, Mapping):
        return None
    events: list[AgUiEvent] = []
    for node, update in payload.items():
        if node == "__interrupt__":
            _record_interrupts(ctx, update)
            continue
        step = str(node)
        events.extend(_close_owner(ctx, _owner(namespace, node)))
        events.append(_ev(ctx, StepStarted, step_name=step))
        if isinstance(update, Mapping) and not namespace:
            events.extend(_state_delta(ctx, update))
            events.extend(_metadata_customs(ctx, update))
        events.append(_ev(ctx, StepFinished, step_name=step))
    return events


# ------------------------------------------------------------------------------- custom


def _opt_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _custom(ctx: AgUiContext, payload: Any) -> list[AgUiEvent]:
    """Sub-agent lifecycle payloads; anything else is ``RAW``.

    Consumed shape (written with LangGraph ``get_stream_writer()`` by Skynet workers, MoA
    proposers and spawners)::

        {"type": "subagent", "phase": "start" | "end" | "error", "id": str, "name": str,
         "description"?: str, "parent_id"?: str, "parent_tool_call_id"?: str,
         "parent_message_id"?: str, "result"?: Any, "message"?: str, "code"?: str}
    """
    if (
        isinstance(payload, Mapping)
        and payload.get("type") == "subagent"
        and payload.get("phase") in _SUBAGENT_PHASES
        and isinstance(payload.get("id"), str)
    ):
        sid: str = payload["id"]
        phase = payload["phase"]
        if phase == "start" and sid not in ctx.active_subagents:
            name = _opt_str(payload, "name") or "subagent"
            ctx.active_subagents[sid] = name
            return [
                _ev(
                    ctx,
                    SubagentStarted,
                    subagent_run_id=sid,
                    name=name,
                    description=_opt_str(payload, "description"),
                    parent_subagent_run_id=_opt_str(payload, "parent_id"),
                    parent_tool_call_id=_opt_str(payload, "parent_tool_call_id"),
                    parent_message_id=_opt_str(payload, "parent_message_id"),
                )
            ]
        if phase == "end" and sid in ctx.active_subagents:
            del ctx.active_subagents[sid]
            result = json_safe(payload["result"]) if "result" in payload else None
            return [
                _ev(
                    ctx,
                    SubagentFinished,
                    subagent_run_id=sid,
                    result=result,
                    outcome={"type": "success"},
                )
            ]
        if phase == "error" and sid in ctx.active_subagents:
            del ctx.active_subagents[sid]
            return [
                _ev(
                    ctx,
                    SubagentError,
                    subagent_run_id=sid,
                    message=_opt_str(payload, "message") or "sub-agent failed",
                    code=_opt_str(payload, "code"),
                )
            ]
    return _raw(ctx, "custom", payload, "langgraph.custom")


# ------------------------------------------------------------------------------- public API


def _split(chunk: Any) -> tuple[tuple[str, ...], str, Any] | None:
    if isinstance(chunk, tuple):
        if len(chunk) == 2 and isinstance(chunk[0], str):
            return (), chunk[0], chunk[1]
        if len(chunk) == 3 and isinstance(chunk[1], str) and isinstance(chunk[0], tuple):
            return tuple(str(p) for p in chunk[0]), chunk[1], chunk[2]
    return None


def _mode_of(chunk: Any) -> str:
    parts = _split(chunk)
    return parts[1] if parts else "unknown"


def _translate(chunk: Any, ctx: AgUiContext) -> list[AgUiEvent]:
    parts = _split(chunk)
    if parts is None:
        return _raw(ctx, "unknown", chunk, "langgraph")
    namespace, mode, payload = parts
    events: list[AgUiEvent] | None = None
    if mode == "messages":
        events = _messages(ctx, payload, namespace)
    elif mode == "updates":
        events = _updates(ctx, payload, namespace)
    elif mode == "custom":
        events = _custom(ctx, payload)
    if events is None:
        source = f"langgraph.{mode}" if mode in {"messages", "updates"} else "langgraph"
        return _raw(ctx, mode, payload, source)
    return events


def to_agui_events(chunk: Any, ctx: AgUiContext) -> list[AgUiEvent]:
    """Translate one ``graph.astream(stream_mode=["messages", "updates", "custom"])`` item.

    Accepts ``(mode, payload)`` and, with ``subgraphs=True``, ``(namespace, mode, payload)``.
    Pure, sync, no I/O and never raises: on an internal error it returns ``[]`` and increments
    ``prismal.agui_translation_errors_total{mode}``. Unknown shapes become ``RAW``.
    """
    try:
        return _count(_translate(chunk, ctx))
    except Exception:
        _increment("agui_translation_errors", {"mode": _mode_of(chunk)})
        return []


def start_run(
    ctx: AgUiContext,
    *,
    initial_state: Mapping[str, Any] | None = None,
    input: Any = None,
) -> list[AgUiEvent]:
    """``RUN_STARTED`` (``protocolVersion`` ``"1.0"``) then, when ``include_state_deltas``, a
    ``STATE_SNAPSHOT`` of the allowlisted state view (the baseline for later deltas)."""
    try:
        events = [
            _ev(
                ctx,
                RunStarted,
                thread_id=ctx.thread_id,
                run_id=ctx.run_id,
                parent_run_id=ctx.parent_run_id,
                input=json_safe(input) if input is not None else None,
            )
        ]
        ctx.last_state = state_view(initial_state, ctx.state_keys)
        _remember_metadata(ctx, initial_state)
        if ctx.include_state_deltas:
            events.append(_ev(ctx, StateSnapshot, snapshot=dict(ctx.last_state)))
        return _count(events)
    except Exception:
        _increment("agui_translation_errors", {"mode": "start"})
        return []


def _native_usage(wire: Mapping[str, Any]) -> list[TokenUsage]:
    served = wire.get("served_model") or ""
    gateway = wire.get("gateway")
    provider = gateway or (served.split("/", 1)[0] if "/" in served else "") or "unknown"
    return [
        TokenUsage(
            provider=provider,
            model=served or "unknown",
            input_tokens=wire["prompt_tokens"],
            output_tokens=wire["completion_tokens"],
            total_tokens=wire["total_tokens"],
            reasoning_tokens=wire["reasoning_tokens"],
            cached_input_tokens=wire["cache_read_tokens"],
            cache_write_input_tokens=wire["cache_write_tokens"],
        )
    ]


def _usage(ctx: AgUiContext, summary: RunSummary | None) -> tuple[list[AgUiEvent], Any]:
    if summary is None or not ctx.include_usage:
        return [], None
    wire = summary.to_usage_wire()
    return [_ev(ctx, CustomEvent, name=USAGE_EVENT_NAME, value=wire)], _native_usage(wire)


def finish_run(
    ctx: AgUiContext,
    *,
    summary: RunSummary | None = None,
    result: Any = None,
) -> list[AgUiEvent]:
    """Close open items, then ``CUSTOM prismal.usage`` (12-key wire), then ``RUN_FINISHED``.

    ``RUN_FINISHED.outcome`` is ``{"type": "interrupt", "interrupts": [...]}`` when the run hit
    a LangGraph ``interrupt()`` (AG-UI 1.0 has no INTERRUPT event), else ``{"type": "success"}``;
    ``RUN_FINISHED.usage`` carries the native ``TokenUsage[]`` (SPEC-PRT-USG-002). Never raises.
    """
    try:
        events = _close_all(ctx)
        usage_events, native = _usage(ctx, summary)
        events.extend(usage_events)
        outcome: dict[str, Any] = (
            {"type": "interrupt", "interrupts": list(ctx.interrupts)}
            if ctx.interrupts
            else {"type": "success"}
        )
        events.append(
            _ev(
                ctx,
                RunFinished,
                thread_id=ctx.thread_id,
                run_id=ctx.run_id,
                result=json_safe(result) if result is not None else None,
                outcome=outcome,
                usage=native,
            )
        )
        return _count(events)
    except Exception:
        _increment("agui_translation_errors", {"mode": "finish"})
        return []


def error_run(
    ctx: AgUiContext,
    *,
    code: str,
    message: str,
    summary: RunSummary | None = None,
) -> list[AgUiEvent]:
    """Close open items, then (optionally) ``CUSTOM prismal.usage``, then ``RUN_ERROR``."""
    try:
        events = _close_all(ctx)
        usage_events, native = _usage(ctx, summary)
        events.extend(usage_events)
        events.append(_ev(ctx, RunError, message=message, code=code, usage=native))
        return _count(events)
    except Exception:
        _increment("agui_translation_errors", {"mode": "error"})
        return []
