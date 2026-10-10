"""Backend adapters over the unchanged legacy stores (SPEC-MEM-ADP-001, TEN-001)."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pytest
from langchain_core.documents import Document

from prismal.core.exceptions import MemoryScanLimitError
from prismal.memory.long_term import LongTermMemory
from prismal.memory.long_term_store import LongTermMemoryStore, preferences_namespace
from prismal.memory.v2.adapters.in_memory import overlap_score
from prismal.memory.v2.adapters.langgraph_store import LangGraphStoreBackend
from prismal.memory.v2.adapters.long_term import LongTermMemoryBackend
from prismal.memory.v2.ports import MemoryBackendPort
from prismal.memory.v2.types import (
    ForgetActor,
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RelationKind,
)

if TYPE_CHECKING:
    from pathlib import Path

RULE = ForgetActor(kind="rule", principal="retention:ttl")


class LexicalVectorStore:
    """VectorStorePort double that actually searches what was added (token overlap)."""

    def __init__(self) -> None:
        self.docs: list[Document] = []
        self.deleted_sources: list[str] = []

    @property
    def collection_name(self) -> str:
        return "lexical"

    def add_documents(self, documents: list[Document]) -> list[str]:
        self.docs.extend(documents)
        return [str(d.metadata.get("source")) for d in documents]

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        scored = [(d, overlap_score(query, d.page_content)) for d in self.docs]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: -s[1])
        return scored[:k]

    def delete_by_source(self, source: str) -> None:
        self.deleted_sources.append(source)
        self.docs = [d for d in self.docs if d.metadata.get("source") != source]

    def delete_collection(self) -> None:
        self.docs = []


def _prov(**kw: object) -> Provenance:
    base: dict[str, object] = {
        "origin": "conversation",
        "source_message_ids": ("m1",),
        "session_id": "s1",
        "agent": "coder",
        "created_at": time.time(),
        "tainted": False,
        "trust": "trusted",
    }
    base.update(kw)
    return Provenance(**base)  # type: ignore[arg-type]


def _rec(
    rid: str,
    content: str,
    *,
    org: str = "acme",
    user: str | None = "u1",
    mtype: MemoryType = MemoryType.SEMANTIC,
    **kw: object,
) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        type=mtype,
        content=content,
        scope=MemoryScope(org_id=org, user_id=user),
        provenance=kw.pop("provenance", _prov()),  # type: ignore[arg-type]
        confidence=0.9,
        **kw,  # type: ignore[arg-type]
    )


# ── LongTermMemoryBackend ────────────────────────────────────────────────────


@pytest.fixture
def ltm(tmp_path: Path) -> LongTermMemory:
    return LongTermMemory(
        db_path=tmp_path / "mem.db", vector_store=LexicalVectorStore(), retention_days=30
    )


class TestLongTermMemoryBackend:
    def test_conforms_to_backend_port(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        assert isinstance(backend, MemoryBackendPort)
        assert backend.name == "legacy_long_term"

    async def test_v2_write_visible_to_unchanged_legacy_recall(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        rid = await backend.put(_rec("mem_1", "user prefers dark mode"))
        assert rid == "mem_1"
        entries = await ltm.recall("dark mode")
        assert [e.content for e in entries] == ["user prefers dark mode"]
        assert entries[0].id == "mem_1"
        assert entries[0].session_id == "s1"

    async def test_round_trip_preserves_v2_fields(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        edge = MemoryEdge("mem_1", "mem_0", RelationKind.UPDATES, 0.8, valid_from=1.0)
        rec = _rec(
            "mem_1",
            "deploys go out on friday",
            mtype=MemoryType.PROCEDURAL,
            provenance=_prov(tainted=True, trust="untrusted", taint_sources=("tool:web",)),
            edges=(edge,),
            version=3,
            expires_at=time.time() + 1000,
        )
        await backend.put(rec)
        got = await backend.get("mem_1", scope=MemoryScope(org_id="acme"))
        assert got == rec

    async def test_redacts_secrets_like_legacy(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("mem_s", "key is AKIAABCDEFGHIJKLMNOP ok"))
        got = await backend.get("mem_s", scope=MemoryScope(org_id="acme"))
        assert got is not None
        assert "AKIA" not in got.content

    async def test_legacy_record_maps_to_semantic_legacy_unknown(self, ltm: LongTermMemory) -> None:
        entry_id = await ltm.save("the office is in Madrid", session_id="old-sess")
        backend = LongTermMemoryBackend(ltm, legacy_org_id="acme")
        got = await backend.get(entry_id, scope=MemoryScope(org_id="acme"))
        assert got is not None
        assert got.type is MemoryType.SEMANTIC
        assert got.provenance.origin == "legacy"
        assert got.provenance.trust == "unknown"
        assert got.provenance.session_id == "old-sess"
        assert got.legacy_ref == entry_id
        assert got.scope.org_id == "acme"

    async def test_legacy_records_invisible_without_legacy_org(self, ltm: LongTermMemory) -> None:
        entry_id = await ltm.save("the office is in Madrid", session_id="old")
        backend = LongTermMemoryBackend(ltm)
        assert await backend.get(entry_id, scope=MemoryScope(org_id="acme")) is None
        assert await backend.list(scope=MemoryScope(org_id="acme")) == []
        other = LongTermMemoryBackend(ltm, legacy_org_id="globex")
        assert await other.get(entry_id, scope=MemoryScope(org_id="acme")) is None

    async def test_tenant_isolation_on_get_search_list(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("a1", "acme secret plan", org="acme"))
        await backend.put(_rec("g1", "globex secret plan", org="globex"))
        acme = MemoryScope(org_id="acme")
        assert await backend.get("g1", scope=acme) is None
        hits = await backend.search("secret plan", scope=acme, types=None, k=10)
        assert [r.id for r, _ in hits] == ["a1"]
        assert [r.id for r in await backend.list(scope=acme)] == ["a1"]

    async def test_search_filters_types_superseded_and_user(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("p1", "likes green tea", mtype=MemoryType.PREFERENCE))
        await backend.put(_rec("s1", "green tea is a drink"))
        await backend.put(_rec("old", "likes green tea a lot", superseded_by="p1"))
        await backend.put(_rec("u2", "green tea for u2", user="u2"))
        scope = MemoryScope(org_id="acme", user_id="u1")
        hits = await backend.search(
            "green tea", scope=scope, types=frozenset({MemoryType.PREFERENCE}), k=5
        )
        assert [r.id for r, _ in hits] == ["p1"]
        all_hits = await backend.search(
            "green tea", scope=scope, types=None, k=5, include_superseded=True
        )
        assert {r.id for r, _ in all_hits} == {"p1", "s1", "old"}
        assert all(0.0 <= s <= 1.0 for _, s in all_hits)
        assert await backend.search("green tea", scope=scope, types=None, k=0) == []

    async def test_search_skips_expired_and_dangling_docs(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("e1", "expired fact", expires_at=time.time() - 10))
        ltm._store.add_documents(  # a vector doc whose SQLite row is gone
            [Document(page_content="expired fact ghost", metadata={"entry_id": "ghost"})]
        )
        assert (
            await backend.search("expired fact", scope=MemoryScope("acme"), types=None, k=5) == []
        )

    async def test_list_since_and_limit(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("r1", "one", provenance=_prov(created_at=100.0)))
        await backend.put(_rec("r2", "two", provenance=_prov(created_at=200.0)))
        await backend.put(_rec("r3", "three", provenance=_prov(created_at=300.0)))
        scope = MemoryScope("acme")
        assert [r.id for r in await backend.list(scope=scope, since=150.0)] == ["r2", "r3"]
        assert [r.id for r in await backend.list(scope=scope, limit=2)] == ["r1", "r2"]

    async def test_update_rewrites_metadata_and_reindexes_content(
        self, ltm: LongTermMemory
    ) -> None:
        backend = LongTermMemoryBackend(ltm)
        rec = _rec("r1", "blue sky")
        await backend.put(rec)
        await backend.update(_rec("r1", "red sky", superseded_by="r9", version=2))
        got = await backend.get("r1", scope=MemoryScope("acme"))
        assert got is not None
        assert (got.content, got.superseded_by, got.version) == ("red sky", "r9", 2)
        assert [e.content for e in await ltm.recall("red sky")] == ["red sky"]
        assert await ltm.recall("blue") == []
        with pytest.raises(KeyError):
            await backend.update(_rec("missing", "x"))

    async def test_put_twice_overwrites(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("r1", "first"))
        await backend.put(_rec("r1", "second"))
        assert [r.content for r in await backend.list(scope=MemoryScope("acme"))] == ["second"]

    async def test_purge_requires_forget_actor_and_respects_scope(
        self, ltm: LongTermMemory
    ) -> None:
        backend = LongTermMemoryBackend(ltm)
        await backend.put(_rec("a1", "acme fact"))
        await backend.put(_rec("g1", "globex fact", org="globex"))
        with pytest.raises(TypeError):
            await backend.purge(["a1"], scope=MemoryScope("acme"), _authorized="agent")  # type: ignore[arg-type]
        n = await backend.purge(["a1", "g1", "nope"], scope=MemoryScope("acme"), _authorized=RULE)
        assert n == 1
        assert [e.id for e in await ltm.recall("acme fact")] == ["g1"]
        assert await backend.get("g1", scope=MemoryScope("globex")) is not None
        with sqlite3.connect(ltm._db) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM memory_entries")]
        assert ids == ["g1"]

    async def test_legacy_ttl_never_expires_v2_records(self, ltm: LongTermMemory) -> None:
        # Retention of v2 records is ForgetPolicy's job: the legacy TTL sweep must not
        # delete them unaudited (review HIGH3).
        backend = LongTermMemoryBackend(ltm)
        old = time.time() - 40 * 86400
        await backend.put(_rec("v2old", "prefers dark mode", provenance=_prov(created_at=old)))
        assert await ltm.expire() == 0
        got = await backend.get("v2old", scope=MemoryScope("acme", "u1"))
        assert got is not None
        assert got.expires_at is None

    async def test_explicit_expires_at_still_honoured_by_v2(self, ltm: LongTermMemory) -> None:
        backend = LongTermMemoryBackend(ltm)
        past = time.time() - 10
        await backend.put(_rec("v2exp", "short lived", expires_at=past))
        got = await backend.get("v2exp", scope=MemoryScope("acme", "u1"))
        assert got is not None
        assert got.expires_at == pytest.approx(past, abs=1)

    async def test_legacy_schema_untouched(self, ltm: LongTermMemory) -> None:
        LongTermMemoryBackend(ltm)
        with sqlite3.connect(ltm._db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_entries)")]
        assert cols == ["id", "session_id", "content", "created_at", "expires_at"]


# ── LangGraphStoreBackend ────────────────────────────────────────────────────


@pytest.fixture
def lts() -> LongTermMemoryStore:
    return LongTermMemoryStore(backend="memory")


class TestLangGraphStoreBackend:
    def test_conforms_to_backend_port(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        assert isinstance(backend, MemoryBackendPort)
        assert backend.name == "langgraph_store"

    def test_namespace_is_derived_from_scope_org_first(self) -> None:
        ns = LangGraphStoreBackend.namespace(
            MemoryScope(org_id="acme", user_id="u1"), MemoryType.PREFERENCE
        )
        assert ns == ("prismal", "acme", "u1", "preference")
        assert LangGraphStoreBackend.namespace(MemoryScope("acme"), MemoryType.EPISODIC) == (
            "prismal",
            "acme",
            "_",
            "episodic",
        )

    def test_rejects_legacy_namespace_inside_prismal_prefix(self, lts: LongTermMemoryStore) -> None:
        with pytest.raises(ValueError, match="prismal"):
            LangGraphStoreBackend(lts, legacy_namespaces=[("prismal", "x")])

    async def test_v2_write_visible_to_unchanged_legacy_recall_facts(
        self, lts: LongTermMemoryStore
    ) -> None:
        backend = LangGraphStoreBackend(lts)
        rec = _rec("mem_1", "prefers dark mode", mtype=MemoryType.PREFERENCE)
        assert await backend.put(rec) == "mem_1"
        facts = await lts.recall_facts(("prismal", "acme", "u1", "preference"), query="dark")
        assert [(f["key"], f["value"], f["user_id"]) for f in facts] == [
            ("mem_1", "prefers dark mode", "u1")
        ]

    async def test_round_trip_and_pii_sanitized(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        edge = MemoryEdge("mem_1", "mem_0", RelationKind.SUPPORTS, 0.7, valid_from=2.0)
        rec = _rec("mem_1", "deploy friday", mtype=MemoryType.PROCEDURAL, edges=(edge,))
        await backend.put(rec)
        assert await backend.get("mem_1", scope=MemoryScope("acme", user_id="u1")) == rec
        await backend.put(_rec("mem_pii", "mail me at jane.doe@example.com"))
        got = await backend.get("mem_pii", scope=MemoryScope("acme"))
        assert got is not None
        assert "jane.doe@example.com" not in got.content

    async def test_tenant_isolation(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        await backend.put(_rec("a1", "secret plan", org="acme"))
        await backend.put(_rec("g1", "secret plan", org="globex"))
        acme = MemoryScope("acme")
        assert await backend.get("g1", scope=acme) is None
        hits = await backend.search("secret plan", scope=acme, types=None, k=5)
        assert [r.id for r, _ in hits] == ["a1"]
        assert [r.id for r in await backend.list(scope=acme)] == ["a1"]
        assert await backend.purge(["g1"], scope=acme, _authorized=RULE) == 0
        assert await backend.get("g1", scope=MemoryScope("globex")) is not None

    async def test_search_filters(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        await backend.put(_rec("p1", "likes green tea", mtype=MemoryType.PREFERENCE))
        await backend.put(_rec("s1", "green tea is a drink"))
        await backend.put(_rec("old", "likes green tea a lot", superseded_by="p1"))
        await backend.put(_rec("u2", "green tea for u2", user="u2"))
        await backend.put(_rec("exp", "green tea expired", expires_at=time.time() - 5))
        scope = MemoryScope("acme", user_id="u1")
        hits = await backend.search(
            "green tea", scope=scope, types=frozenset({MemoryType.PREFERENCE}), k=5
        )
        assert [r.id for r, _ in hits] == ["p1"]
        every = await backend.search(
            "green tea", scope=scope, types=None, k=5, include_superseded=True
        )
        assert {r.id for r, _ in every} == {"p1", "s1", "old"}
        assert await backend.search("green tea", scope=scope, types=None, k=0) == []
        assert await backend.search("zzz", scope=scope, types=None, k=5) == []

    async def test_update_moves_namespace_when_type_changes(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        await backend.put(_rec("r1", "tabs over spaces", mtype=MemoryType.EPISODIC))
        await backend.update(_rec("r1", "tabs over spaces", mtype=MemoryType.PREFERENCE, version=2))
        assert await lts.recall_facts(("prismal", "acme", "u1", "episodic"), query="") == []
        got = await backend.get("r1", scope=MemoryScope("acme"))
        assert got is not None
        assert (got.type, got.version) == (MemoryType.PREFERENCE, 2)
        with pytest.raises(KeyError):
            await backend.update(_rec("nope", "x"))

    async def test_scan_is_unbounded_by_default(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts, page_size=2)
        for i in range(7):
            await backend.put(_rec(f"r{i}", f"fact {i}"))
        assert len(await backend.list(scope=MemoryScope("acme"))) == 7

    async def test_scan_cap_raises_instead_of_truncating(self, lts: LongTermMemoryStore) -> None:
        # Erasure/retention list through the same scan: a silent truncation would
        # leave records behind (review MED6), so hitting the cap fails closed.
        backend = LangGraphStoreBackend(lts, page_size=2, max_scan=3)
        for i in range(5):
            await backend.put(_rec(f"r{i}", f"fact {i}"))
        with pytest.raises(MemoryScanLimitError):
            await backend.list(scope=MemoryScope("acme"))

    async def test_scan_cap_not_hit_when_items_fit(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts, page_size=2, max_scan=4)
        for i in range(4):
            await backend.put(_rec(f"r{i}", f"fact {i}"))
        assert len(await backend.list(scope=MemoryScope("acme"))) == 4

    async def test_list_since_limit_and_purge(self, lts: LongTermMemoryStore) -> None:
        backend = LangGraphStoreBackend(lts)
        for i, ts in enumerate((100.0, 200.0, 300.0), start=1):
            await backend.put(_rec(f"r{i}", f"fact {i}", provenance=_prov(created_at=ts)))
        scope = MemoryScope("acme")
        assert [r.id for r in await backend.list(scope=scope, since=150.0)] == ["r2", "r3"]
        assert [r.id for r in await backend.list(scope=scope, limit=1)] == ["r1"]
        with pytest.raises(TypeError):
            await backend.purge(["r1"], scope=scope, _authorized=None)  # type: ignore[arg-type]
        assert await backend.purge(["r1", "r1", "zz"], scope=scope, _authorized=RULE) == 1
        assert [r.id for r in await backend.list(scope=scope)] == ["r2", "r3"]

    async def test_legacy_fact_maps_to_semantic_legacy_unknown(
        self, lts: LongTermMemoryStore
    ) -> None:
        await lts.store_fact("u1", preferences_namespace("u1"), "theme", "dark mode")
        backend = LangGraphStoreBackend(
            lts, legacy_namespaces=[("preferences",)], legacy_org_id="acme"
        )
        listed = await backend.list(scope=MemoryScope("acme"))
        assert len(listed) == 1
        rec = listed[0]
        assert rec.type is MemoryType.SEMANTIC
        assert rec.provenance.origin == "legacy"
        assert rec.provenance.trust == "unknown"
        assert rec.scope == MemoryScope("acme", user_id="u1")
        assert rec.content == "dark mode"
        assert await backend.get(rec.id, scope=MemoryScope("acme")) == rec
        hits = await backend.search("dark", scope=MemoryScope("acme"), types=None, k=3)
        assert [r.id for r, _ in hits] == [rec.id]
        assert await backend.purge([rec.id], scope=MemoryScope("acme"), _authorized=RULE) == 1
        assert await lts.recall_facts(preferences_namespace("u1"), query="") == []

    async def test_legacy_facts_invisible_without_matching_legacy_org(
        self, lts: LongTermMemoryStore
    ) -> None:
        await lts.store_fact("u1", preferences_namespace("u1"), "theme", "dark mode")
        hidden = LangGraphStoreBackend(lts, legacy_namespaces=[("preferences",)])
        assert await hidden.list(scope=MemoryScope("acme")) == []
        other = LangGraphStoreBackend(
            lts, legacy_namespaces=[("preferences",)], legacy_org_id="globex"
        )
        assert await other.list(scope=MemoryScope("acme")) == []
        assert await other.get("legacy:bogus", scope=MemoryScope("acme")) is None
