"""Value objects, ports and the in-memory fake backend (SPEC-MEM-TYP-001/002, PRT-001/002)."""

from __future__ import annotations

import dataclasses

import pytest

from prismal.core.exceptions import MemoryTenancyError
from prismal.decisions.types import RiskTier
from prismal.memory.v2.adapters.in_memory import InMemoryBackend
from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort, MemoryPort
from prismal.memory.v2.types import (
    ForgetActor,
    ForgetSelector,
    MemoryCandidate,
    MemoryContext,
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RelationKind,
    WriteOutcome,
    new_record_id,
)


def _prov(**kw: object) -> Provenance:
    base: dict[str, object] = {
        "origin": "conversation",
        "source_message_ids": ("m1",),
        "session_id": "s1",
        "agent": "coder",
        "created_at": 1_000.0,
        "tainted": False,
        "trust": "trusted",
    }
    base.update(kw)
    return Provenance(**base)  # type: ignore[arg-type]


def _rec(content: str, *, org: str = "acme", **kw: object) -> MemoryRecord:
    fields: dict[str, object] = {
        "id": new_record_id(),
        "type": MemoryType.SEMANTIC,
        "content": content,
        "scope": MemoryScope(org_id=org),
        "provenance": _prov(),
        "confidence": 0.9,
    }
    fields.update(kw)
    return MemoryRecord(**fields)  # type: ignore[arg-type]


_RULE = ForgetActor(kind="rule", principal="retention:episodic")


class TestTypes:
    def test_empty_org_rejected(self) -> None:
        with pytest.raises(MemoryTenancyError):
            MemoryScope(org_id="")
        with pytest.raises(MemoryTenancyError):
            MemoryScope(org_id="   ")

    def test_values_are_frozen(self) -> None:
        for cls in (MemoryScope, Provenance, MemoryRecord, MemoryEdge, WriteOutcome, ForgetActor):
            assert dataclasses.fields(cls)
            assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_enums(self) -> None:
        assert [t.value for t in MemoryType] == ["episodic", "semantic", "procedural", "preference"]
        assert [r.value for r in RelationKind] == [
            "supports",
            "contradicts",
            "updates",
            "unrelated",
        ]

    def test_edge_never_unrelated(self) -> None:
        with pytest.raises(ValueError, match="UNRELATED"):
            MemoryEdge(
                src_id="a",
                dst_id="b",
                relation=RelationKind.UNRELATED,
                confidence=1.0,
                valid_from=0.0,
            )

    def test_human_actor_needs_approval(self) -> None:
        with pytest.raises(ValueError, match="approval_id"):
            ForgetActor(kind="human", principal="alice")
        assert ForgetActor(kind="human", principal="alice", approval_id="r1").approval_id == "r1"

    def test_context_defaults(self) -> None:
        ctx = MemoryContext(scope=MemoryScope(org_id="acme"), session_id="s", actor="coder")
        assert ctx.risk_tier is RiskTier.LOW

    def test_candidate_defaults(self) -> None:
        c = MemoryCandidate(content="x", source_message_ids=("m",))
        assert c.origin == "conversation" and c.suggested_type is None and dict(c.metadata) == {}

    def test_record_ids_unique_and_sortable(self) -> None:
        ids = [new_record_id() for _ in range(50)]
        assert len(set(ids)) == 50
        assert ids == sorted(ids)

    def test_scope_covers(self) -> None:
        org = MemoryScope(org_id="acme")
        bob = MemoryScope(org_id="acme", user_id="bob")
        alice = MemoryScope(org_id="acme", user_id="alice")
        assert bob.covers(org)  # org-wide records are visible to bob
        assert bob.covers(bob)
        assert not bob.covers(alice)
        assert org.covers(alice)  # an org-wide request sees every user's records
        assert not org.covers(MemoryScope(org_id="other"))

    def test_selector_defaults(self) -> None:
        sel = ForgetSelector()
        assert sel.include_derived is True and sel.record_ids == ()


class TestPorts:
    def test_in_memory_backend_conforms(self) -> None:
        assert isinstance(InMemoryBackend(), MemoryBackendPort)

    def test_ports_are_runtime_checkable(self) -> None:
        assert not isinstance(object(), MemoryPort)
        assert not isinstance(object(), MemoryGraphPort)


class TestInMemoryBackend:
    async def test_put_get_scoped(self) -> None:
        b = InMemoryBackend()
        r = _rec("user likes tea")
        assert await b.put(r) == r.id
        assert await b.get(r.id, scope=MemoryScope(org_id="acme")) == r
        assert await b.get(r.id, scope=MemoryScope(org_id="other")) is None

    async def test_search_ranks_by_overlap_and_filters_tenant(self) -> None:
        b = InMemoryBackend()
        tea = _rec("user likes green tea")
        coffee = _rec("user hates coffee")
        foreign = _rec("user likes green tea", org="other")
        for r in (tea, coffee, foreign):
            await b.put(r)
        hits = await b.search("green tea", scope=MemoryScope(org_id="acme"), types=None, k=5)
        assert [r.id for r, _ in hits][0] == tea.id
        assert foreign.id not in {r.id for r, _ in hits}
        assert all(0.0 <= s <= 1.0 for _, s in hits)

    async def test_search_type_filter_and_superseded(self) -> None:
        b = InMemoryBackend()
        old = _rec("tea", superseded_by="x")
        ep = _rec("tea", type=MemoryType.EPISODIC)
        await b.put(old)
        await b.put(ep)
        scope = MemoryScope(org_id="acme")
        assert [r.id for r, _ in await b.search("tea", scope=scope, types=None, k=5)] == [ep.id]
        sem = await b.search(
            "tea", scope=scope, types=frozenset({MemoryType.SEMANTIC}), k=5, include_superseded=True
        )
        assert [r.id for r, _ in sem] == [old.id]

    async def test_update_and_list_since(self) -> None:
        b = InMemoryBackend()
        r = _rec("a", provenance=_prov(created_at=10.0))
        later = _rec("b", provenance=_prov(created_at=20.0))
        await b.put(r)
        await b.put(later)
        await b.update(dataclasses.replace(r, version=2))
        scope = MemoryScope(org_id="acme")
        assert (await b.get(r.id, scope=scope)).version == 2  # type: ignore[union-attr]
        assert [x.id for x in await b.list(scope=scope, since=15.0)] == [later.id]
        assert len(await b.list(scope=scope, limit=1)) == 1

    async def test_update_rejects_unknown_record(self) -> None:
        with pytest.raises(KeyError):
            await InMemoryBackend().update(_rec("ghost"))

    async def test_purge_scoped_and_requires_actor(self) -> None:
        b = InMemoryBackend()
        mine, theirs = _rec("a"), _rec("b", org="other")
        await b.put(mine)
        await b.put(theirs)
        n = await b.purge([mine.id, theirs.id], scope=MemoryScope(org_id="acme"), _authorized=_RULE)
        assert n == 1
        assert await b.get(theirs.id, scope=MemoryScope(org_id="other")) is not None
        with pytest.raises(TypeError):
            await b.purge([theirs.id], scope=MemoryScope(org_id="other"), _authorized=None)  # type: ignore[arg-type]

    async def test_user_scope_isolation(self) -> None:
        b = InMemoryBackend()
        alice = _rec("pin is 1234", scope=MemoryScope(org_id="acme", user_id="alice"))
        await b.put(alice)
        bob = MemoryScope(org_id="acme", user_id="bob")
        assert await b.search("pin", scope=bob, types=None, k=5) == []
        assert await b.get(alice.id, scope=bob) is None
