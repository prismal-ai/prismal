"""Tests for the tenant/run-scoped artifact store (Phase TKE — SPEC-TKE-ART-001)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.agents.artifacts import (
    ArtifactRef,
    ArtifactSlice,
    ArtifactStorePort,
    InMemoryArtifactStore,
    SqliteArtifactStore,
    build_artifact_store,
    make_read_artifact_tool,
)
from prismal.core.config import Settings
from prismal.core.exceptions import ArtifactNotFound, ArtifactStoreError


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))


class BrokenAudit:
    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        raise RuntimeError("audit down")


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _settings(**kw: Any) -> Settings:
    return Settings(**kw)


@pytest.fixture(params=["memory", "sqlite"])
def store_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    def make(**kw: Any) -> Any:
        if request.param == "memory":
            return InMemoryArtifactStore(**kw)
        return SqliteArtifactStore(str(tmp_path / "sub" / "a.db"), **kw)

    return make


def test_adapters_satisfy_port(tmp_path: Path) -> None:
    assert isinstance(InMemoryArtifactStore(), ArtifactStorePort)
    assert isinstance(SqliteArtifactStore(str(tmp_path / "x.db")), ArtifactStorePort)


def test_put_get_round_trip(store_factory: Any) -> None:
    store = store_factory(audit=FakeAudit())
    ref = store.put("hello world", run_id="r1", tenant="t1", kind="tool_output", tool="grep")
    assert isinstance(ref, ArtifactRef)
    assert ref.run_id == "r1" and ref.tenant == "t1" and ref.tool == "grep"
    assert ref.kind == "tool_output"
    assert ref.tokens_estimate == 3  # ceil(11 / 4)
    sl = store.get(ref.id, run_id="r1", tenant="t1")
    assert isinstance(sl, ArtifactSlice)
    assert sl.content == "hello world"
    assert sl.start == 0 and sl.end == 11 and sl.total_chars == 11
    assert sl.ref == ref


def test_slicing_clamped(store_factory: Any) -> None:
    store = store_factory()
    ref = store.put("0123456789", run_id="r", tenant="t", kind="rag")
    sl = store.get(ref.id, run_id="r", tenant="t", start=2, end=5)
    assert (sl.content, sl.start, sl.end, sl.total_chars) == ("234", 2, 5, 10)
    sl = store.get(ref.id, run_id="r", tenant="t", start=7)
    assert sl.content == "789" and sl.end == 10
    sl = store.get(ref.id, run_id="r", tenant="t", start=-5, end=999)
    assert sl.content == "0123456789" and sl.start == 0 and sl.end == 10
    sl = store.get(ref.id, run_id="r", tenant="t", start=8, end=3)
    assert sl.content == "" and sl.start == 8 and sl.end == 8


@pytest.mark.parametrize(("tenant", "run_id"), [("other", "r1"), ("t1", "other")])
def test_scope_mismatch_raises_and_audits(store_factory: Any, tenant: str, run_id: str) -> None:
    audit = FakeAudit()
    store = store_factory(audit=audit)
    ref = store.put("secret", run_id="r1", tenant="t1", kind="tool_output")
    with pytest.raises(ArtifactNotFound) as exc:
        store.get(ref.id, run_id=run_id, tenant=tenant)
    assert isinstance(exc.value, ArtifactStoreError)
    with pytest.raises(ArtifactNotFound) as missing:
        store.get("nope", run_id="r1", tenant="t1")
    # Indistinguishable message shape.
    assert str(exc.value).replace(ref.id, "X") == str(missing.value).replace("nope", "X")
    mismatches = [p for e, p in audit.events if e == "artifact_scope_mismatch"]
    assert len(mismatches) == 1
    payload = mismatches[0]
    assert set(payload) == {"artifact_id_hash", "requested_tenant_hash", "requested_run_hash"}
    assert all(isinstance(v, str) and len(v) == 16 for v in payload.values())
    assert "secret" not in repr(audit.events)
    assert tenant not in payload.values()


def test_missing_id_raises(store_factory: Any) -> None:
    store = store_factory()
    with pytest.raises(ArtifactNotFound):
        store.get("does-not-exist", run_id="r", tenant="t")


def test_audit_failure_never_breaks(store_factory: Any) -> None:
    store = store_factory(audit=BrokenAudit())
    ref = store.put("x", run_id="r", tenant="t", kind="k")
    with pytest.raises(ArtifactNotFound):
        store.get(ref.id, run_id="r", tenant="other")
    assert store.get(ref.id, run_id="r", tenant="t").content == "x"


def test_default_audit_is_lazy(monkeypatch: pytest.MonkeyPatch, store_factory: Any) -> None:
    import prismal.agents.artifacts as mod

    built: list[FakeAudit] = []

    def _factory() -> FakeAudit:
        a = FakeAudit()
        built.append(a)
        return a

    monkeypatch.setattr(mod, "_default_audit", _factory)
    store = store_factory()
    ref = store.put("x", run_id="r", tenant="t", kind="k")
    with pytest.raises(ArtifactNotFound):
        store.get(ref.id, run_id="r", tenant="other")
    with pytest.raises(ArtifactNotFound):
        store.get(ref.id, run_id="other", tenant="t")
    assert len(built) == 1
    assert len(built[0].events) == 2


def test_default_audit_factory_failure_tolerated(
    monkeypatch: pytest.MonkeyPatch, store_factory: Any
) -> None:
    import prismal.agents.artifacts as mod

    def _boom() -> Any:
        raise RuntimeError("cannot open log")

    monkeypatch.setattr(mod, "_default_audit", _boom)
    store = store_factory()
    ref = store.put("x", run_id="r", tenant="t", kind="k")
    with pytest.raises(ArtifactNotFound):
        store.get(ref.id, run_id="r", tenant="other")


def test_default_audit_builds_audit_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.agents.artifacts as mod
    import prismal.security.audit as audit_mod

    class StubLogger:
        pass

    monkeypatch.setattr(audit_mod, "AuditLogger", StubLogger)
    assert isinstance(mod._default_audit(), StubLogger)


def test_ttl_expiry_and_purge(store_factory: Any) -> None:
    clock = FakeClock(1000.0)
    store = store_factory(clock=clock, settings=_settings(artifact_ttl_s=100))
    a = store.put("a", run_id="r", tenant="t", kind="k")  # default ttl 100
    b = store.put("b", run_id="r", tenant="t", kind="k", ttl_s=10)
    c = store.put("c", run_id="r", tenant="t", kind="k", ttl_s=0)  # never expires
    assert a.created_at == 1000.0
    clock.now = 1050.0
    with pytest.raises(ArtifactNotFound):
        store.get(b.id, run_id="r", tenant="t")
    assert store.get(a.id, run_id="r", tenant="t").content == "a"
    assert store.purge_expired() == 1
    assert store.purge_expired(now=1200.0) == 1
    with pytest.raises(ArtifactNotFound):
        store.get(a.id, run_id="r", tenant="t")
    clock.now = 10**9
    assert store.get(c.id, run_id="r", tenant="t").content == "c"
    assert store.purge_expired() == 0


def test_settings_ttl_zero_never_expires(store_factory: Any) -> None:
    clock = FakeClock(0.0)
    store = store_factory(clock=clock, settings=_settings(artifact_ttl_s=0))
    ref = store.put("z", run_id="r", tenant="t", kind="k")
    clock.now = 10**12
    assert store.get(ref.id, run_id="r", tenant="t").content == "z"


def test_negative_ttl_rejected(store_factory: Any) -> None:
    store = store_factory()
    with pytest.raises(ValueError):
        store.put("x", run_id="r", tenant="t", kind="k", ttl_s=-1)


def test_ids_unique_and_long(store_factory: Any) -> None:
    store = store_factory()
    ids = {store.put("x", run_id="r", tenant="t", kind="k").id for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) >= 20 for i in ids)


def test_sqlite_persistence_across_instances(tmp_path: Path) -> None:
    path = str(tmp_path / "nested" / "dir" / "art.db")
    s1 = SqliteArtifactStore(path)
    ref = s1.put("persisted", run_id="r", tenant="t", kind="handoff", tool=None)
    s2 = SqliteArtifactStore(path)
    sl = s2.get(ref.id, run_id="r", tenant="t")
    assert sl.content == "persisted"
    assert sl.ref == ref


def test_build_artifact_store_selection(tmp_path: Path) -> None:
    assert isinstance(build_artifact_store(_settings()), InMemoryArtifactStore)
    sq = build_artifact_store(
        _settings(artifact_store_backend="sqlite", artifact_store_path=str(tmp_path / "b.db"))
    )
    assert isinstance(sq, SqliteArtifactStore)
    assert (tmp_path / "b.db").exists()


def test_build_artifact_store_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.agents.artifacts as mod

    monkeypatch.setattr(mod, "get_settings", lambda: _settings())
    assert isinstance(build_artifact_store(), InMemoryArtifactStore)


def test_build_artifact_store_unknown_backend() -> None:
    s = _settings()
    object.__setattr__(s, "artifact_store_backend", "redis")
    with pytest.raises(ArtifactStoreError):
        build_artifact_store(s)


def test_read_artifact_tool_happy_path() -> None:
    store = InMemoryArtifactStore()
    ref = store.put("abcdefghij", run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="t", sanitize=lambda s: s)
    assert tool.name == "read_artifact"
    assert set(tool.args) == {"artifact_id", "start", "end"}
    assert tool.invoke({"artifact_id": ref.id}) == "abcdefghij"
    assert tool.invoke({"artifact_id": ref.id, "start": 2, "end": 4}) == "cd"


def test_read_artifact_tool_sanitize_applied() -> None:
    store = InMemoryArtifactStore()
    ref = store.put("payload", run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="t", sanitize=lambda s: s.upper())
    assert tool.invoke({"artifact_id": ref.id}) == "PAYLOAD"


def test_read_artifact_tool_default_sanitizer_strips_controls() -> None:
    store = InMemoryArtifactStore()
    ref = store.put("ok\x00\x07text", run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="t")
    out = tool.invoke({"artifact_id": ref.id})
    assert "\x00" not in out and "\x07" not in out
    assert "ok" in out and "text" in out


def test_read_artifact_tool_not_found_and_scope_bound() -> None:
    store = InMemoryArtifactStore(audit=FakeAudit())
    ref = store.put("x", run_id="r", tenant="t", kind="tool_output")
    tool = make_read_artifact_tool(store, run_id="r", tenant="other")
    assert tool.invoke({"artifact_id": ref.id}) == "artifact not found"
    assert tool.invoke({"artifact_id": "missing"}) == "artifact not found"


def test_metrics_do_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.monitoring.otel as otel

    class Boom:
        def __init__(self) -> None:
            raise RuntimeError("otel broken")

    monkeypatch.setattr(otel, "OTelManager", Boom)
    store = InMemoryArtifactStore()
    ref = store.put("x", run_id="r", tenant="t", kind="k")
    assert store.get(ref.id, run_id="r", tenant="t").content == "x"


def test_metrics_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    import prismal.monitoring.otel as otel

    calls: list[tuple[str, int, dict[str, Any] | None]] = []

    class Rec:
        def increment_counter(
            self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
        ) -> None:
            calls.append((metric, value, attributes))

    monkeypatch.setattr(otel, "OTelManager", Rec)
    store = InMemoryArtifactStore()
    ref = store.put("x", run_id="r", tenant="t", kind="rag")
    store.get(ref.id, run_id="r", tenant="t")
    assert ("artifacts_stored", 1, {"kind": "rag"}) in calls
    assert any(c[0] == "artifact_reads" for c in calls)
