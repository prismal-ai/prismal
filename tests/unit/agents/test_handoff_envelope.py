"""Tests for multi-agent handoff envelopes (Phase TKE — SPEC-TKE-HND-001)."""

from __future__ import annotations

from typing import Any

import pytest

from prismal.agents.artifacts import ArtifactRef, InMemoryArtifactStore
from prismal.agents.handoff import (
    HandoffEnvelope,
    build_envelope,
    envelope_enabled,
    envelope_to_message_text,
    maybe_envelope,
)
from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.utils.token_estimate import estimate_tokens


class RecordingStore:
    def __init__(self) -> None:
        self.inner = InMemoryArtifactStore(settings=Settings())
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def put(self, content: str, **kw: Any) -> ArtifactRef:
        self.puts.append((content, kw))
        return self.inner.put(content, **kw)

    def get(self, *a: Any, **kw: Any) -> Any:
        return self.inner.get(*a, **kw)

    def purge_expired(self, now: float | None = None) -> int:
        return self.inner.purge_expired(now)


class BrokenStore:
    def put(self, content: str, **kw: Any) -> ArtifactRef:
        raise RuntimeError("store down")

    def get(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise RuntimeError

    def purge_expired(self, now: float | None = None) -> int:  # pragma: no cover
        return 0


def _long_text(n: int = 200) -> str:
    return " ".join(f"Sentence number {i} explains a finding." for i in range(n))


def _build(text: str, store: Any, max_tokens: int = 50, **kw: Any) -> HandoffEnvelope:
    return build_envelope(
        text, store=store, run_id="r1", tenant="t1", max_summary_tokens=max_tokens, **kw
    )


class TestBuildEnvelope:
    def test_short_text_no_artifact(self) -> None:
        store = RecordingStore()
        env = _build("Short answer.", store)
        assert env.summary == "Short answer."
        assert env.artifacts == ()
        assert env.status == "ok"
        assert store.puts == []

    def test_long_text_stored_and_summarized(self) -> None:
        store = RecordingStore()
        text = _long_text()
        env = _build(text, store)
        assert len(store.puts) == 1
        content, kw = store.puts[0]
        assert content == text
        assert kw["kind"] == "handoff"
        assert len(env.artifacts) == 1
        ref = env.artifacts[0]
        assert estimate_tokens(env.summary) <= 50
        assert env.summary.endswith(f"… [full text: artifact {ref.id}]")
        assert store.get(ref.id, run_id="r1", tenant="t1").content == text

    def test_sentence_boundary_cut(self) -> None:
        env = _build(_long_text(), RecordingStore())
        body = env.summary.rsplit(" … [full text:", 1)[0]
        assert body.endswith(".")
        assert _long_text().startswith(body)

    def test_paragraph_boundary_cut(self) -> None:
        text = "First paragraph without stop\n\n" + "x" * 2000
        env = _build(text, RecordingStore(), max_tokens=30)
        body = env.summary.rsplit(" … [full text:", 1)[0]
        assert body == "First paragraph without stop"

    def test_deterministic(self) -> None:
        a = _build(_long_text(), RecordingStore()).summary
        b = _build(_long_text(), RecordingStore()).summary
        assert a.rsplit("artifact", 1)[0] == b.rsplit("artifact", 1)[0]

    def test_store_failure_truncated_note(self) -> None:
        env = _build(_long_text(), BrokenStore())
        assert env.artifacts == ()
        assert env.summary.endswith("… [truncated]")
        assert estimate_tokens(env.summary) <= 50

    def test_no_store_truncates(self) -> None:
        env = _build(_long_text(), None)
        assert env.artifacts == ()
        assert env.summary.endswith("… [truncated]")

    def test_tiny_budget_does_not_raise(self) -> None:
        env = _build(_long_text(), RecordingStore(), max_tokens=0)
        assert len(env.artifacts) == 1

    def test_usage_and_status_carried(self) -> None:
        u = Usage(prompt_tokens=10, completion_tokens=5, calls=1)
        env = _build("ok", RecordingStore(), usage=u, status="partial")
        assert env.usage == u
        assert env.status == "partial"

    def test_invalid_status_raises_before_io(self) -> None:
        store = RecordingStore()
        with pytest.raises(ValueError, match="status"):
            _build(_long_text(), store, status="weird")
        assert store.puts == []

    def test_counter_emitted_and_failure_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.monitoring import otel

        seen: list[tuple[str, Any]] = []

        def fake(self: Any, metric: str, value: int = 1, attributes: Any = None) -> None:
            seen.append((metric, attributes))

        monkeypatch.setattr(otel.OTelManager, "increment_counter", fake)
        _build("x", RecordingStore(), pattern="moa")
        assert ("handoff_envelopes", {"pattern": "moa"}) in seen

        def boom(self: Any, *a: Any, **kw: Any) -> None:
            raise RuntimeError("otel down")

        monkeypatch.setattr(otel.OTelManager, "increment_counter", boom)
        assert _build("x", RecordingStore(), pattern="moa").summary == "x"


class TestMessageText:
    def test_summary_only(self) -> None:
        assert envelope_to_message_text(HandoffEnvelope(summary="hi")) == "hi"

    def test_with_artifacts_and_status(self) -> None:
        store = InMemoryArtifactStore(settings=Settings())
        r1 = store.put("a", run_id="r", tenant="t", kind="handoff")
        r2 = store.put("b", run_id="r", tenant="t", kind="handoff")
        env = HandoffEnvelope(summary="sum", artifacts=(r1, r2), status="failed")
        assert envelope_to_message_text(env) == (
            f"sum\nArtifacts: {r1.id}, {r2.id}\nStatus: failed"
        )


class TestSerialization:
    def test_round_trip(self) -> None:
        store = InMemoryArtifactStore(settings=Settings())
        ref = store.put("abc", run_id="r", tenant="t", kind="handoff", tool="x")
        env = HandoffEnvelope(
            summary="s",
            artifacts=(ref,),
            status="partial",
            usage=Usage(prompt_tokens=3, cost_usd=0.5, cost_source="litellm"),
        )
        d = env.to_dict()
        assert isinstance(d["artifacts"], list)
        assert HandoffEnvelope.from_dict(d) == env

    def test_round_trip_is_json_safe(self) -> None:
        import json

        env = HandoffEnvelope(summary="s", usage=Usage(calls=1))
        assert HandoffEnvelope.from_dict(json.loads(json.dumps(env.to_dict()))) == env

    def test_legacy_dict_missing_optionals(self) -> None:
        env = HandoffEnvelope.from_dict({"summary": "only"})
        assert env == HandoffEnvelope(summary="only")

    def test_legacy_partial_nested(self) -> None:
        env = HandoffEnvelope.from_dict(
            {
                "summary": "s",
                "artifacts": [{"id": "a1", "run_id": "r", "tenant": "t"}],
                "usage": {"prompt_tokens": 2},
            }
        )
        assert env.artifacts[0].id == "a1"
        assert env.artifacts[0].kind == "handoff"
        assert env.usage == Usage(prompt_tokens=2)


class TestFlags:
    def test_disabled_by_default(self) -> None:
        assert envelope_enabled(Settings()) is False

    def test_needs_master(self) -> None:
        assert envelope_enabled(Settings(handoff_envelope_enabled=True)) is False
        s = Settings(token_efficiency_enabled=True, handoff_envelope_enabled=True)
        assert envelope_enabled(s) is True

    def test_maybe_envelope_disabled_none(self) -> None:
        store = RecordingStore()
        out = maybe_envelope(
            _long_text(), settings=Settings(), store=store, run_id="r", tenant="t", pattern="p"
        )
        assert out is None
        assert store.puts == []

    def test_maybe_envelope_enabled_uses_settings_cap(self) -> None:
        s = Settings(
            token_efficiency_enabled=True,
            handoff_envelope_enabled=True,
            handoff_summary_max_tokens=40,
        )
        env = maybe_envelope(
            _long_text(), settings=s, store=RecordingStore(), run_id="r", tenant="t", pattern="p"
        )
        assert env is not None
        assert estimate_tokens(env.summary) <= 40
        assert len(env.artifacts) == 1


class TestRunArtifactStore:
    """Per-run shared artifact store registry (TKE7-02/03)."""

    def test_same_run_id_returns_same_store(self) -> None:
        from prismal.agents.handoff import clear_run_artifact_store, get_run_artifact_store

        s = Settings()
        try:
            a = get_run_artifact_store("run-x", s)
            b = get_run_artifact_store("run-x", s)
            assert a is b
        finally:
            clear_run_artifact_store("run-x")

    def test_distinct_run_ids_get_distinct_stores(self) -> None:
        from prismal.agents.handoff import clear_run_artifact_store, get_run_artifact_store

        s = Settings()
        try:
            assert get_run_artifact_store("run-a", s) is not get_run_artifact_store("run-b", s)
        finally:
            clear_run_artifact_store("run-a")
            clear_run_artifact_store("run-b")

    def test_clear_releases_store_and_is_idempotent(self) -> None:
        from prismal.agents.handoff import clear_run_artifact_store, get_run_artifact_store

        s = Settings()
        first = get_run_artifact_store("run-c", s)
        clear_run_artifact_store("run-c")
        clear_run_artifact_store("run-c")  # idempotent
        second = get_run_artifact_store("run-c", s)
        clear_run_artifact_store("run-c")
        assert first is not second

    def test_store_reaches_full_text_via_envelope_ref(self) -> None:
        from prismal.agents.handoff import clear_run_artifact_store, get_run_artifact_store

        s = Settings(
            token_efficiency_enabled=True,
            handoff_envelope_enabled=True,
            handoff_summary_max_tokens=40,
        )
        text = _long_text()
        try:
            store = get_run_artifact_store("run-d", s)
            env = maybe_envelope(
                text, settings=s, store=store, run_id="run-d", tenant="", pattern="t"
            )
            assert env is not None and env.artifacts
            got = get_run_artifact_store("run-d", s).get(
                env.artifacts[0].id, run_id="run-d", tenant=""
            )
            assert got.content == text
        finally:
            clear_run_artifact_store("run-d")


class TestMaybeRunEnvelope:
    def test_disabled_returns_none_and_builds_no_store(self) -> None:
        from prismal.agents import handoff
        from prismal.agents.handoff import maybe_run_envelope

        env = maybe_run_envelope(
            _long_text(), settings=Settings(), run_id="run-off", tenant="t", pattern="p"
        )
        assert env is None
        assert "run-off" not in handoff._RUN_STORES

    def test_enabled_stores_full_text_in_run_store(self) -> None:
        from prismal.agents.handoff import (
            clear_run_artifact_store,
            get_run_artifact_store,
            maybe_run_envelope,
        )

        s = Settings(
            token_efficiency_enabled=True,
            handoff_envelope_enabled=True,
            handoff_summary_max_tokens=40,
        )
        text = _long_text()
        try:
            env = maybe_run_envelope(text, settings=s, run_id="run-on", tenant="t", pattern="p")
            assert env is not None
            ref = env.artifacts[0]
            assert (
                get_run_artifact_store("run-on", tenant="t")
                .get(ref.id, run_id="run-on", tenant="t")
                .content
                == text
            )
        finally:
            clear_run_artifact_store("run-on", tenant="t")


def test_envelope_enabled_requires_real_true_flags() -> None:
    """Non-bool truthy flags (e.g. a MagicMock settings double) do not enable envelopes."""
    from unittest.mock import MagicMock

    assert envelope_enabled(MagicMock()) is False


# ── security review 2026-10-05: scope + retention ────────────────────────────


def test_handoff_scope_without_session_is_anonymous() -> None:
    from prismal.agents.handoff import handoff_scope

    run_a, tenant = handoff_scope({"metadata": {}})
    run_b, _ = handoff_scope("not-a-dict")
    assert tenant == "default"
    assert run_a.startswith("anon-") and run_b.startswith("anon-") and run_a != run_b


def test_run_store_is_keyed_by_tenant() -> None:
    from prismal.agents import handoff

    handoff._RUN_STORES.clear()
    a = handoff.get_run_artifact_store("r1", tenant="t1")
    b = handoff.get_run_artifact_store("r1", tenant="t2")
    assert a is not b
    assert handoff.get_run_artifact_store("r1", tenant="t1") is a
    handoff.clear_run_artifact_store("r1", tenant="t1")
    assert ("t1", "r1") not in handoff._RUN_STORES
    handoff._RUN_STORES.clear()


def test_run_store_registry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.agents import handoff

    handoff._RUN_STORES.clear()
    monkeypatch.setattr(handoff, "_MAX_RUN_STORES", 3)
    first = handoff.get_run_artifact_store("r0")
    for i in range(1, 5):
        handoff.get_run_artifact_store(f"r{i}")
    assert len(handoff._RUN_STORES) == 3
    assert handoff.get_run_artifact_store("r0") is not first  # oldest evicted
    handoff._RUN_STORES.clear()
