"""ForgetPolicy — rule / HITL-only deletion (Phase MEM — SPEC-MEM-DEL-001, MEM1-09, MEM4-05)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import MemoryForgetDenied, MemoryTenancyError
from prismal.memory.v2.adapters.in_memory import InMemoryBackend
from prismal.memory.v2.forget import (
    ForgetPolicy,
    ForgetRequest,
    MemoryErasureIncomplete,
    within_erasure_scope,
)
from prismal.memory.v2.types import (
    ForgetActor,
    ForgetSelector,
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RelationKind,
)
from prismal.monitoring.otel import OTelManager

DAY = 86_400.0
NOW = 1_000 * DAY
ORG = MemoryScope(org_id="acme")
OTHER = MemoryScope(org_id="globex")


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class FakeClock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _rec(
    rid: str,
    *,
    content: str = "fact",
    scope: MemoryScope = ORG,
    type_: MemoryType = MemoryType.EPISODIC,
    created_at: float = NOW,
    sources: tuple[str, ...] = (),
    superseded_by: str | None = None,
    edges: tuple[MemoryEdge, ...] = (),
) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        type=type_,
        content=content,
        scope=scope,
        provenance=Provenance(
            origin="conversation",
            source_message_ids=sources,
            session_id="s",
            agent="a",
            created_at=created_at,
            tainted=False,
            trust="trusted",
        ),
        confidence=0.9,
        superseded_by=superseded_by,
        edges=edges,
    )


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "memory_retention_days_by_type": {"episodic": 90},
        "memory_superseded_retention_days": 30,
    }
    base.update(kw)
    return Settings(**base)


def _policy(
    backend: InMemoryBackend | None = None, *, audit: FakeAudit | None = None, **kw: Any
) -> ForgetPolicy:
    return ForgetPolicy(
        backend=backend or InMemoryBackend(),
        graph=kw.pop("graph", None),
        audit=audit or FakeAudit(),
        permissions=kw.pop("permissions", None),
        settings=kw.pop("settings", _settings()),
        clock=kw.pop("clock", FakeClock()),
        **kw,
    )


async def _ids(backend: InMemoryBackend, scope: MemoryScope = ORG) -> set[str]:
    return {r.id for r in await backend.list(scope=scope)}


# ── request ──────────────────────────────────────────────────────────────────


def test_request_creates_pending_request() -> None:
    policy = _policy()
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent:coder")
    assert isinstance(req, ForgetRequest)
    assert req.status == "pending"
    assert req.selector == sel
    assert req.requested_by == "agent:coder"
    assert req.created_at == NOW
    assert req.request_id
    assert policy.get_request(req.request_id) == req
    assert policy.pending() == (req,)


def test_request_without_scope_is_rejected() -> None:
    with pytest.raises(MemoryTenancyError):
        _policy().request(ForgetSelector(record_ids=("m1",)), requested_by="u")


def test_request_is_audited_hash_first() -> None:
    audit = FakeAudit()
    policy = _policy(audit=audit)
    policy.request(ForgetSelector(record_ids=("m1",), scope=ORG, reason="secret"), requested_by="u")
    event, payload = audit.events[-1]
    assert event == "memory.forget_requested"
    assert "selector_hash" in payload
    assert "secret" not in repr(payload)
    assert "m1" not in repr(payload)


# ── actor matrix: denials ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "actor",
    [
        "agent:coder",
        None,
        {"kind": "human", "principal": "x", "approval_id": "a"},
        type("LLM", (), {"kind": "llm", "principal": "gpt"})(),
    ],
)
async def test_non_forget_actor_is_denied(actor: object) -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    audit = FakeAudit()
    policy = _policy(backend, audit=audit)
    with (
        patch.object(OTelManager, "increment_counter") as inc,
        pytest.raises(MemoryForgetDenied),
    ):
        await policy.execute(ForgetSelector(record_ids=("m1",), scope=ORG), actor=actor)  # type: ignore[arg-type]
    assert await _ids(backend) == {"m1"}
    assert audit.events[-1][0] == "memory.forget"
    assert audit.events[-1][1]["outcome"] == "denied"
    inc.assert_any_call("memory_forget", attributes={"actor": "other", "outcome": "denied"})


async def test_rule_actor_with_handcrafted_selector_is_denied() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend)
    sel = ForgetSelector(scope=ORG, types=frozenset({MemoryType.EPISODIC}), older_than=NOW)
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(sel, actor=ForgetActor(kind="rule", principal="retention"))
    assert await _ids(backend) == {"m1"}


async def test_human_with_unknown_approval_is_denied() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    with (
        patch.object(OTelManager, "increment_counter") as inc,
        pytest.raises(MemoryForgetDenied),
    ):
        await policy.execute(
            sel, actor=ForgetActor(kind="human", principal="alice", approval_id="nope")
        )
    inc.assert_any_call("memory_forget", attributes={"actor": "human", "outcome": "denied"})
    assert await _ids(backend) == {"m1"}


async def test_human_with_pending_request_is_denied() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(
            sel, actor=ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
        )
    assert await _ids(backend) == {"m1"}


async def test_human_with_approval_for_other_selector_is_denied() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    await backend.put(_rec("m2"))
    policy = _policy(backend)
    req = policy.request(ForgetSelector(record_ids=("m1",), scope=ORG), requested_by="agent")
    await policy.approve(req.request_id, principal="alice")
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(
            ForgetSelector(record_ids=("m1", "m2"), scope=ORG),
            actor=ForgetActor(kind="human", principal="alice", approval_id=req.request_id),
        )
    assert await _ids(backend) == {"m1", "m2"}


# ── HITL path ────────────────────────────────────────────────────────────────


async def test_human_with_approved_request_purges_and_marks_executed() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    await backend.put(_rec("m2"))
    audit = FakeAudit()
    policy = _policy(backend, audit=audit)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG, include_derived=False)
    req = policy.request(sel, requested_by="agent")
    approved = await policy.approve(req.request_id, principal="alice")
    assert approved.status == "approved"
    actor = ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
    with patch.object(OTelManager, "increment_counter") as inc:
        n = await policy.execute(sel, actor=actor)
    assert n == 1
    assert await _ids(backend) == {"m2"}
    got = policy.get_request(req.request_id)
    assert got is not None
    assert got.status == "executed"
    inc.assert_any_call("memory_forget", attributes={"actor": "human", "outcome": "executed"})
    event, payload = audit.events[-1]
    assert event == "memory.forget"
    assert payload["outcome"] == "executed"
    assert payload["count"] == 1
    assert payload["actor"] == "human"
    assert payload["approval_id"] == req.request_id
    assert "selector_hash" in payload
    # one-shot: the approval cannot be replayed
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(sel, actor=actor)


async def test_approve_unknown_request_raises() -> None:
    with pytest.raises(MemoryForgetDenied):
        await _policy().approve("nope", principal="alice")


async def test_approver_veto_rejects_request() -> None:
    async def veto(_req: ForgetRequest, _principal: str) -> bool:
        return False

    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend, approver=veto)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    got = await policy.approve(req.request_id, principal="alice")
    assert got.status == "rejected"
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(
            sel, actor=ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
        )


async def test_reject_marks_request_rejected() -> None:
    policy = _policy()
    req = policy.request(ForgetSelector(record_ids=("m1",), scope=ORG), requested_by="agent")
    got = await policy.reject(req.request_id, principal="alice")
    assert got.status == "rejected"
    assert policy.pending() == ()
    with pytest.raises(MemoryForgetDenied):
        await policy.approve(req.request_id, principal="alice")


class FakePermissions:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.grants: list[tuple[str, str]] = []
        self.checks: list[tuple[str, str]] = []
        self.identities: list[tuple[str, str | None]] = []

    async def grant(
        self,
        permission_type: Any,
        resource: str,
        ttl_seconds: int | None = 3600,
        reason: str = "",
        *,
        identity: str | None = None,
    ) -> None:
        self.grants.append((str(permission_type), resource))
        self.identities.append(("grant", identity))

    async def check(
        self, permission_type: Any, resource: str, *, identity: str | None = None
    ) -> bool:
        self.checks.append((str(permission_type), resource))
        self.identities.append(("check", identity))
        return self.allow


async def test_permission_manager_grant_is_required_for_human_execution() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    perms = FakePermissions(allow=False)
    policy = _policy(backend, permissions=perms)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    await policy.approve(req.request_id, principal="alice")
    assert perms.grants == [("database_write", f"memory_forget:{req.request_id}")]
    actor = ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(sel, actor=actor)
    assert await _ids(backend) == {"m1"}
    perms.allow = True
    assert await policy.execute(sel, actor=actor) == 1


async def test_human_execution_is_tenant_scoped() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1", scope=OTHER))
    policy = _policy(backend)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    await policy.approve(req.request_id, principal="alice")
    n = await policy.execute(
        sel, actor=ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
    )
    assert n == 0
    assert await _ids(backend, OTHER) == {"m1"}


async def test_execute_without_scope_raises_tenancy_error() -> None:
    with pytest.raises(MemoryTenancyError):
        await _policy().execute(
            ForgetSelector(record_ids=("m1",)),
            actor=ForgetActor(kind="rule", principal="retention"),
        )


# ── retention rules (MEM4-05) ────────────────────────────────────────────────


def test_retention_rules_from_settings() -> None:
    policy = _policy(
        settings=_settings(
            memory_retention_days_by_type={"episodic": 90, "semantic": 0},
            memory_superseded_retention_days=30,
        )
    )
    rules = policy.retention_rules(scope=ORG)
    assert (
        ForgetSelector(
            scope=ORG,
            types=frozenset({MemoryType.EPISODIC}),
            older_than=NOW - 90 * DAY,
            include_derived=False,
            reason="retention:episodic",
        )
        in rules
    )
    assert (
        ForgetSelector(
            scope=ORG,
            older_than=NOW - 30 * DAY,
            include_derived=False,
            reason="retention:superseded",
        )
        in rules
    )
    assert len(rules) == 2  # semantic=0 keeps forever


def test_retention_rules_zero_superseded_keeps() -> None:
    policy = _policy(
        settings=_settings(memory_retention_days_by_type={}, memory_superseded_retention_days=0)
    )
    assert policy.retention_rules(scope=ORG) == ()


async def test_rule_actor_executes_issued_retention_rule() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("old", created_at=NOW - 100 * DAY))
    await backend.put(_rec("new", created_at=NOW - 10 * DAY))
    await backend.put(_rec("sem", created_at=NOW - 200 * DAY, type_=MemoryType.SEMANTIC))
    policy = _policy(backend, settings=_settings(memory_superseded_retention_days=0))
    actor = ForgetActor(kind="rule", principal="retention")
    total = 0
    with patch.object(OTelManager, "increment_counter") as inc:
        for sel in policy.retention_rules(scope=ORG):
            total += await policy.execute(sel, actor=actor)
    assert total == 1
    assert await _ids(backend) == {"new", "sem"}
    inc.assert_any_call("memory_forget", attributes={"actor": "rule", "outcome": "executed"})


async def test_retention_keeps_consolidated_summaries_of_expired_episodes() -> None:
    # TTL expiry is not an erasure request: summaries citing expired episodes survive.
    backend = InMemoryBackend()
    await backend.put(_rec("old", created_at=NOW - 100 * DAY))
    await backend.put(
        _rec("sum", type_=MemoryType.SEMANTIC, created_at=NOW - 95 * DAY, sources=("old",))
    )
    policy = _policy(backend, settings=_settings(memory_superseded_retention_days=0))
    (sel,) = policy.retention_rules(scope=ORG)
    assert await policy.execute(sel, actor=ForgetActor(kind="rule", principal="retention")) == 1
    assert await _ids(backend) == {"sum"}


async def test_rule_selector_is_one_shot() -> None:
    backend = InMemoryBackend()
    policy = _policy(backend)
    actor = ForgetActor(kind="rule", principal="retention")
    sel = policy.retention_rules(scope=ORG)[0]
    await policy.execute(sel, actor=actor)
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(sel, actor=actor)


async def test_superseded_retention_purges_only_old_superseded() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("a", created_at=NOW - 60 * DAY, superseded_by="b"))
    await backend.put(_rec("b", created_at=NOW - 50 * DAY, type_=MemoryType.SEMANTIC))
    await backend.put(_rec("c", created_at=NOW - 60 * DAY, type_=MemoryType.SEMANTIC))
    await backend.put(
        _rec("d", created_at=NOW - 60 * DAY, superseded_by="e", type_=MemoryType.SEMANTIC)
    )
    await backend.put(_rec("e", created_at=NOW - 5 * DAY, type_=MemoryType.SEMANTIC))
    policy = _policy(backend, settings=_settings(memory_retention_days_by_type={}))
    (sel,) = policy.retention_rules(scope=ORG)
    n = await policy.execute(sel, actor=ForgetActor(kind="rule", principal="retention"))
    # "a" was superseded 50 days ago (by "b"); "d" only 5 days ago (by "e").
    assert n == 1
    assert await _ids(backend) == {"b", "c", "d", "e"}


# ── include_derived (erasure completeness) ──────────────────────────────────


class FakeGraph:
    def __init__(self) -> None:
        self.purged: list[tuple[tuple[str, ...], str]] = []

    async def purge_nodes(
        self, record_ids: tuple[str, ...], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        assert isinstance(_authorized, ForgetActor)
        self.purged.append((tuple(sorted(record_ids)), scope.org_id))
        return len(record_ids)


async def _approved_execute(policy: ForgetPolicy, sel: ForgetSelector) -> int:
    req = policy.request(sel, requested_by="agent")
    await policy.approve(req.request_id, principal="alice")
    return await policy.execute(
        sel, actor=ForgetActor(kind="human", principal="alice", approval_id=req.request_id)
    )


async def test_session_selector_purges_only_that_sessions_records() -> None:
    # Review N2: the legacy facade's clear(session_id) narrowed on thread_id, which
    # save() never sets, so the "executed" request deleted nothing.
    backend = InMemoryBackend()
    for rid, sess in (("a", "sessA"), ("b", "sessB")):
        rec = _rec(rid)
        await backend.put(replace(rec, provenance=replace(rec.provenance, session_id=sess)))
    policy = _policy(backend)
    sel = ForgetSelector(scope=ORG, session_id="sessA", reason="legacy clear")
    assert await _approved_execute(policy, sel) == 1
    assert await _ids(backend) == {"b"}


async def test_include_derived_purges_transitive_derivations_and_edges() -> None:
    backend = InMemoryBackend()
    edge_to_m1 = MemoryEdge("x", "m1", RelationKind.SUPPORTS, 0.9, NOW)
    edge_other = MemoryEdge("x", "y", RelationKind.SUPPORTS, 0.9, NOW)
    await backend.put(_rec("m1"))
    await backend.put(_rec("sum1", type_=MemoryType.SEMANTIC, sources=("m1", "m9")))
    await backend.put(_rec("sum2", type_=MemoryType.PROCEDURAL, sources=("sum1",)))
    await backend.put(_rec("x", edges=(edge_to_m1, edge_other)))
    await backend.put(_rec("y"))
    await backend.put(_rec("z", scope=OTHER, sources=("m1",)))
    graph = FakeGraph()
    purged_hook: list[tuple[str, ...]] = []

    async def playbook_hook(ids: tuple[str, ...], scope: MemoryScope) -> int:
        purged_hook.append(tuple(sorted(ids)))
        return 0

    policy = _policy(backend, graph=graph, derived_purgers=(playbook_hook,))
    n = await _approved_execute(policy, ForgetSelector(record_ids=("m1",), scope=ORG))
    assert n == 3
    assert await _ids(backend) == {"x", "y"}
    assert await _ids(backend, OTHER) == {"z"}
    x = await backend.get("x", scope=ORG)
    assert x is not None
    assert x.edges == (edge_other,)
    assert graph.purged == [(("m1", "sum1", "sum2"), "acme")]
    assert purged_hook == [("m1", "sum1", "sum2")]


async def test_include_derived_false_keeps_derivations() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    await backend.put(_rec("sum1", sources=("m1",)))
    policy = _policy(backend)
    n = await _approved_execute(
        policy, ForgetSelector(record_ids=("m1",), scope=ORG, include_derived=False)
    )
    assert n == 1
    assert await _ids(backend) == {"sum1"}


async def test_graph_without_purge_nodes_gets_edges_invalidated() -> None:
    class InvalidatingGraph:
        def __init__(self) -> None:
            self.invalidated: list[tuple[str, str]] = []

        async def neighbors(
            self, record_id: str, *, scope: MemoryScope, as_of: float | None, relations: Any = None
        ) -> list[MemoryEdge]:
            return [MemoryEdge(record_id, "n", RelationKind.SUPPORTS, 0.9, NOW)]

        async def invalidate_edge(
            self, src_id: str, dst_id: str, *, at: float, scope: MemoryScope
        ) -> None:
            self.invalidated.append((src_id, dst_id))

    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    graph = InvalidatingGraph()
    policy = _policy(backend, graph=graph)
    await _approved_execute(policy, ForgetSelector(record_ids=("m1",), scope=ORG))
    assert graph.invalidated == [("m1", "n")]


async def test_derived_hook_failure_leaves_request_retryable_and_raises() -> None:
    calls: list[tuple[str, ...]] = []
    fail = [True]

    async def flaky(ids: tuple[str, ...], scope: MemoryScope) -> int:
        calls.append(tuple(sorted(ids)))
        if fail[0]:
            raise RuntimeError("boom")
        return len(ids)

    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    audit = FakeAudit()
    policy = _policy(backend, audit=audit, derived_purgers=(flaky,))
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    await policy.approve(req.request_id, principal="dpo")
    actor = ForgetActor(kind="human", principal="dpo", approval_id=req.request_id)
    with pytest.raises(MemoryErasureIncomplete):
        await policy.execute(sel, actor=actor)
    after = policy.get_request(req.request_id)
    assert after is not None
    assert after.status == "approved"  # never "executed" while derived data survives
    assert audit.events[-1][1]["outcome"] == "incomplete"
    assert audit.events[-1][1]["derived_errors"] == 1
    # Retry with the same approval: the record is already gone, the hook still re-runs.
    fail[0] = False
    assert await policy.execute(sel, actor=actor) == 0
    assert calls == [("m1",), ("m1",)]
    done = policy.get_request(req.request_id)
    assert done is not None
    assert done.status == "executed"


async def test_graph_purge_failure_leaves_request_retryable() -> None:
    class BrokenGraph(FakeGraph):
        async def purge_nodes(
            self, record_ids: tuple[str, ...], *, scope: MemoryScope, _authorized: ForgetActor
        ) -> int:
            raise RuntimeError("graph down")

    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend, graph=BrokenGraph())
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="agent")
    await policy.approve(req.request_id, principal="dpo")
    with pytest.raises(MemoryErasureIncomplete):
        await policy.execute(
            sel, actor=ForgetActor(kind="human", principal="dpo", approval_id=req.request_id)
        )
    after = policy.get_request(req.request_id)
    assert after is not None
    assert after.status == "approved"


async def test_retention_purges_graph_nodes_of_expired_records() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("old", created_at=NOW - 100 * DAY))
    await backend.put(_rec("new", created_at=NOW))
    graph = FakeGraph()
    hook_calls: list[tuple[str, ...]] = []

    async def hook(ids: tuple[str, ...], scope: MemoryScope) -> int:
        hook_calls.append(ids)
        return 0

    policy = _policy(
        backend,
        graph=graph,
        derived_purgers=(hook,),
        settings=_settings(memory_superseded_retention_days=0),
    )
    (sel,) = policy.retention_rules(scope=ORG)
    assert await policy.execute(sel, actor=ForgetActor(kind="rule", principal="retention")) == 1
    assert graph.purged == [(("old",), "acme")]
    assert hook_calls == []  # TTL expiry is not an erasure of derived playbooks


async def test_retention_graph_failure_is_retried_by_next_run() -> None:
    class FlakyGraph(FakeGraph):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        async def purge_nodes(
            self, record_ids: tuple[str, ...], *, scope: MemoryScope, _authorized: ForgetActor
        ) -> int:
            if self.fail:
                raise RuntimeError("down")
            return await super().purge_nodes(record_ids, scope=scope, _authorized=_authorized)

    backend = InMemoryBackend()
    await backend.put(_rec("old", created_at=NOW - 100 * DAY))
    graph = FlakyGraph()
    policy = _policy(backend, graph=graph, settings=_settings(memory_superseded_retention_days=0))
    actor = ForgetActor(kind="rule", principal="retention")
    (sel,) = policy.retention_rules(scope=ORG)
    with pytest.raises(MemoryErasureIncomplete):
        await policy.execute(sel, actor=actor)
    graph.fail = False
    (sel2,) = policy.retention_rules(scope=ORG)
    assert await policy.execute(sel2, actor=actor) == 0
    assert graph.purged == [(("old",), "acme")]


async def test_include_derived_follows_superseded_chains() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("v0", content="alice lives in Rome", superseded_by="v1"))
    await backend.put(_rec("v1", content="alice lives in Paris", superseded_by="v2"))
    await backend.put(_rec("v2", content="alice lives in Berlin"))
    await backend.put(_rec("other", content="unrelated"))
    policy = _policy(backend)
    n = await _approved_execute(policy, ForgetSelector(record_ids=("v2",), scope=ORG))
    assert n == 3
    assert await _ids(backend) == {"other"}


# ── exact-scope erasure (HIGH1) ─────────────────────────────────────────────

ALICE = MemoryScope(org_id="acme", user_id="alice")
BOB = MemoryScope(org_id="acme", user_id="bob")


@pytest.mark.parametrize(
    ("selector_scope", "record_scope", "expected"),
    [
        (ORG, ORG, True),
        (ORG, ALICE, True),
        (ALICE, ALICE, True),
        (ALICE, MemoryScope("acme", user_id="alice", thread_id="t1"), True),
        (ALICE, ORG, False),  # wider shared record
        (ALICE, BOB, False),
        (ALICE, MemoryScope("globex", user_id="alice"), False),
        (MemoryScope("acme", user_id="carol", thread_id="s1"), MemoryScope("acme", "carol"), False),
        (MemoryScope("acme", thread_id="s1"), MemoryScope("acme", agent="x", thread_id="s1"), True),
    ],
)
def test_within_erasure_scope(
    selector_scope: MemoryScope, record_scope: MemoryScope, expected: bool
) -> None:
    assert within_erasure_scope(selector_scope, record_scope) is expected


async def test_user_scoped_erasure_spares_wider_shared_records() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("shared", scope=ORG))
    await backend.put(_rec("a1", scope=ALICE))
    await backend.put(_rec("a2", scope=MemoryScope("acme", user_id="alice", thread_id="t")))
    await backend.put(_rec("b1", scope=BOB))
    policy = _policy(backend)
    n = await _approved_execute(policy, ForgetSelector(scope=ALICE, reason="gdpr"))
    assert n == 2
    assert await _ids(backend) == {"shared", "b1"}


async def test_record_id_erasure_cannot_reach_wider_record() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("shared", scope=ORG))
    policy = _policy(backend)
    n = await _approved_execute(policy, ForgetSelector(record_ids=("shared",), scope=ALICE))
    assert n == 0
    assert await _ids(backend) == {"shared"}


async def test_derived_wider_records_are_reported_not_purged() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("a1", scope=ALICE))
    await backend.put(_rec("orgsum", scope=ORG, sources=("a1",)))
    audit = FakeAudit()
    policy = _policy(backend, audit=audit)
    n = await _approved_execute(policy, ForgetSelector(record_ids=("a1",), scope=ALICE))
    assert n == 1
    assert await _ids(backend) == {"orgsum"}
    assert audit.events[-1][1]["derived_out_of_scope"] == 1


# ── HITL approval (LOW8) ────────────────────────────────────────────────────


async def test_requester_cannot_approve_own_request() -> None:
    policy = _policy()
    req = policy.request(ForgetSelector(record_ids=("m1",), scope=ORG), requested_by="alice")
    with pytest.raises(MemoryForgetDenied):
        await policy.approve(req.request_id, principal="alice")
    still = policy.get_request(req.request_id)
    assert still is not None
    assert still.status == "pending"


async def test_selector_filters_by_types_and_age() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("e_old", created_at=NOW - 10 * DAY))
    await backend.put(_rec("s_old", created_at=NOW - 10 * DAY, type_=MemoryType.SEMANTIC))
    await backend.put(_rec("e_new", created_at=NOW))
    policy = _policy(backend)
    sel = ForgetSelector(
        scope=ORG,
        types=frozenset({MemoryType.EPISODIC}),
        older_than=NOW - DAY,
        include_derived=False,
    )
    assert await _approved_execute(policy, sel) == 1
    assert await _ids(backend) == {"s_old", "e_new"}


def test_forget_request_is_frozen() -> None:
    req = _policy().request(ForgetSelector(record_ids=("m1",), scope=ORG), requested_by="u")
    with pytest.raises(AttributeError):
        req.status = "approved"  # type: ignore[misc]
    assert replace(req, status="approved").status == "approved"


# ── agent tool: request only ────────────────────────────────────────────────


async def test_request_memory_forget_tool_only_creates_pending_request() -> None:
    from prismal.memory.v2.forget import build_request_memory_forget_tool

    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend)
    tool = build_request_memory_forget_tool(policy, scope=ORG, requested_by="agent:coder")
    assert tool.name == "request_memory_forget"
    out = await tool.ainvoke({"record_ids": ["m1"], "reason": "user asked"})
    assert "pending" in out
    (req,) = policy.pending()
    assert req.request_id in out
    assert req.selector.scope == ORG
    assert req.selector.record_ids == ("m1",)
    assert req.requested_by == "agent:coder"
    assert await _ids(backend) == {"m1"}


def test_request_memory_forget_tool_sync_invoke() -> None:
    from prismal.memory.v2.forget import build_request_memory_forget_tool

    policy = _policy()
    tool = build_request_memory_forget_tool(policy, scope=ORG, requested_by="agent")
    out = tool.invoke({"record_ids": ["m1"]})
    assert "pending" in out
    assert len(policy.pending()) == 1


async def test_include_derived_erases_real_sqlite_graph_nodes() -> None:
    from prismal.memory.v2.graph.sqlite import SqliteMemoryGraph

    backend = InMemoryBackend()
    graph = SqliteMemoryGraph(":memory:")
    m1 = _rec("m1")
    keep = _rec("keep")
    derived = _rec("sum1", type_=MemoryType.SEMANTIC, sources=("m1",))
    for rec in (m1, keep, derived):
        await backend.put(rec)
        await graph.upsert_node(rec)
    await graph.add_edge(MemoryEdge("sum1", "keep", RelationKind.SUPPORTS, 0.9, NOW), scope=ORG)
    audit = FakeAudit()
    policy = _policy(backend, graph=graph, audit=audit)
    assert await _approved_execute(policy, ForgetSelector(record_ids=("m1",), scope=ORG)) == 2
    assert audit.events[-1][1]["derived_errors"] == 0
    assert graph.node_count(scope=ORG) == 1
    assert graph.edge_count(scope=ORG) == 0
    graph.close()


async def test_approval_records_approver() -> None:
    policy = _policy()
    req = policy.request(ForgetSelector(record_ids=("m1",), scope=ORG), requested_by="bob")
    got = await policy.approve(req.request_id, principal="dpo")
    assert got.approved_by == "dpo"


@pytest.mark.parametrize("executor", ["mallory", "bob"])
async def test_only_the_approver_can_execute(executor: str) -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    policy = _policy(backend)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="bob")
    await policy.approve(req.request_id, principal="dpo")
    with pytest.raises(MemoryForgetDenied):
        await policy.execute(
            sel, actor=ForgetActor(kind="human", principal=executor, approval_id=req.request_id)
        )
    assert await _ids(backend) == {"m1"}
    still = policy.get_request(req.request_id)
    assert still is not None
    assert still.status == "approved"


async def test_permission_grant_and_check_are_bound_to_the_approver_identity() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    perms = FakePermissions(allow=True)
    policy = _policy(backend, permissions=perms)
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    req = policy.request(sel, requested_by="bob")
    await policy.approve(req.request_id, principal="dpo")
    await policy.execute(
        sel, actor=ForgetActor(kind="human", principal="dpo", approval_id=req.request_id)
    )
    assert perms.identities == [("grant", "dpo"), ("check", "dpo")]


class _FlakyGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    async def purge_nodes(
        self, record_ids: tuple[str, ...], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        if self.fail:
            raise RuntimeError("down")
        return await super().purge_nodes(record_ids, scope=scope, _authorized=_authorized)


async def test_erasure_retry_after_restart_still_reaches_the_graph() -> None:
    # Review N3: the pending set is in-process; after a restart a new request for the
    # same record ids found nothing in the backend and skipped the graph entirely.
    backend = InMemoryBackend()
    await backend.put(_rec("m1"))
    graph = _FlakyGraph()
    sel = ForgetSelector(record_ids=("m1",), scope=ORG)
    with pytest.raises(MemoryErasureIncomplete):
        await _approved_execute(_policy(backend, graph=graph), sel)
    assert await _ids(backend) == set()
    graph.fail = False
    restarted = _policy(backend, graph=graph)  # fresh in-memory request book
    assert await _approved_execute(restarted, sel) == 0
    assert graph.purged == [(("m1",), "acme")]


async def test_pending_ttl_ids_never_reach_derived_hooks() -> None:
    # Review N5: ids left pending by a failed TTL run must not be fed to the
    # playbook purge hooks by a later erasure (TTL expiry is not an erasure).
    backend = InMemoryBackend()
    await backend.put(_rec("old", created_at=NOW - 100 * DAY))
    await backend.put(_rec("m1"))
    graph = _FlakyGraph()
    hook_calls: list[tuple[str, ...]] = []

    async def hook(ids: tuple[str, ...], scope: MemoryScope) -> int:
        hook_calls.append(tuple(sorted(ids)))
        return 0

    policy = _policy(
        backend,
        graph=graph,
        derived_purgers=(hook,),
        settings=_settings(memory_superseded_retention_days=0),
    )
    (rule,) = policy.retention_rules(scope=ORG)
    with pytest.raises(MemoryErasureIncomplete):
        await policy.execute(rule, actor=ForgetActor(kind="rule", principal="retention"))
    graph.fail = False
    await _approved_execute(policy, ForgetSelector(record_ids=("m1",), scope=ORG))
    assert hook_calls == [("m1",)]
    assert sorted(ids for ids, _ in graph.purged) == [("m1",), ("old",)]


async def test_narrow_erasure_never_strips_graph_of_live_wider_record() -> None:
    backend = InMemoryBackend()
    await backend.put(_rec("shared"))  # org-wide, outside a user-scoped erasure
    graph = FakeGraph()
    alice = MemoryScope(org_id="acme", user_id="alice")
    sel = ForgetSelector(record_ids=("shared",), scope=alice)
    assert await _approved_execute(_policy(backend, graph=graph), sel) == 0
    assert graph.purged == []
    assert await _ids(backend) == {"shared"}
