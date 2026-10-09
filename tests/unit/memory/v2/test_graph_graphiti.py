"""GraphitiMemoryGraph with an injected fake client (MEM6-02, SPEC-MEM-GRF-001).

No ``graphiti_core`` install is needed: the adapter only touches the injected
client (MEM0-03: ``add_episode`` / ``search`` / ``remove_episode``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from prismal.core.exceptions import MemoryBackendUnavailable, MemoryErasureIncomplete
from prismal.memory.v2.graph import GraphFact, GraphitiMemoryGraph, require_graphiti
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

if TYPE_CHECKING:
    from pathlib import Path

ORG_A = MemoryScope(org_id="org-a")
ORG_B = MemoryScope(org_id="org-b")
RULE = ForgetActor(kind="rule", principal="retention")


def _ts(x: float) -> datetime:
    return datetime.fromtimestamp(x, tz=UTC)


@dataclass
class _Episode:
    uuid: str


@dataclass
class _AddResult:
    episode: _Episode


@dataclass
class _EntityEdge:
    uuid: str
    fact: str
    group_id: str
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None


@dataclass
class FakeGraphiti:
    episodes: list[dict[str, Any]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    facts: list[_EntityEdge] = field(default_factory=list)
    search_calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    async def add_episode(self, **kwargs: Any) -> _AddResult:
        if self.fail:
            raise RuntimeError("neo4j down")
        self.episodes.append(kwargs)
        return _AddResult(episode=_Episode(uuid=f"ep-{len(self.episodes)}"))

    async def search(
        self, query: str, *, group_ids: list[str] | None = None, num_results: int = 10
    ) -> list[_EntityEdge]:
        if self.fail:
            raise RuntimeError("neo4j down")
        self.search_calls.append({"query": query, "group_ids": group_ids, "k": num_results})
        return [f for f in self.facts if group_ids is None or f.group_id in group_ids]

    async def remove_episode(self, episode_uuid: str) -> None:
        self.removed.append(episode_uuid)


def _record(
    rid: str, scope: MemoryScope = ORG_A, *, tainted: bool = False, content: str | None = None
) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        type=MemoryType.EPISODIC,
        content=content if content is not None else f"content of {rid}",
        scope=scope,
        provenance=Provenance(
            origin="conversation",
            source_message_ids=("m1",),
            session_id="s1",
            agent="coder",
            created_at=1_000.0,
            tainted=tainted,
            trust="untrusted" if tainted else "trusted",
        ),
        confidence=0.9,
    )


def test_absent_extra_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "graphiti_core", None)
    with pytest.raises(MemoryBackendUnavailable, match="graph-memory"):
        require_graphiti()
    with pytest.raises(MemoryBackendUnavailable, match="graph-memory"):
        GraphitiMemoryGraph()


def test_present_extra_without_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "graphiti_core", type(sys)("graphiti_core"))
    require_graphiti()  # extra present: no error
    with pytest.raises(MemoryBackendUnavailable, match="injected"):
        GraphitiMemoryGraph()


def test_conforms_to_port() -> None:
    assert isinstance(GraphitiMemoryGraph(FakeGraphiti()), MemoryGraphPort)


async def test_upsert_node_adds_episode_to_tenant_group() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("r1"))
    await graph.upsert_node(_record("r2", ORG_B))

    first, second = client.episodes
    assert first["name"] == "r1"
    assert first["episode_body"] == "content of r1"
    assert first["reference_time"] == _ts(1_000.0)
    assert first["group_id"] != second["group_id"]
    assert first["group_id"].startswith("prismal-")
    assert "org-a" not in first["group_id"]  # hashed, collision-safe
    # Re-upserting identical content does not duplicate the episode.
    await graph.upsert_node(_record("r1"))
    assert len(client.episodes) == 2
    await graph.upsert_node(_record("r1", content="changed"))
    assert len(client.episodes) == 3


async def test_tainted_records_never_reach_graphiti() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("t1", tainted=True))
    assert client.episodes == []


async def test_client_failure_is_fail_open() -> None:
    client = FakeGraphiti(fail=True)
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("r1"))  # no raise
    assert await graph.search_facts("x", scope=ORG_A) == []


async def test_explicit_edges_are_temporal_and_tenant_scoped() -> None:
    graph = GraphitiMemoryGraph(FakeGraphiti())
    edge = MemoryEdge(
        src_id="a", dst_id="b", relation=RelationKind.UPDATES, confidence=0.7, valid_from=10.0
    )
    await graph.add_edge(edge, scope=ORG_A)
    await graph.invalidate_edge("a", "b", at=20.0, scope=ORG_A)
    assert await graph.neighbors("a", scope=ORG_A, as_of=None) == []
    assert len(await graph.neighbors("a", scope=ORG_A, as_of=15.0)) == 1
    assert await graph.neighbors("a", scope=ORG_B, as_of=15.0) == []


async def test_search_facts_as_of_and_tenant_groups() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("r1"))
    group_a = client.episodes[0]["group_id"]
    client.facts = [
        _EntityEdge("e1", "alice lives in Paris", group_a, _ts(10), _ts(20)),
        _EntityEdge("e2", "alice lives in Rome", group_a, _ts(20)),
        _EntityEdge("e3", "other tenant fact", "prismal-other", _ts(1)),
        _EntityEdge("e4", "expired", group_a, _ts(1), None, _ts(5)),
    ]
    now = await graph.search_facts("where does alice live", scope=ORG_A)
    assert [f.fact for f in now] == ["alice lives in Rome"]
    past = await graph.search_facts("where does alice live", scope=ORG_A, as_of=15.0)
    assert [f.fact for f in past] == ["alice lives in Paris"]
    assert isinstance(past[0], GraphFact)
    assert past[0].valid_at == 10.0 and past[0].invalid_at == 20.0
    assert client.search_calls[0]["group_ids"] == [group_a]


async def test_user_scoped_groups() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    alice = MemoryScope(org_id="org-a", user_id="alice")
    await graph.upsert_node(_record("r0"))
    await graph.upsert_node(_record("r1", alice))
    org_group, alice_group = client.episodes[0]["group_id"], client.episodes[1]["group_id"]
    assert org_group != alice_group

    await graph.search_facts("q", scope=alice)
    assert client.search_calls[-1]["group_ids"] == [org_group, alice_group]
    bob = MemoryScope(org_id="org-a", user_id="bob")
    await graph.search_facts("q", scope=bob)
    assert alice_group not in client.search_calls[-1]["group_ids"]
    await graph.search_facts("q", scope=ORG_A)  # org-wide covers known user groups
    assert set(client.search_calls[-1]["group_ids"]) == {org_group, alice_group}


async def test_purge_nodes_removes_episodes_and_edges() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("r1"))
    await graph.upsert_node(_record("r2"))
    await graph.add_edge(
        MemoryEdge(
            src_id="r1", dst_id="r2", relation=RelationKind.SUPPORTS, confidence=1, valid_from=1
        ),
        scope=ORG_A,
    )
    removed = await graph.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE)
    assert removed == 1
    assert client.removed == ["ep-1"]
    assert await graph.neighbors("r2", scope=ORG_A, as_of=None) == []
    # Purging in another tenant does not touch org-a's episode.
    await graph.purge_nodes(["r2"], scope=ORG_B, _authorized=RULE)
    assert client.removed == ["ep-1"]
    graph.close()


async def test_purge_without_remove_episode_fails_when_episode_known() -> None:
    # Erasure must not report success while the content still lives in Graphiti.
    class _NoRemove(FakeGraphiti):
        remove_episode = None  # type: ignore[assignment]

    graph = GraphitiMemoryGraph(_NoRemove())
    await graph.upsert_node(_record("r1"))
    with pytest.raises(MemoryErasureIncomplete):
        await graph.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE)


async def test_purge_without_remove_episode_ok_when_nothing_to_remove() -> None:
    class _NoRemove(FakeGraphiti):
        remove_episode = None  # type: ignore[assignment]

    graph = GraphitiMemoryGraph(_NoRemove())
    await graph.upsert_node(_record("r1", tainted=True))  # never sent to Graphiti
    assert await graph.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE) == 1


async def test_episode_index_survives_restart(tmp_path: Path) -> None:
    # Review MED4: the record→episode map was in-process only, so after a restart
    # erasure left the episode (and its content) in Graphiti.
    client = FakeGraphiti()
    path = tmp_path / "edges.db"
    first = GraphitiMemoryGraph(client, edges_path=path)
    await first.upsert_node(_record("r1"))
    first.close()
    second = GraphitiMemoryGraph(client, edges_path=path)
    await second.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE)
    assert client.removed == ["ep-1"]
    second.close()


async def test_unchanged_content_not_re_added_after_restart(tmp_path: Path) -> None:
    client = FakeGraphiti()
    path = tmp_path / "edges.db"
    first = GraphitiMemoryGraph(client, edges_path=path)
    await first.upsert_node(_record("r1"))
    first.close()
    second = GraphitiMemoryGraph(client, edges_path=path)
    await second.upsert_node(_record("r1"))
    assert len(client.episodes) == 1
    second.close()


async def test_failed_episode_removal_raises_and_is_retryable() -> None:
    class _Flaky(FakeGraphiti):
        calls: int = 0

        async def remove_episode(self, episode_uuid: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("neo4j down")
            self.removed.append(episode_uuid)

    client = _Flaky()
    graph = GraphitiMemoryGraph(client)
    await graph.upsert_node(_record("r1"))
    with pytest.raises(MemoryErasureIncomplete):
        await graph.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE)
    await graph.purge_nodes(["r1"], scope=ORG_A, _authorized=RULE)
    assert client.removed == ["ep-1"]


async def test_narrow_purge_never_removes_episodes_outside_its_scope() -> None:
    client = FakeGraphiti()
    graph = GraphitiMemoryGraph(client)
    alice = MemoryScope(org_id="org-a", user_id="alice")
    bob = MemoryScope(org_id="org-a", user_id="bob")
    await graph.upsert_node(_record("mine", alice))  # ep-1
    await graph.upsert_node(_record("bobs", bob))  # ep-2
    await graph.upsert_node(_record("shared", ORG_A))  # ep-3
    await graph.purge_nodes(["mine", "bobs", "shared"], scope=alice, _authorized=RULE)
    assert client.removed == ["ep-1"]
    graph.close()
