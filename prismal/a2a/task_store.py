"""A2A v1 task store (SPEC-PRT-A2A-005, ADR-PRT-001 §2.1).

:class:`A2ATaskStorePort` indexes the tasks the inbound handler produces so
``GetTask`` / ``CancelTask`` / ``ListTasks`` can be answered **tenant-scoped**
(and, under ``a2a_strict``, principal-scoped). :class:`InMemoryA2ATaskStore`
wraps the handler's existing registry (``A2AServerHandler._tasks``, the
``task_id → A2ATask`` dict that already serves v0.3 ``tasks/get``): the store
keeps the metadata (:class:`TaskRecord`), the registry keeps the bodies.

Pagination: newest-first by ``(updated_at, task_id)``; page tokens are opaque
url-safe base64 of the last returned ``(updated_at, task_id)`` cursor; the last
page returns ``""``; the page size is clamped to
``min(max_page_size, 100)`` (protocol maximum).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismal.a2a.v1 import constants as c

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from prismal.a2a.types import A2ATask
    from prismal.a2a.v1.types import ListTasksRequest, TaskState


class TaskStoreQueryError(ValueError):
    """Malformed ``ListTasks`` input (bad page token or timestamp) → JSON-RPC -32602."""


@dataclass(frozen=True)
class TaskRecord:
    """Tenant-scoped metadata of one inbound task."""

    task_id: str
    context_id: str
    org_id: str | None
    principal: str | None
    state: TaskState
    created_at: datetime
    updated_at: datetime
    artifact_ids: tuple[str, ...] = ()


@runtime_checkable
class A2ATaskStorePort(Protocol):
    """Where the dual-stack handler records and queries v1 tasks."""

    def put(self, task: TaskRecord) -> None: ...

    def get(self, task_id: str, *, org_id: str | None) -> TaskRecord | None: ...

    def peek(self, task_id: str) -> TaskRecord | None:
        """Unscoped owner lookup — internal ownership checks only, never returned to callers."""
        ...

    def list(
        self, *, org_id: str | None, params: ListTasksRequest, principal: str | None = None
    ) -> tuple[list[TaskRecord], str]: ...

    def count(
        self, *, org_id: str | None, params: ListTasksRequest, principal: str | None = None
    ) -> int: ...

    def task(self, task_id: str) -> A2ATask | None: ...


def _aware(value: datetime) -> datetime:
    """Naive datetimes are UTC (never compared naive-vs-aware → no ``TypeError``)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _parse_ts(raw: str) -> datetime:
    try:
        return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except (TypeError, ValueError) as exc:
        raise TaskStoreQueryError(f"invalid timestamp: {raw!r}") from exc


def _cursor(record: TaskRecord) -> tuple[datetime, str]:
    return _aware(record.updated_at), record.task_id


def _encode_token(record: TaskRecord) -> str:
    payload = json.dumps({"u": record.updated_at.isoformat(), "id": record.task_id})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_token(token: str) -> tuple[datetime, str]:
    try:
        obj = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        stamp, task_id = obj["u"], obj["id"]
        if not isinstance(stamp, str) or not isinstance(task_id, str):
            raise TypeError("token fields must be strings")
        return _parse_ts(stamp), task_id
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise TaskStoreQueryError("invalid page token") from exc


class InMemoryA2ATaskStore:
    """Bounded in-process :class:`A2ATaskStorePort` over the handler's task registry."""

    def __init__(
        self,
        registry: MutableMapping[str, A2ATask] | None = None,
        *,
        max_entries: int = 10_000,
        max_page_size: int = c.LIST_TASKS_MAX_PAGE_SIZE,
    ) -> None:
        if max_entries < 1 or max_page_size < 1:
            raise ValueError("max_entries and max_page_size must be positive")
        self._registry: MutableMapping[str, A2ATask] = registry if registry is not None else {}
        self._records: OrderedDict[str, TaskRecord] = OrderedDict()
        self._max_entries = max_entries
        self._max_page_size = min(max_page_size, c.LIST_TASKS_MAX_PAGE_SIZE)

    def __len__(self) -> int:
        return len(self._records)

    def put(self, task: TaskRecord) -> None:
        self._records.pop(task.task_id, None)
        self._records[task.task_id] = task
        while len(self._records) > self._max_entries:
            evicted, _ = self._records.popitem(last=False)
            self._registry.pop(evicted, None)

    def get(self, task_id: str, *, org_id: str | None) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def peek(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def task(self, task_id: str) -> A2ATask | None:
        return self._registry.get(task_id)

    def _matching(
        self, org_id: str | None, params: ListTasksRequest, principal: str | None
    ) -> list[TaskRecord]:
        after = (
            _parse_ts(params.status_timestamp_after)
            if params.status_timestamp_after is not None
            else None
        )
        out = [
            r
            for r in self._records.values()
            if r.org_id == org_id
            and (principal is None or r.principal == principal)
            and (params.context_id is None or r.context_id == params.context_id)
            and (params.status is None or r.state == params.status)
            and (after is None or _aware(r.updated_at) >= after)
        ]
        out.sort(key=_cursor, reverse=True)
        return out

    def page_size(self, params: ListTasksRequest) -> int:
        """The effective (clamped) page size for *params*."""
        requested = params.page_size or c.LIST_TASKS_DEFAULT_PAGE_SIZE
        return min(requested, self._max_page_size)

    def list(
        self, *, org_id: str | None, params: ListTasksRequest, principal: str | None = None
    ) -> tuple[list[TaskRecord], str]:
        records = self._matching(org_id, params, principal)
        if params.page_token:
            cursor = _decode_token(params.page_token)
            records = [r for r in records if _cursor(r) < cursor]
        size = self.page_size(params)
        page = records[:size]
        token = _encode_token(page[-1]) if len(records) > size else ""
        return page, token

    def count(
        self, *, org_id: str | None, params: ListTasksRequest, principal: str | None = None
    ) -> int:
        return len(self._matching(org_id, params, principal))


__all__ = ["A2ATaskStorePort", "InMemoryA2ATaskStore", "TaskRecord", "TaskStoreQueryError"]
