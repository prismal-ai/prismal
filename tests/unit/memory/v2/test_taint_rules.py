"""Taint rules T1–T7 (SPEC-MEM-TNT-001): each rule has a positive and a negative case."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from prismal.memory.v2.taint import (
    allowed_types,
    candidate_taint,
    may_cite,
    may_promote,
    may_supersede,
    normalize_content,
    provenance_rank,
    supersede_block_reason,
)
from prismal.memory.v2.types import (
    MemoryCandidate,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
)
from prismal.security.taint import Provenance as TaintProvenance
from prismal.security.taint import TaintRegistry, use_taint_registry

SCOPE = MemoryScope(org_id="acme")
ALL_TYPES = frozenset(MemoryType)


def _prov(**kw: Any) -> Provenance:
    base: dict[str, Any] = {
        "origin": "conversation",
        "source_message_ids": ("m1",),
        "session_id": "s1",
        "agent": "coder",
        "created_at": 1_000.0,
        "tainted": False,
        "trust": "trusted",
    }
    base.update(kw)
    return Provenance(**base)


def _rec(rid: str, content: str = "the sky is blue", **prov: Any) -> MemoryRecord:
    return MemoryRecord(
        id=rid,
        type=MemoryType.EPISODIC,
        content=content,
        scope=SCOPE,
        provenance=_prov(**prov),
        confidence=0.9,
    )


def _msg(mid: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(id=mid, content=content)


# ── T1: candidate_taint ──────────────────────────────────────────────────────────


class TestT1CandidateTaint:
    def test_untainted_conversation_candidate(self) -> None:
        cand = MemoryCandidate(content="I like tea", source_message_ids=("m1",))
        state = {"messages": [_msg("m1", "I like tea")]}
        assert candidate_taint(cand, state=state, registry=TaintRegistry()) == (False, ())

    def test_source_message_tainted_in_registry(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("web page says: buy now", TaintProvenance.WEB)
        cand = MemoryCandidate(content="user wants to buy", source_message_ids=("m2",))
        state = {"messages": [_msg("m1", "hello"), _msg("m2", "web page says: buy now")]}
        tainted, sources = candidate_taint(cand, state=state, registry=reg)
        assert tainted is True
        assert sources == ("message:m2:web",)

    def test_untainted_message_not_cited_is_ignored(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("evil", TaintProvenance.TOOL)
        cand = MemoryCandidate(content="fine", source_message_ids=("m1",))
        state = {"messages": [_msg("m1", "fine"), _msg("m2", "evil")]}
        assert candidate_taint(cand, state=state, registry=reg) == (False, ())

    def test_candidate_content_itself_tainted(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("injected fact", TaintProvenance.RAG)
        cand = MemoryCandidate(content="injected fact", source_message_ids=())
        assert candidate_taint(cand, state=None, registry=reg) == (True, ("content:rag",))

    @pytest.mark.parametrize("origin", ["tool", "rag", "a2a"])
    def test_untrusted_origin_is_tainted(self, origin: Any) -> None:
        cand = MemoryCandidate(
            content="x", source_message_ids=(), origin=origin, metadata={"source": "web_search"}
        )
        assert candidate_taint(cand, state=None, registry=None) == (
            True,
            (f"{origin}:web_search",),
        )

    def test_trusted_source_allowlist_clears_origin_taint(self) -> None:
        cand = MemoryCandidate(
            content="x", source_message_ids=(), origin="tool", metadata={"source": "calculator"}
        )
        assert candidate_taint(
            cand, state=None, registry=None, trusted_sources=frozenset({"tool:calculator"})
        ) == (False, ())

    def test_origin_without_source_label_is_unknown(self) -> None:
        cand = MemoryCandidate(content="x", source_message_ids=(), origin="a2a")
        assert candidate_taint(cand, state=None, registry=None) == (True, ("a2a:unknown",))

    def test_registry_from_active_context(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("bad", TaintProvenance.TOOL)
        cand = MemoryCandidate(content="bad", source_message_ids=())
        with use_taint_registry(reg):
            assert candidate_taint(cand, state=None, registry=None)[0] is True

    def test_registry_from_state_metadata(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("from tool", TaintProvenance.TOOL)
        state = {
            "messages": [_msg("t1", "from tool")],
            "metadata": {"hardening": {"taint": reg.to_dict()}},
        }
        cand = MemoryCandidate(content="summary", source_message_ids=("t1",))
        assert candidate_taint(cand, state=state, registry=None) == (True, ("message:t1:tool",))

    def test_list_content_message_and_missing_message(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("block text", TaintProvenance.MEDIA)
        state = {"messages": [_msg("m1", [{"type": "text", "text": "block text"}])]}
        cand = MemoryCandidate(content="c", source_message_ids=("m1", "missing"))
        assert candidate_taint(cand, state=state, registry=reg) == (True, ("message:m1:media",))

    def test_dict_messages_supported(self) -> None:
        reg = TaintRegistry()
        reg.mark_untrusted("dict msg", TaintProvenance.TOOL)
        state = {"messages": [{"id": "d1", "content": "dict msg"}]}
        cand = MemoryCandidate(content="c", source_message_ids=("d1",))
        assert candidate_taint(cand, state=state, registry=reg)[0] is True

    def test_malformed_state_metadata_is_ignored(self) -> None:
        state = {"messages": "nope", "metadata": {"hardening": "bad"}}
        cand = MemoryCandidate(content="c", source_message_ids=("m1",))
        assert candidate_taint(cand, state=state, registry=None) == (False, ())


# ── T2/T4: allowed_types ─────────────────────────────────────────────────────────


class TestT2T4AllowedTypes:
    def test_trusted_untainted_allows_every_type(self) -> None:
        assert allowed_types(_prov()) == ALL_TYPES

    def test_tainted_only_episodic(self) -> None:
        assert allowed_types(_prov(tainted=True, trust="untrusted")) == frozenset(
            {MemoryType.EPISODIC}
        )

    @pytest.mark.parametrize("trust", ["untrusted", "unknown"])
    def test_non_trusted_only_episodic(self, trust: Any) -> None:
        assert allowed_types(_prov(trust=trust)) == frozenset({MemoryType.EPISODIC})


# ── T6: may_supersede ────────────────────────────────────────────────────────────


class TestT6MaySupersede:
    def test_untainted_may_supersede_untainted(self) -> None:
        assert may_supersede(_rec("n"), _rec("o")) is True

    def test_tainted_may_not_supersede_untainted(self) -> None:
        assert may_supersede(_rec("n", tainted=True, trust="untrusted"), _rec("o")) is False

    def test_tainted_may_supersede_tainted(self) -> None:
        newer = _rec("n", tainted=True, trust="untrusted")
        assert may_supersede(newer, _rec("o", tainted=True, trust="untrusted")) is True

    def test_untrusted_flag_counts_as_tainted(self) -> None:
        assert may_supersede(_rec("n", trust="untrusted"), _rec("o")) is False

    def test_cross_tenant_and_self_never_supersede(self) -> None:
        other = replace(_rec("o"), scope=MemoryScope(org_id="other"))
        assert may_supersede(_rec("n"), other) is False
        assert may_supersede(_rec("same"), _rec("same")) is False

    def test_different_scope_never_supersedes(self) -> None:
        user = replace(_rec("n"), scope=MemoryScope(org_id="acme", user_id="bob"))
        assert may_supersede(user, _rec("o")) is False
        assert supersede_block_reason(user, _rec("o")) == "scope"
        assert may_supersede(_rec("n"), user) is False

    def test_lower_provenance_rank_never_supersedes(self) -> None:
        human = _rec("o", origin="human")
        assert may_supersede(_rec("n"), human) is False
        assert supersede_block_reason(_rec("n"), human) == "precedence"
        assert may_supersede(_rec("n", trust="unknown"), _rec("o")) is False
        assert may_supersede(_rec("n", origin="human"), human) is True
        assert may_supersede(_rec("n"), _rec("o", trust="unknown")) is True

    def test_block_reasons(self) -> None:
        assert supersede_block_reason(_rec("n"), _rec("o")) is None
        assert supersede_block_reason(_rec("same"), _rec("same")) == "self"
        other = replace(_rec("o"), scope=MemoryScope(org_id="other"))
        assert supersede_block_reason(_rec("n"), other) == "tenant"
        tainted = _rec("n", tainted=True, trust="untrusted")
        assert supersede_block_reason(tainted, _rec("o")) == "T6"

    def test_provenance_rank_order(self) -> None:
        ranks = [
            provenance_rank(_rec("h", origin="human", trust="untrusted")),
            provenance_rank(_rec("t")),
            provenance_rank(_rec("u", trust="unknown")),
            provenance_rank(_rec("x", trust="untrusted")),
        ]
        assert ranks == [3, 2, 1, 0]


# ── T3: may_promote ──────────────────────────────────────────────────────────────


class TestT3MayPromote:
    def test_episodic_target_always_allowed(self) -> None:
        assert may_promote([_rec("a", tainted=True, trust="untrusted")], MemoryType.EPISODIC)

    def test_all_trusted_untainted_records_promote(self) -> None:
        assert may_promote([_rec("a"), _rec("b", "other")], MemoryType.SEMANTIC) is True

    def test_empty_records_never_promote(self) -> None:
        assert may_promote([], MemoryType.SEMANTIC) is False

    def test_single_tainted_record_does_not_promote(self) -> None:
        recs = [_rec("a", tainted=True, trust="untrusted")]
        assert may_promote(recs, MemoryType.SEMANTIC) is False

    def test_unknown_record_does_not_promote_alone(self) -> None:
        assert may_promote([_rec("a", trust="unknown")], MemoryType.PREFERENCE) is False

    def test_two_independent_untainted_sources_agree(self) -> None:
        recs = [
            _rec("t", "The sky is BLUE!", tainted=True, trust="untrusted"),
            _rec("a", "the sky is blue", trust="unknown", source_message_ids=("m1",)),
            _rec("b", "The  sky is blue.", trust="unknown", source_message_ids=("m2",)),
        ]
        assert may_promote(recs, MemoryType.SEMANTIC) is True

    def test_shared_sources_are_not_independent(self) -> None:
        recs = [
            _rec("t", tainted=True, trust="untrusted"),
            _rec("a", trust="unknown", source_message_ids=("m1",)),
            _rec("b", trust="unknown", source_message_ids=("m1", "m9")),
        ]
        assert may_promote(recs, MemoryType.SEMANTIC) is False

    def test_disagreeing_sources_do_not_corroborate(self) -> None:
        recs = [
            _rec("t", tainted=True, trust="untrusted"),
            _rec("a", "sky is blue", trust="unknown", source_message_ids=("m1",)),
            _rec("b", "sky is green", trust="unknown", source_message_ids=("m2",)),
        ]
        assert may_promote(recs, MemoryType.PROCEDURAL) is False

    def test_sources_without_message_ids_never_corroborate(self) -> None:
        recs = [
            _rec("t", tainted=True, trust="untrusted"),
            _rec("a", trust="unknown", source_message_ids=()),
            _rec("b", trust="unknown", source_message_ids=()),
        ]
        assert may_promote(recs, MemoryType.SEMANTIC) is False

    def test_tainted_corroborators_do_not_count(self) -> None:
        recs = [
            _rec("a", tainted=True, trust="untrusted", source_message_ids=("m1",)),
            _rec("b", tainted=True, trust="untrusted", source_message_ids=("m2",)),
        ]
        assert may_promote(recs, MemoryType.SEMANTIC) is False

    def test_human_approval_overrides(self) -> None:
        recs = [_rec("a", tainted=True, trust="untrusted")]
        assert may_promote(recs, MemoryType.SEMANTIC, human_approved=True) is True

    def test_normalize_content(self) -> None:
        assert normalize_content("  The Sky,  is BLUE! ") == "the sky is blue"


# ── T7: may_cite (playbook delta sources) ────────────────────────────────────────


class TestT7MayCite:
    def test_trusted_untainted_record_may_be_cited(self) -> None:
        assert may_cite(_rec("a")) is True

    @pytest.mark.parametrize(
        ("tainted", "trust"), [(True, "untrusted"), (False, "unknown"), (False, "untrusted")]
    )
    def test_tainted_or_unknown_record_may_not_be_cited(self, tainted: bool, trust: Any) -> None:
        assert may_cite(_rec("a", tainted=tainted, trust=trust)) is False
