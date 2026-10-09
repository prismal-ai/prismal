"""SqliteMemoryGraph — temporal edges, as_of views, tenant scope (MEM6-01, SPEC-MEM-GRF-001)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from prismal.core.exceptions import MemoryTenancyError
from prismal.memory.v2.graph import SqliteMemoryGraph
from prismal.memory.v2.ports import MemoryGraphPort
from prismal.memory.v2.types import (
    ForgetActor,
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RelationKind,
)

ORG_A = MemoryScope(org_id="org-a")
ORG_B = MemoryScope(org_id="org-b")
RULE = ForgetActor(kind="rule", principal="retention")


def _record(rid: str, scope: MemoryScope = ORG_A, *, tainted: bool = False) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        type=MemoryType.SEMANTIC,
        content=f"content of {rid}",
        scope=scope,
        provenance=Provenance(
            origin="conversation",
            source_message_ids=("m1",),
            session_id="s1",
            agent="coder",
            created_at=100.0,
            tainted=tainted,
            trust="untrusted" if tainted else "trusted",
        ),
        confidence=0.9,
    )


def _edge(
    src: str,
    dst: str,
    relation: RelationKind = RelationKind.SUPPORTS,
    *,
    valid_from: float = 10.0,
    valid_to: float | None = None,
) -> MemoryEdge:
    return MemoryEdge(
        src_id=src,
        dst_id=dst,
        relation=relation,
        confidence=0.8,
        valid_from=valid_from,
        valid_to=valid_to,
    )


@pytest.fixture
def graph() -> Iterator[SqliteMemoryGraph]:
    g = SqliteMemoryGraph()
    yield g
    g.close()


def test_conforms_to_port(graph: SqliteMemoryGraph) -> None:
    assert isinstance(graph, MemoryGraphPort)


async def test_add_edge_then_neighbors_current_view(graph: SqliteMemoryGraph) -> None:
    await graph.upsert_node(_record("a"))
    await graph.upsert_node(_record("b"))
    await graph.add_edge(_edge("a", "b"), scope=ORG_A)

    out = await graph.neighbors("a", scope=ORG_A, as_of=None)
    assert [(e.src_id, e.dst_id, e.relation) for e in out] == [("a", "b", RelationKind.SUPPORTS)]
    # Incoming edges are neighbors too.
    assert [e.src_id for e in await graph.neighbors("b", scope=ORG_A, as_of=None)] == ["a"]


async def test_invalidate_never_deletes_and_as_of_sees_history(graph: SqliteMemoryGraph) -> None:
    await graph.add_edge(_edge("a", "b", valid_from=10.0), scope=ORG_A)
    await graph.invalidate_edge("a", "b", at=50.0, scope=ORG_A)

    assert await graph.neighbors("a", scope=ORG_A, as_of=None) == []
    past = await graph.neighbors("a", scope=ORG_A, as_of=20.0)
    assert len(past) == 1
    assert past[0].valid_to == 50.0
    assert await graph.neighbors("a", scope=ORG_A, as_of=50.0) == []
    assert await graph.neighbors("a", scope=ORG_A, as_of=5.0) == []  # before valid_from
    assert graph.edge_count(scope=ORG_A) == 1  # invalidated, not deleted


async def test_invalidate_only_touches_open_edges(graph: SqliteMemoryGraph) -> None:
    await graph.add_edge(_edge("a", "b", valid_from=1.0, valid_to=5.0), scope=ORG_A)
    await graph.add_edge(_edge("a", "b", valid_from=6.0), scope=ORG_A)
    await graph.invalidate_edge("a", "b", at=9.0, scope=ORG_A)
    history = await graph.neighbors("a", scope=ORG_A, as_of=3.0)
    assert [e.valid_to for e in history] == [5.0]  # closed edge keeps its own valid_to


async def test_relation_filter(graph: SqliteMemoryGraph) -> None:
    await graph.add_edge(_edge("a", "b", RelationKind.SUPPORTS), scope=ORG_A)
    await graph.add_edge(_edge("a", "c", RelationKind.CONTRADICTS), scope=ORG_A)
    out = await graph.neighbors(
        "a", scope=ORG_A, as_of=None, relations=frozenset({RelationKind.CONTRADICTS})
    )
    assert [e.dst_id for e in out] == ["c"]


async def test_tenant_isolation(graph: SqliteMemoryGraph) -> None:
    await graph.add_edge(_edge("a", "b"), scope=ORG_A)
    await graph.add_edge(_edge("a", "z"), scope=ORG_B)
    assert [e.dst_id for e in await graph.neighbors("a", scope=ORG_A, as_of=None)] == ["b"]
    assert [e.dst_id for e in await graph.neighbors("a", scope=ORG_B, as_of=None)] == ["z"]
    await graph.invalidate_edge("a", "b", at=99.0, scope=ORG_B)  # other tenant: no effect
    assert len(await graph.neighbors("a", scope=ORG_A, as_of=None)) == 1


async def test_narrow_scope_hides_edges_to_other_users_nodes(graph: SqliteMemoryGraph) -> None:
    alice = MemoryScope(org_id="org-a", user_id="alice")
    bob = MemoryScope(org_id="org-a", user_id="bob")
    await graph.upsert_node(_record("a", alice))
    await graph.upsert_node(_record("b", alice))
    await graph.upsert_node(_record("c", bob))
    await graph.add_edge(_edge("a", "b"), scope=alice)
    await graph.add_edge(_edge("a", "c"), scope=ORG_A)
    assert [e.dst_id for e in await graph.neighbors("a", scope=alice, as_of=None)] == ["b"]
    # Bob cannot even see alice's node as an anchor.
    assert await graph.neighbors("a", scope=bob, as_of=None) == []
    # Org-wide scope sees both.
    assert len(await graph.neighbors("a", scope=ORG_A, as_of=None)) == 2


async def test_upsert_node_rejects_cross_tenant_overwrite(graph: SqliteMemoryGraph) -> None:
    await graph.upsert_node(_record("a", ORG_A))
    await graph.upsert_node(_record("a", ORG_A))  # same tenant: idempotent upsert
    assert graph.node_count(scope=ORG_A) == 1
    await graph.upsert_node(_record("a", ORG_B))  # same id, other tenant: separate node
    assert graph.node_count(scope=ORG_A) == 1
    assert graph.node_count(scope=ORG_B) == 1


async def test_purge_nodes_erases_nodes_and_incident_edges(graph: SqliteMemoryGraph) -> None:
    for rid in ("a", "b", "c"):
        await graph.upsert_node(_record(rid))
    await graph.add_edge(_edge("a", "b"), scope=ORG_A)
    await graph.add_edge(_edge("c", "a"), scope=ORG_A)
    await graph.add_edge(_edge("b", "c"), scope=ORG_A)
    await graph.add_edge(_edge("a", "b"), scope=ORG_B)

    removed = await graph.purge_nodes(["a"], scope=ORG_A, _authorized=RULE)
    assert removed == 1
    assert graph.node_count(scope=ORG_A) == 2
    assert await graph.neighbors("a", scope=ORG_A, as_of=10.0) == []
    assert [e.dst_id for e in await graph.neighbors("b", scope=ORG_A, as_of=None)] == ["c"]
    assert len(await graph.neighbors("a", scope=ORG_B, as_of=None)) == 1  # other tenant kept


async def test_purge_nodes_requires_actor(graph: SqliteMemoryGraph) -> None:
    with pytest.raises(TypeError):
        await graph.purge_nodes(["a"], scope=ORG_A, _authorized=None)  # type: ignore[arg-type]


async def test_purge_nodes_empty_is_noop(graph: SqliteMemoryGraph) -> None:
    assert await graph.purge_nodes([], scope=ORG_A, _authorized=RULE) == 0


def test_rejects_scope_without_org() -> None:
    with pytest.raises(MemoryTenancyError):
        MemoryScope(org_id="")


async def test_file_backed_persists(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "graph.sqlite"
    g1 = SqliteMemoryGraph(path)
    await g1.add_edge(_edge("a", "b"), scope=ORG_A)
    g1.close()
    g2 = SqliteMemoryGraph(path)
    assert len(await g2.neighbors("a", scope=ORG_A, as_of=None)) == 1
    g2.close()


async def test_known_user_ids_per_tenant(graph: SqliteMemoryGraph) -> None:
    await graph.upsert_node(_record("a"))
    await graph.upsert_node(_record("b", MemoryScope(org_id="org-a", user_id="alice")))
    await graph.upsert_node(_record("c", MemoryScope(org_id="org-b", user_id="bob")))
    assert graph.known_user_ids(scope=ORG_A) == ["alice"]
    assert graph.known_user_ids(scope=ORG_B) == ["bob"]
