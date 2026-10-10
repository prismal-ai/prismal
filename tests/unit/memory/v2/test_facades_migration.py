"""Legacy-shaped facades over MemoryPort and the annotate-only backfill (SPEC-MEM-ADP-002)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from prismal.core.exceptions import MemoryForgetDenied, MemoryTenancyError
from prismal.memory.long_term import LongTermMemory, MemoryEntry
from prismal.memory.long_term_store import LongTermMemoryStore, preferences_namespace
from prismal.memory.v2.adapters.in_memory import InMemoryBackend
from prismal.memory.v2.adapters.langgraph_store import LangGraphStoreBackend
from prismal.memory.v2.adapters.long_term import LongTermMemoryBackend
from prismal.memory.v2.facades import LegacyFactStoreFacade, LegacyLongTermFacade
from prismal.memory.v2.migration import MigrationReport, migrate_legacy_records
from prismal.memory.v2.types import (
    ConsolidationReport,
    ForgetActor,
    ForgetSelector,
    MemoryCandidate,
    MemoryContext,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RecalledItem,
    RecallQuery,
    RecallResult,
    WriteOutcome,
)

if TYPE_CHECKING:
    from pathlib import Path

ORG = MemoryScope(org_id="acme")


def _ctx() -> MemoryContext:
    return MemoryContext(scope=MemoryScope("acme", user_id="u1"), session_id="ctx", actor="coder")


def _org_ctx() -> MemoryContext:
    return MemoryContext(scope=MemoryScope("acme"), session_id="ctx", actor="coder")


def _rec(rid: str, content: str, **kw: object) -> MemoryRecord:
    prov = Provenance(
        origin="conversation",
        source_message_ids=(),
        session_id=str(kw.pop("session_id", "s1")),
        agent=None,
        created_at=1_700_000_000.0,
        tainted=False,
        trust="trusted",
    )
    return MemoryRecord(
        id=rid,
        type=kw.pop("mtype", MemoryType.SEMANTIC),  # type: ignore[arg-type]
        content=content,
        scope=kw.pop("scope", MemoryScope("acme", user_id="u1")),  # type: ignore[arg-type]
        provenance=prov,
        confidence=0.9,
        **kw,  # type: ignore[arg-type]
    )


@dataclass
class FakePort:
    """Records MemoryPort calls; recall returns the scripted records."""

    recalled: list[MemoryRecord] = field(default_factory=list)
    status: str = "stored"
    writes: list[tuple[MemoryCandidate, MemoryContext]] = field(default_factory=list)
    recalls: list[tuple[RecallQuery, MemoryContext, int]] = field(default_factory=list)
    fail_recall: bool = False

    async def write(self, candidate: MemoryCandidate, *, ctx: MemoryContext) -> WriteOutcome:
        self.writes.append((candidate, ctx))
        rid = None if self.status in {"skipped", "quarantined"} else f"mem_{len(self.writes)}"
        return WriteOutcome(self.status, rid, MemoryType.SEMANTIC, (), "none", "")  # type: ignore[arg-type]

    async def recall(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult:
        if self.fail_recall:
            raise RuntimeError("boom")
        self.recalls.append((query, ctx, budget_tokens))
        items = tuple(RecalledItem(r, 1.0, 1) for r in self.recalled)
        return RecallResult(items, "", len(items), "sufficient", (), 0)

    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport:
        raise NotImplementedError

    async def forget(self, selector: ForgetSelector, *, actor: ForgetActor) -> int:
        raise AssertionError("facades must never delete directly")


@dataclass(frozen=True)
class _Req:
    request_id: str


@dataclass
class FakeForget:
    calls: list[tuple[ForgetSelector, str]] = field(default_factory=list)

    def request(self, selector: ForgetSelector, *, requested_by: str) -> _Req:
        self.calls.append((selector, requested_by))
        return _Req(f"fr_{len(self.calls)}")


# ── LegacyLongTermFacade ─────────────────────────────────────────────────────


class TestLegacyLongTermFacade:
    def test_method_names_and_parameters_match_legacy(self) -> None:
        for name in ("save", "recall", "clear", "expire"):
            legacy = inspect.signature(getattr(LongTermMemory, name))
            facade = inspect.signature(getattr(LegacyLongTermFacade, name))
            assert list(legacy.parameters) == list(facade.parameters), name
            for p in legacy.parameters.values():
                assert facade.parameters[p.name].default == p.default, (name, p.name)

    async def test_save_maps_to_write_with_session(self) -> None:
        port = FakePort()
        facade = LegacyLongTermFacade(port, ctx_factory=_ctx)
        assert await facade.save("likes tea", session_id="sess-9") == "mem_1"
        cand, ctx = port.writes[0]
        assert cand.content == "likes tea"
        assert cand.origin == "conversation"
        assert ctx.session_id == "sess-9"
        assert ctx.scope == _ctx().scope

    async def test_save_returns_empty_id_when_not_stored(self) -> None:
        facade = LegacyLongTermFacade(FakePort(status="quarantined"), ctx_factory=_ctx)
        assert await facade.save("x", session_id="s") == ""

    async def test_recall_maps_records_to_memory_entries(self) -> None:
        port = FakePort(
            recalled=[
                _rec("a", "alpha", session_id="s1", expires_at=1_800_000_000.0),
                _rec("b", "beta", session_id="s2"),
                _rec("c", "gamma", session_id="s1"),
            ]
        )
        facade = LegacyLongTermFacade(port, ctx_factory=_ctx, budget_tokens=123)
        everything = await facade.recall("q")
        assert all(isinstance(e, MemoryEntry) for e in everything)
        assert [e.id for e in everything] == ["a", "b", "c"]
        assert everything[0].content == "alpha"
        assert everything[0].created_at.year == 2023
        assert everything[1].expires_at.year == 9999
        only_s1 = await facade.recall("q", session_id="s1", k=1)
        assert [e.id for e in only_s1] == ["a"]
        query, _ctx_used, budget = port.recalls[0]
        assert (query.text, budget) == ("q", 123)

    async def test_recall_budget_defaults_from_settings(self) -> None:
        port = FakePort()
        await LegacyLongTermFacade(port, ctx_factory=_ctx).recall("q")
        assert port.recalls[0][2] > 0

    async def test_recall_degrades_to_empty_on_error(self) -> None:
        facade = LegacyLongTermFacade(FakePort(fail_recall=True), ctx_factory=_ctx)
        assert await facade.recall("q") == []

    async def test_clear_requests_forget_instead_of_deleting(self) -> None:
        forget = FakeForget()
        facade = LegacyLongTermFacade(FakePort(), ctx_factory=_ctx, forget=forget)
        assert await facade.clear(session_id="sess-1") == "fr_1"
        selector, by = forget.calls[0]
        assert by == "coder"
        # save() records the session in provenance, not in scope.thread_id (review N2)
        assert selector.scope == _ctx().scope
        assert selector.session_id == "sess-1"
        assert selector.record_ids == ()
        assert "clear" in selector.reason
        assert await facade.clear() == "fr_2"
        assert forget.calls[1][0].scope == _ctx().scope
        assert forget.calls[1][0].session_id is None

    async def test_clear_without_forget_requester_is_denied(self) -> None:
        facade = LegacyLongTermFacade(FakePort(), ctx_factory=_ctx)
        with pytest.raises(MemoryForgetDenied):
            await facade.clear()

    async def test_expire_is_a_noop_left_to_retention_rules(self) -> None:
        forget = FakeForget()
        facade = LegacyLongTermFacade(FakePort(), ctx_factory=_ctx, forget=forget)
        assert await facade.expire() == 0
        assert forget.calls == []


# ── LegacyFactStoreFacade ────────────────────────────────────────────────────


class TestLegacyFactStoreFacade:
    def test_method_names_and_parameters_match_legacy(self) -> None:
        for name in ("store_fact", "recall_facts", "delete_fact"):
            legacy = inspect.signature(getattr(LongTermMemoryStore, name))
            facade = inspect.signature(getattr(LegacyFactStoreFacade, name))
            assert list(legacy.parameters) == list(facade.parameters), name
            for p in legacy.parameters.values():
                assert facade.parameters[p.name].default == p.default, (name, p.name)
        assert LegacyFactStoreFacade(FakePort(), ctx_factory=_ctx).backend_name == "memory_v2"

    async def test_store_fact_maps_to_write(self) -> None:
        port = FakePort()
        facade = LegacyFactStoreFacade(port, ctx_factory=_org_ctx)
        assert await facade.store_fact("u7", ("preferences", "u7"), "theme", {"a": 1}) is None
        cand, ctx = port.writes[0]
        assert cand.content == "{'a': 1}"
        assert cand.suggested_type is MemoryType.PREFERENCE
        assert cand.metadata == {"legacy_namespace": "preferences/u7", "legacy_key": "theme"}
        assert ctx.scope == MemoryScope("acme", user_id="u7")
        await facade.store_fact("u7", ("learned_facts", "u7"), "k", "v")
        assert port.writes[1][0].suggested_type is None

    async def test_store_fact_for_the_context_user_is_allowed(self) -> None:
        port = FakePort()
        await LegacyFactStoreFacade(port, ctx_factory=_ctx).store_fact(
            "u1", ("preferences", "u1"), "theme", "dark"
        )
        assert port.writes[0][1].scope == MemoryScope("acme", user_id="u1")

    async def test_store_fact_refuses_to_override_the_context_user(self) -> None:
        port = FakePort()
        facade = LegacyFactStoreFacade(port, ctx_factory=_ctx)
        with pytest.raises(MemoryTenancyError):
            await facade.store_fact("u7", ("preferences", "u7"), "theme", "dark")
        assert port.writes == []

    async def test_recall_facts_refuses_another_users_namespace(self) -> None:
        port = FakePort(recalled=[_rec("a", "dark mode")])
        facade = LegacyFactStoreFacade(port, ctx_factory=_ctx)
        with pytest.raises(MemoryTenancyError):
            await facade.recall_facts(("preferences", "u7"), "mode")
        assert port.recalls == []
        assert await LegacyFactStoreFacade(port, ctx_factory=_org_ctx).recall_facts(
            ("preferences", "u7"), "mode"
        )
        assert port.recalls[0][1].scope == MemoryScope("acme", user_id="u7")

    async def test_recall_facts_maps_to_recall(self) -> None:
        port = FakePort(
            recalled=[
                _rec("a", "dark mode", mtype=MemoryType.PREFERENCE, legacy_ref="legacy:x"),
                _rec("b", "other", scope=MemoryScope("acme")),
            ]
        )
        facade = LegacyFactStoreFacade(port, ctx_factory=_ctx)
        facts = await facade.recall_facts(("preferences", "u1"), "mode", limit=1)
        assert facts == [{"key": "a", "value": "dark mode", "user_id": "u1", "created_at": 1.7e9}]
        query, ctx, _ = port.recalls[0]
        assert query.types == frozenset({MemoryType.PREFERENCE})
        assert ctx.scope == MemoryScope("acme", user_id="u1")
        facts = await facade.recall_facts(("learned_facts",), "q")
        assert [f["key"] for f in facts] == ["a", "b"]
        assert facts[1]["user_id"] == ""
        assert port.recalls[1][0].types is None
        assert port.recalls[1][1].scope == _ctx().scope

    async def test_recall_facts_zero_limit_and_errors(self) -> None:
        port = FakePort(fail_recall=True)
        facade = LegacyFactStoreFacade(port, ctx_factory=_ctx)
        assert await facade.recall_facts(("preferences", "u1"), "q", limit=0) == []
        assert await facade.recall_facts(("preferences", "u1"), "q") == []

    async def test_delete_fact_requests_forget(self) -> None:
        forget = FakeForget()
        facade = LegacyFactStoreFacade(FakePort(), ctx_factory=_ctx, forget=forget)
        assert await facade.delete_fact(("preferences", "u1"), "mem_1") == "fr_1"
        selector, by = forget.calls[0]
        assert (selector.record_ids, selector.scope, by) == (("mem_1",), _ctx().scope, "coder")
        with pytest.raises(MemoryForgetDenied):
            await LegacyFactStoreFacade(FakePort(), ctx_factory=_ctx).delete_fact(("x",), "k")


# ── migrate_legacy_records ───────────────────────────────────────────────────


class _Vectors:
    def __init__(self) -> None:
        self.docs: list[object] = []

    @property
    def collection_name(self) -> str:
        return "v"

    def add_documents(self, documents: list[object]) -> list[str]:
        self.docs.extend(documents)
        return []

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[object, float]]:
        return [(d, 1.0) for d in self.docs if query in d.page_content][:k]  # type: ignore[attr-defined]

    def delete_by_source(self, source: str) -> None:
        raise AssertionError("migration must never delete")

    def delete_collection(self) -> None:
        raise AssertionError("migration must never delete")


class TestMigrateLegacyRecords:
    async def _ltm_with_legacy(self, tmp_path: Path) -> tuple[LongTermMemory, list[str]]:
        ltm = LongTermMemory(db_path=tmp_path / "m.db", vector_store=_Vectors(), retention_days=9)
        ids = [await ltm.save(f"legacy fact {i}", session_id=f"s{i}") for i in range(3)]
        return ltm, ids

    async def test_long_term_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        ltm, ids = await self._ltm_with_legacy(tmp_path)
        backend = LongTermMemoryBackend(ltm, legacy_org_id="acme")
        await backend.put(_rec("v2rec", "native v2 fact"))
        report = await migrate_legacy_records(backend, scope=ORG)
        assert report == MigrationReport(scanned=4, annotated=3, skipped=1, dry_run=True)
        assert await migrate_legacy_records(backend, scope=ORG) == report
        hidden = LongTermMemoryBackend(ltm)  # without legacy_org, un-annotated rows stay hidden
        assert [r.id for r in await hidden.list(scope=ORG)] == ["v2rec"]

    async def test_long_term_real_run_is_idempotent_and_annotate_only(self, tmp_path: Path) -> None:
        ltm, ids = await self._ltm_with_legacy(tmp_path)
        before = [(e.id, e.content) for e in ltm._list_all()]
        backend = LongTermMemoryBackend(ltm, legacy_org_id="acme")
        report = await migrate_legacy_records(backend, scope=ORG, dry_run=False, batch_size=2)
        assert report == MigrationReport(scanned=3, annotated=3, skipped=0, dry_run=False)
        again = await migrate_legacy_records(backend, scope=ORG, dry_run=False, batch_size=2)
        assert again == MigrationReport(scanned=3, annotated=0, skipped=3, dry_run=False)
        assert [(e.id, e.content) for e in ltm._list_all()] == before
        # annotated rows now carry their tenant: visible without legacy_org_id
        plain = LongTermMemoryBackend(ltm)
        rec = await plain.get(ids[0], scope=ORG)
        assert rec is not None
        assert (rec.type, rec.provenance.origin, rec.provenance.trust) == (
            MemoryType.SEMANTIC,
            "legacy",
            "unknown",
        )
        assert rec.legacy_ref == ids[0]
        assert [e.id for e in await ltm.recall("legacy fact 1")] == [ids[1]]

    async def test_long_term_other_org_sees_nothing(self, tmp_path: Path) -> None:
        ltm, _ = await self._ltm_with_legacy(tmp_path)
        backend = LongTermMemoryBackend(ltm, legacy_org_id="acme")
        report = await migrate_legacy_records(backend, scope=MemoryScope("globex"), dry_run=False)
        assert report == MigrationReport(scanned=0, annotated=0, skipped=0, dry_run=False)

    async def test_langgraph_store_backfill(self) -> None:
        lts = LongTermMemoryStore(backend="memory")
        for i in range(3):
            await lts.store_fact("u1", preferences_namespace("u1"), f"k{i}", f"pref {i}")
        before = await lts.recall_facts(preferences_namespace("u1"), query="", limit=10)
        backend = LangGraphStoreBackend(
            lts, legacy_namespaces=[("preferences",)], legacy_org_id="acme"
        )
        await backend.put(_rec("v2rec", "native"))
        dry = await migrate_legacy_records(backend, scope=ORG, batch_size=1)
        assert dry == MigrationReport(scanned=3, annotated=3, skipped=0, dry_run=True)
        real = await migrate_legacy_records(backend, scope=ORG, dry_run=False, batch_size=1)
        assert real == MigrationReport(scanned=3, annotated=3, skipped=0, dry_run=False)
        again = await migrate_legacy_records(backend, scope=ORG, dry_run=False)
        assert again == MigrationReport(scanned=3, annotated=0, skipped=3, dry_run=False)
        after = await lts.recall_facts(preferences_namespace("u1"), query="", limit=10)
        assert after == before
        plain = LangGraphStoreBackend(lts, legacy_namespaces=[("preferences",)])
        listed = await plain.list(scope=ORG)
        assert sorted(r.content for r in listed) == ["native", "pref 0", "pref 1", "pref 2"]
        legacy = [r for r in listed if r.provenance.origin == "legacy"]
        assert all(r.scope == MemoryScope("acme", user_id="u1") for r in legacy)
        assert all(r.legacy_ref == r.id for r in legacy)

    async def test_non_annotatable_backend_counts_everything_as_skipped(self) -> None:
        backend = InMemoryBackend()
        await backend.put(_rec("a", "x"))
        await backend.put(_rec("b", "y"))
        report = await migrate_legacy_records(backend, scope=ORG, dry_run=False)
        assert report == MigrationReport(scanned=2, annotated=0, skipped=2, dry_run=False)

    async def test_rejects_non_positive_batch_size(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            await migrate_legacy_records(InMemoryBackend(), scope=ORG, batch_size=0)
