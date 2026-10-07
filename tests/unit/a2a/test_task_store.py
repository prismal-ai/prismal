"""A2A v1 task store (SPEC-PRT-A2A-005, ADR-PRT-001 §2.1)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from prismal.a2a.task_store import (
    A2ATaskStorePort,
    InMemoryA2ATaskStore,
    TaskRecord,
    TaskStoreQueryError,
)
from prismal.a2a.types import A2ATask
from prismal.a2a.v1.types import ListTasksRequest, TaskState

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 6, tzinfo=UTC)


def _rec(
    i: int,
    org: str | None = "acme",
    *,
    principal: str | None = None,
    state: TaskState = TaskState.COMPLETED,
    context_id: str = "ctx",
    task_id: str | None = None,
) -> TaskRecord:
    ts = T0 + timedelta(seconds=i)
    return TaskRecord(
        task_id=task_id or f"{org}-{i:04d}",
        context_id=context_id,
        org_id=org,
        principal=principal,
        state=state,
        created_at=ts,
        updated_at=ts,
    )


def test_in_memory_store_conforms_to_port() -> None:
    assert isinstance(InMemoryA2ATaskStore(), A2ATaskStorePort)


class TestAcceptance:
    def test_120_acme_5_beta_pages_50_50_20(self) -> None:
        store = InMemoryA2ATaskStore()
        for i in range(120):
            store.put(_rec(i, "acme"))
        for i in range(5):
            store.put(_rec(1000 + i, "beta"))

        pages: list[list[TaskRecord]] = []
        token: str | None = None
        while True:
            params = ListTasksRequest(page_size=50, page_token=token)
            records, token = store.list(org_id="acme", params=params)
            pages.append(records)
            assert all(r.org_id == "acme" for r in records)
            if token == "":
                break
        assert [len(p) for p in pages] == [50, 50, 20]
        assert token == ""
        ids = [r.task_id for p in pages for r in p]
        assert len(set(ids)) == 120
        # newest first
        assert ids[0] == "acme-0119"
        assert ids[-1] == "acme-0000"
        assert store.count(org_id="acme", params=ListTasksRequest()) == 120


class TestListing:
    def test_default_page_size_is_50(self) -> None:
        store = InMemoryA2ATaskStore()
        for i in range(60):
            store.put(_rec(i))
        records, token = store.list(org_id="acme", params=ListTasksRequest())
        assert len(records) == 50
        assert token

    def test_page_size_clamped_to_store_max_and_protocol_max(self) -> None:
        store = InMemoryA2ATaskStore(max_page_size=200)
        for i in range(150):
            store.put(_rec(i))
        records, _ = store.list(org_id="acme", params=ListTasksRequest(page_size=500))
        assert len(records) == 100
        small = InMemoryA2ATaskStore(max_page_size=10)
        for i in range(20):
            small.put(_rec(i))
        records, _ = small.list(org_id="acme", params=ListTasksRequest(page_size=50))
        assert len(records) == 10

    def test_principal_scoping(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1, principal="alice"))
        store.put(_rec(2, principal="bob"))
        records, _ = store.list(org_id="acme", params=ListTasksRequest(), principal="alice")
        assert [r.principal for r in records] == ["alice"]
        assert store.count(org_id="acme", params=ListTasksRequest(), principal="bob") == 1

    def test_none_org_is_its_own_tenant(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1, None))
        store.put(_rec(2, "acme"))
        records, _ = store.list(org_id=None, params=ListTasksRequest())
        assert [r.org_id for r in records] == [None]

    def test_filters(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1, state=TaskState.WORKING, context_id="a"))
        store.put(_rec(2, state=TaskState.COMPLETED, context_id="a"))
        store.put(_rec(3, state=TaskState.WORKING, context_id="b"))
        by_state, _ = store.list(org_id="acme", params=ListTasksRequest(status=TaskState.WORKING))
        assert {r.context_id for r in by_state} == {"a", "b"}
        by_ctx, _ = store.list(org_id="acme", params=ListTasksRequest(context_id="a"))
        assert len(by_ctx) == 2
        after = (T0 + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        recent, _ = store.list(org_id="acme", params=ListTasksRequest(status_timestamp_after=after))
        assert [r.task_id for r in recent] == ["acme-0003", "acme-0002"]

    def test_equal_timestamps_are_paged_stably(self) -> None:
        store = InMemoryA2ATaskStore()
        for i in range(7):
            rec = _rec(0, task_id=f"t{i}")
            store.put(rec)
        seen: list[str] = []
        token: str | None = None
        while token != "":
            records, token = store.list(
                org_id="acme", params=ListTasksRequest(page_size=3, page_token=token)
            )
            seen += [r.task_id for r in records]
        assert sorted(seen) == [f"t{i}" for i in range(7)]
        assert len(seen) == 7

    @pytest.mark.parametrize(
        "token",
        [
            "!!!not-base64!!!",
            base64.urlsafe_b64encode(b"not json").decode(),
            base64.urlsafe_b64encode(b'{"u": 1}').decode(),
        ],
    )
    def test_bad_token_raises_query_error(self, token: str) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1))
        with pytest.raises(TaskStoreQueryError):
            store.list(org_id="acme", params=ListTasksRequest(page_token=token))

    def test_bad_timestamp_raises_query_error(self) -> None:
        store = InMemoryA2ATaskStore()
        with pytest.raises(TaskStoreQueryError):
            store.list(org_id="acme", params=ListTasksRequest(status_timestamp_after="nope"))

    def test_empty_store(self) -> None:
        records, token = InMemoryA2ATaskStore().list(org_id="acme", params=ListTasksRequest())
        assert records == []
        assert token == ""


class TestGetPutAndRegistry:
    def test_get_is_tenant_scoped(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1, "acme", task_id="t"))
        assert store.get("t", org_id="acme") is not None
        assert store.get("t", org_id="beta") is None
        assert store.get("missing", org_id="acme") is None

    def test_put_replaces_record(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(1, task_id="t", state=TaskState.WORKING))
        store.put(_rec(2, task_id="t", state=TaskState.CANCELED))
        rec = store.get("t", org_id="acme")
        assert rec is not None and rec.state is TaskState.CANCELED
        assert store.count(org_id="acme", params=ListTasksRequest()) == 1

    def test_wraps_handler_registry(self) -> None:
        registry: dict[str, A2ATask] = {"t": A2ATask(id="t", status="completed")}
        store = InMemoryA2ATaskStore(registry)
        assert store.task("t") is registry["t"]
        assert store.task("nope") is None

    def test_bounded_eviction_drops_oldest_and_registry_body(self) -> None:
        registry: dict[str, A2ATask] = {}
        store = InMemoryA2ATaskStore(registry, max_entries=3)
        for i in range(5):
            registry[f"acme-{i:04d}"] = A2ATask(id=f"acme-{i:04d}", status="completed")
            store.put(_rec(i))
        assert len(store) == 3
        assert store.get("acme-0000", org_id="acme") is None
        assert "acme-0000" not in registry
        assert "acme-0004" in registry

    def test_rejects_non_positive_bounds(self) -> None:
        with pytest.raises(ValueError):
            InMemoryA2ATaskStore(max_entries=0)
        with pytest.raises(ValueError):
            InMemoryA2ATaskStore(max_page_size=0)


def test_peek_is_unscoped_owner_lookup() -> None:
    store = InMemoryA2ATaskStore()
    store.put(_rec(1, "acme", task_id="t"))
    rec = store.peek("t")
    assert rec is not None and rec.org_id == "acme"
    assert store.peek("missing") is None


class TestNaiveTimestamps:
    """Review fix: naive timestamps / forged tokens never raise TypeError."""

    def test_naive_status_timestamp_after_is_utc(self) -> None:
        store = InMemoryA2ATaskStore()
        store.put(_rec(0))
        store.put(_rec(10))
        after = (T0 + timedelta(seconds=5)).replace(tzinfo=None).isoformat()
        params = ListTasksRequest(status_timestamp_after=after)
        page, _ = store.list(org_id="acme", params=params)
        assert [r.task_id for r in page] == ["acme-0010"]
        assert store.count(org_id="acme", params=params) == 1

    def test_forged_naive_token_is_utc(self) -> None:
        store = InMemoryA2ATaskStore()
        for i in range(3):
            store.put(_rec(i))
        raw = f'{{"u": "{(T0 + timedelta(seconds=2)).replace(tzinfo=None).isoformat()}", "id": "acme-0002"}}'
        token = base64.urlsafe_b64encode(raw.encode()).decode()
        page, _ = store.list(org_id="acme", params=ListTasksRequest(page_token=token))
        assert [r.task_id for r in page] == ["acme-0001", "acme-0000"]

    def test_naive_record_timestamps_compare(self) -> None:
        store = InMemoryA2ATaskStore()
        rec = _rec(3)
        store.put(
            TaskRecord(
                task_id="naive",
                context_id="c",
                org_id="acme",
                principal=None,
                state=TaskState.COMPLETED,
                created_at=rec.created_at.replace(tzinfo=None),
                updated_at=rec.updated_at.replace(tzinfo=None),
            )
        )
        store.put(_rec(1))
        params = ListTasksRequest(status_timestamp_after=T0.isoformat())
        page, _ = store.list(org_id="acme", params=params)
        assert [r.task_id for r in page] == ["naive", "acme-0001"]

    @pytest.mark.parametrize("raw", ['{"u": 5, "id": "x"}', '"str"', '{"u": null}'])
    def test_malformed_token_payloads_raise_query_error(self, raw: str) -> None:
        token = base64.urlsafe_b64encode(raw.encode()).decode()
        store = InMemoryA2ATaskStore()
        store.put(_rec(0))
        with pytest.raises(TaskStoreQueryError):
            store.list(org_id="acme", params=ListTasksRequest(page_token=token))
