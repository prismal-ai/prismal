"""``ollama`` decision backend (DEC Ollama delta — SPEC-DEC-OLL-001…007, 011; DEC2-07/08/09/11)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from prismal.budget.meter import CostMeter
from prismal.decisions.adapters import ollama as ollama_mod
from prismal.decisions.adapters.ollama import (
    OllamaDecisionAdapter,
    OllamaTierGroup,
    batching_allowed,
    clear_preflight_cache,
    plan_chunks,
)
from prismal.decisions.catalog import HITL_QUESTION_IDS
from prismal.decisions.types import DecisionKind, GatePolicy, QuestionSpec
from tests.unit.decisions.adapters._ollama_fake import (
    ACCURATE,
    FAST,
    FakeModel,
    FakeOllama,
)
from tests.unit.decisions.contract import CHOICE, NOUL, SCORE, STATE, wide_choice

pytestmark = pytest.mark.unit

BASE = "http://localhost:11434"
POLICY = GatePolicy(timeout_ms=2_000)
HITL = {
    qid: QuestionSpec(id=qid, kind=DecisionKind.NOUL, instructions=f"Concrete fact {qid}?")
    for qid in HITL_QUESTION_IDS
}
PER_TURN = {CHOICE.id: CHOICE, NOUL.id: NOUL}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    clear_preflight_cache()
    yield
    clear_preflight_cache()


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any] | None]]:
    seen: list[tuple[str, dict[str, Any] | None]] = []
    monkeypatch.setattr(ollama_mod, "inc", lambda m, a=None: seen.append((m, a)))
    return seen


def _fast(server: FakeOllama, **kw: Any) -> OllamaDecisionAdapter:
    return OllamaDecisionAdapter(
        base_url=BASE, model_name=FAST, tier="fast", transport=server.transport(), **kw
    )


def _accurate(server: FakeOllama, **kw: Any) -> OllamaDecisionAdapter:
    return OllamaDecisionAdapter(
        base_url=BASE, model_name=ACCURATE, tier="accurate", transport=server.transport(), **kw
    )


def _pair(server: FakeOllama, **kw: Any) -> tuple[OllamaDecisionAdapter, OllamaDecisionAdapter]:
    fast, accurate = _fast(server, **kw), _accurate(server, **kw)
    OllamaTierGroup.link(fast=fast, accurate=accurate)
    return fast, accurate


def _big_state(tokens: int) -> dict[str, str]:
    return {"user": "x" * (tokens * 4)}


# ── identity, wire, preflight (OLL-001/002/003) ───────────────────────────────


async def test_names_and_bare_model_before_preflight() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server)
    assert (fast.name, accurate.name) == ("ollama:fast", "ollama:accurate")
    assert fast.model == FAST
    assert server.requests == []  # preflight is lazy


async def test_preflight_ok_digest_in_model_and_wire() -> None:
    server = FakeOllama()
    fast = _fast(server)
    out = await fast.decide(STATE, {CHOICE.id: CHOICE}, policy=POLICY)
    d = out[CHOICE.id]
    assert not d.abstained
    assert d.value == "coder"
    assert d.model == "tev1:4b@9b5bb969e46c"
    assert fast.model == "tev1:4b@9b5bb969e46c"
    (req,) = server.calls("/v1/systemone")
    assert str(req.url) == f"{BASE}/v1/systemone"
    assert req.headers["authorization"] == "Bearer ollama"
    body = json.loads(req.content)
    assert body["model"] == FAST  # the wire uses the bare name
    assert body["keep_alive"] == "30m"


async def test_preflight_refuses_without_decision_capability(refused: list[Any]) -> None:
    server = FakeOllama(models={FAST: FakeModel(capabilities=["completion"])})
    out = await _fast(server).decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    assert all(d.abstained for d in out.values())
    assert server.calls("/v1/systemone") == []
    assert ("decision_ollama_preflight_refused", {"tier": "fast", "reason": "capability"}) in (
        refused
    )


@pytest.mark.parametrize("version", ["0.34.9", "0.9.0", "garbage"])
async def test_preflight_refuses_old_or_unparsable_version(
    version: str, refused: list[Any]
) -> None:
    server = FakeOllama(version=version)
    out = await _fast(server).decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    assert server.calls("/api/show") == []
    assert ("decision_ollama_preflight_refused", {"tier": "fast", "reason": "version"}) in refused


async def test_preflight_accepts_newer_version_numerically() -> None:
    server = FakeOllama(version="0.100.0")
    out = await _fast(server, min_version="0.35.0").decide(STATE, PER_TURN, policy=POLICY)
    assert not any(d.abstained for d in out.values())


async def test_preflight_refuses_missing_tag(refused: list[Any]) -> None:
    server = FakeOllama(models={FAST: FakeModel(tagged=False)})
    out = await _fast(server).decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    assert server.calls("/v1/systemone") == []
    assert ("decision_ollama_preflight_refused", {"tier": "fast", "reason": "missing"}) in refused


async def test_preflight_refuses_model_not_pulled(refused: list[Any]) -> None:
    server = FakeOllama(models={})
    out = await _fast(server).decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    assert ("decision_ollama_preflight_refused", {"tier": "fast", "reason": "missing"}) in refused


async def test_preflight_cached_across_calls_and_instances() -> None:
    server = FakeOllama()
    fast = _fast(server)
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    other = _fast(server)
    out = await other.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@9b5bb969e46c"
    assert len(server.calls("/api/version")) == 1
    assert len(server.calls("/api/show")) == 1
    assert len(server.calls("/api/tags")) == 1
    assert len(server.calls("/v1/systemone")) == 3


async def test_refusal_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(ollama_mod, "_monotonic", lambda: now[0])
    server = FakeOllama(version="0.1.0")
    fast = _fast(server)
    await fast.decide(STATE, PER_TURN, policy=POLICY)
    now[0] += 3_600
    await fast.decide(STATE, PER_TURN, policy=POLICY)
    assert len(server.calls("/api/version")) == 1


async def test_transport_error_retries_after_30s(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(ollama_mod, "_monotonic", lambda: now[0])
    server = FakeOllama(raise_on={"/api/version"})
    fast = _fast(server)
    out = await fast.decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    now[0] += 10
    await fast.decide(STATE, PER_TURN, policy=POLICY)
    assert len(server.calls("/api/version")) == 1  # inside the retry window
    server.raise_on.clear()
    now[0] += 25
    out = await fast.decide(STATE, PER_TURN, policy=POLICY)
    assert len(server.calls("/api/version")) == 2
    assert not any(d.abstained for d in out.values())


# ── limits (OLL-004) and errors (OLL-005) ─────────────────────────────────────


async def test_oversize_prompt_never_sent() -> None:
    server = FakeOllama()
    fast = _fast(server)
    out = await fast.decide(_big_state(2_000), {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].abstained
    assert out[NOUL.id].reason == "unsupported"
    assert server.calls("/v1/systemone") == []


async def test_num_ctx_defaults_to_2048_when_absent() -> None:
    server = FakeOllama(models={FAST: FakeModel(num_ctx=None)})
    fast = _fast(server)
    # Window = 2048 ⇒ limit int(2048 × 0.9) = 1843 estimated tokens.
    ok = await fast.decide(_big_state(1_700), {NOUL.id: NOUL}, policy=POLICY)
    over = await fast.decide(_big_state(1_850), {NOUL.id: NOUL}, policy=POLICY)
    assert not ok[NOUL.id].abstained
    assert over[NOUL.id].reason == "unsupported"


async def test_body_over_64_kib_never_sent() -> None:
    server = FakeOllama(models={FAST: FakeModel(num_ctx=1_000_000)})
    out = await _fast(server).decide({"user": "y" * 70_000}, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].reason == "unsupported"
    assert server.calls("/v1/systemone") == []


def test_choice_options_limit_2_to_26() -> None:
    fast = _fast(FakeOllama())
    assert fast.supports(wide_choice(26), language="es") is True
    assert fast.supports(wide_choice(27), language="es") is False
    assert fast.supports(CHOICE, language="es") is True


async def test_too_many_options_abstains_without_request() -> None:
    server = FakeOllama()
    wide = wide_choice(27)
    out = await _fast(server).decide(STATE, {wide.id: wide}, policy=POLICY)
    assert out[wide.id].reason == "unsupported"
    assert server.calls("/v1/systemone") == []


async def test_http_400_abstains_without_retry() -> None:
    server = FakeOllama(systemone_status=400)
    out = await _fast(server).decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].abstained
    assert out[NOUL.id].reason == "backend_error"
    assert len(server.calls("/v1/systemone")) == 1


async def test_timeout_abstains() -> None:
    server = FakeOllama(systemone_delay_s=1.0)
    fast = _fast(server)
    await fast.decide(STATE, {}, policy=POLICY)  # no-op
    out = await fast.decide(STATE, PER_TURN, policy=GatePolicy(timeout_ms=100))
    assert {d.reason for d in out.values()} == {"timeout"}


async def test_model_mismatch_abstains() -> None:
    server = FakeOllama(served_model="other:1b")
    out = await _fast(server).decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].reason == "model_mismatch"


# ── keep_alive + batching (OLL-006, OLL-011) ──────────────────────────────────


async def test_keep_alive_in_every_body() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server, keep_alive="45m", max_batch=2)
    await fast.decide(STATE, PER_TURN, policy=POLICY)
    await accurate.decide(STATE, HITL, policy=POLICY)
    bodies = server.systemone_bodies()
    assert len(bodies) == 2 + 3
    assert {b["keep_alive"] for b in bodies} == {"45m"}


async def test_fast_sends_one_question_per_request() -> None:
    server = FakeOllama()
    fast = _fast(server, max_batch=4)
    questions = {CHOICE.id: CHOICE, NOUL.id: NOUL, SCORE.id: SCORE}
    out = await fast.decide(STATE, questions, policy=POLICY)
    assert not any(d.abstained for d in out.values())
    assert [len(b["questions"]) for b in server.systemone_bodies()] == [1, 1, 1]


async def test_accurate_batches_at_most_max_batch() -> None:
    server = FakeOllama()
    accurate = _accurate(server, max_batch=2)
    out = await accurate.decide(STATE, HITL, policy=POLICY)
    assert not any(d.abstained for d in out.values())
    assert all(d.model == "nimble:9b-q4_K_M@572f1f4c801d" for d in out.values())
    sizes = [len(b["questions"]) for b in server.systemone_bodies()]
    assert sizes == [2, 2, 1]
    sent = [qid for b in server.systemone_bodies() for qid in b["questions"]]
    assert sorted(sent) == sorted(HITL)


async def test_accurate_batching_disabled_by_record(tmp_path: Path) -> None:
    (tmp_path / "batch@nimble:9b-q4_K_M@572f1f4c801d.json").write_text(
        json.dumps({"single_accuracy": 0.95, "batch_accuracy": 0.90})
    )
    server = FakeOllama()
    accurate = _accurate(server, max_batch=4, calibration_dir=str(tmp_path))
    await accurate.decide(STATE, HITL, policy=POLICY)
    assert [len(b["questions"]) for b in server.systemone_bodies()] == [1] * 5


def test_plan_chunks_splits_by_size_and_fit() -> None:
    sizes = {f"q{i}": 10 for i in range(7)}
    assert plan_chunks(list(sizes), max_batch=3, fits=lambda ids: True) == [
        ["q0", "q1", "q2"],
        ["q3", "q4", "q5"],
        ["q6"],
    ]
    # A chunk that would not fit is closed early (never truncated).
    assert plan_chunks(["a", "b", "c"], max_batch=3, fits=lambda ids: len(ids) <= 1) == [
        ["a"],
        ["b"],
        ["c"],
    ]
    # Never more than 64 questions per request.
    chunks = plan_chunks([f"q{i}" for i in range(70)], max_batch=100, fits=lambda ids: True)
    assert [len(c) for c in chunks] == [64, 6]


@pytest.mark.parametrize(
    ("content", "allowed"),
    [
        (None, True),
        ("not json", True),
        ({"single_accuracy": 0.95, "batch_accuracy": 0.94}, True),
        ({"single_accuracy": 0.95, "batch_accuracy": 0.93}, True),
        ({"single_accuracy": 0.95, "batch_accuracy": 0.929}, False),
        ({"single_accuracy": "x"}, True),
    ],
)
def test_batching_allowed(tmp_path: Path, content: Any, allowed: bool) -> None:
    path = tmp_path / "batch@m:1@abcdefabcdef.json"
    if content is not None:
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    assert batching_allowed(tmp_path, "m:1", "abcdefabcdef") is allowed


def test_batching_allowed_without_directory() -> None:
    assert batching_allowed(None, "m:1", "abcdefabcdef") is True


# ── tier routing (OLL-007) ────────────────────────────────────────────────────


async def test_fast_defers_low_frequency_to_healthy_accurate() -> None:
    server = FakeOllama()
    fast, _ = _pair(server)
    out = await fast.decide(STATE, {SCORE.id: SCORE, NOUL.id: NOUL}, policy=POLICY)
    assert out[SCORE.id].reason == "unsupported"
    assert not out[NOUL.id].abstained
    assert [list(b["questions"]) for b in server.systemone_bodies()] == [[NOUL.id]]


async def test_fast_answers_low_frequency_without_accurate_sibling() -> None:
    server = FakeOllama()
    out = await _fast(server).decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    assert not out[SCORE.id].abstained


async def test_fast_answers_low_frequency_when_accurate_refused() -> None:
    server = FakeOllama(
        models={FAST: FakeModel(), ACCURATE: FakeModel(capabilities=[], digest="c" * 64)}
    )
    fast, accurate = _pair(server)
    await accurate.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)  # refused preflight
    out = await fast.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    assert not out[SCORE.id].abstained


async def test_unknown_question_id_counts_as_per_turn() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server)
    adhoc = QuestionSpec(id="adhoc.q", kind=DecisionKind.NOUL, instructions="Is it?")
    assert not (await fast.decide(STATE, {adhoc.id: adhoc}, policy=POLICY))[adhoc.id].abstained
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    out = await accurate.decide(STATE, {adhoc.id: adhoc}, policy=POLICY)
    assert out[adhoc.id].reason == "unsupported"


async def test_accurate_skips_per_turn_when_fast_fits() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server)
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    before = len(server.calls("/v1/systemone"))
    out = await accurate.decide(STATE, PER_TURN, policy=POLICY)
    assert {d.reason for d in out.values()} == {"unsupported"}
    assert len(server.calls("/v1/systemone")) == before


async def test_accurate_takes_oversize_per_turn_prompt() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server)
    big = _big_state(2_500)  # > 0.9 × 2050 (Tev1), < 0.9 × 8194 (Nimble)
    first = await fast.decide(big, {NOUL.id: NOUL}, policy=POLICY)
    assert first[NOUL.id].reason == "unsupported"
    out = await accurate.decide(big, {NOUL.id: NOUL}, policy=POLICY)
    assert not out[NOUL.id].abstained
    assert out[NOUL.id].backend == "ollama:accurate"


async def test_accurate_takes_per_turn_when_fast_refused() -> None:
    server = FakeOllama(
        models={FAST: FakeModel(capabilities=[]), ACCURATE: FakeModel(digest="d" * 64)}
    )
    fast, accurate = _pair(server)
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    out = await accurate.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert not out[NOUL.id].abstained


async def test_accurate_takes_per_turn_without_fast_sibling() -> None:
    server = FakeOllama()
    out = await _accurate(server).decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert not out[NOUL.id].abstained


async def test_accurate_p95_gate_blocks_per_turn_only() -> None:
    server = FakeOllama()
    fast, accurate = _pair(server, accurate_max_p95_ms=1_000)
    big = _big_state(2_500)
    await fast.decide(big, {NOUL.id: NOUL}, policy=POLICY)
    accurate.latency_samples.extend([2_000.0] * 50)
    out = await accurate.decide(big, {NOUL.id: NOUL, SCORE.id: SCORE}, policy=POLICY)
    assert out[NOUL.id].reason == "unsupported"
    assert not out[SCORE.id].abstained


def test_p95_window_is_last_50_samples() -> None:
    accurate = _accurate(FakeOllama())
    accurate.latency_samples.extend([5_000.0] * 50)
    accurate.latency_samples.extend([100.0] * 50)
    assert accurate.p95_ms() == 100.0
    assert len(accurate.latency_samples) == 50


async def test_successful_requests_record_latency() -> None:
    server = FakeOllama()
    accurate = _accurate(server)
    assert accurate.p95_ms() is None
    await accurate.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    assert len(accurate.latency_samples) == 1


# ── warm-up, metering, batch states, close ────────────────────────────────────


async def test_warmup_preflights_and_sends_one_tiny_noul() -> None:
    server = FakeOllama()
    accurate = _accurate(server, keep_alive="1h")
    assert await accurate.warmup() is True
    (body,) = server.systemone_bodies()
    assert body["keep_alive"] == "1h"
    assert len(body["questions"]) == 1
    assert next(iter(body["questions"].values()))["type"] == "noul"
    assert len(accurate.latency_samples) == 0  # cold load never feeds the p95 gate


async def test_warmup_never_raises() -> None:
    server = FakeOllama(raise_on={"/api/version", "/v1/systemone"})
    assert await _fast(server).warmup() is False


async def test_usage_metered_at_zero_cost() -> None:
    meter = CostMeter()
    server = FakeOllama()
    await _fast(server, cost_meter=meter).decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert meter.usage.prompt_tokens == 120
    assert meter.usage.completion_tokens == 3
    assert meter.usage.cost_usd == 0.0


async def test_decide_batch_one_result_per_state() -> None:
    server = FakeOllama()
    fast = _fast(server)
    out = await fast.decide_batch([STATE, {"user": "otro"}], {NOUL.id: NOUL}, policy=POLICY)
    assert len(out) == 2
    assert all(not r[NOUL.id].abstained for r in out)
    assert server.calls("/v1/systemone/batch") == []


async def test_aclose_idempotent() -> None:
    fast = _fast(FakeOllama())
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    await fast.aclose()
    await fast.aclose()


# ── review regressions (M2, L2, L5) ───────────────────────────────────────────

NEW_FAST_DIGEST = "0123456789ab" + "c" * 52


async def test_successful_preflight_rechecked_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: an in-place ``ollama pull`` changes the digest; after 60 s the new one is used."""
    now = [1_000.0]
    monkeypatch.setattr(ollama_mod, "_monotonic", lambda: now[0])
    server = FakeOllama()
    fast = _fast(server)
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@9b5bb969e46c"
    server.models[FAST].digest = NEW_FAST_DIGEST
    now[0] += 30
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@9b5bb969e46c"  # inside the TTL: no re-check
    assert len(server.calls("/api/tags")) == 1
    now[0] += 31
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@0123456789ab"
    assert fast.model == "tev1:4b@0123456789ab"
    assert len(server.calls("/api/show")) == 2  # digest changed ⇒ capabilities re-read
    assert len(server.calls("/api/version")) == 1


async def test_unchanged_digest_recheck_only_reads_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(ollama_mod, "_monotonic", lambda: now[0])
    server = FakeOllama()
    fast = _fast(server)
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    now[0] += 61
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@9b5bb969e46c"
    assert len(server.calls("/api/tags")) == 2
    assert len(server.calls("/api/show")) == 1
    now[0] += 30
    await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert len(server.calls("/api/tags")) == 2  # TTL restarted at the re-check


async def test_model_mismatch_forces_recheck_on_next_call() -> None:
    server = FakeOllama(served_model="other:1b")
    fast = _fast(server)
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].reason == "model_mismatch"
    server.served_model = None
    server.models[FAST].digest = NEW_FAST_DIGEST
    out = await fast.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
    assert out[NOUL.id].model == "tev1:4b@0123456789ab"


async def test_http_error_records_latency() -> None:
    """L2: a failing tier must not look healthy to the p95 gate."""
    server = FakeOllama(systemone_status=500)
    accurate = _accurate(server)
    await accurate.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    assert len(accurate.latency_samples) == 1


async def test_transport_error_records_latency() -> None:
    server = FakeOllama()
    accurate = _accurate(server)
    await accurate.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    server.raise_on.add("/v1/systemone")
    await accurate.decide(STATE, {SCORE.id: SCORE}, policy=POLICY)
    assert len(accurate.latency_samples) == 2


async def test_outer_timeout_cancellation_records_latency() -> None:
    server = FakeOllama(systemone_delay_s=1.0)
    accurate = _accurate(server)
    out = await accurate.decide(STATE, {SCORE.id: SCORE}, policy=GatePolicy(timeout_ms=150))
    assert out[SCORE.id].reason == "timeout"
    assert len(accurate.latency_samples) == 1
    assert accurate.latency_samples[0] >= 100.0
    assert accurate.p95_ms() is not None


def test_preflight_lock_is_safe_across_event_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    """L5: two ``asyncio.run`` calls contending on the preflight lock must not raise."""
    now = [1_000.0]
    monkeypatch.setattr(ollama_mod, "_monotonic", lambda: now[0])

    async def contend(server: FakeOllama) -> list[dict[str, Any]]:
        a, b = _fast(server), _fast(server)
        return list(
            await asyncio.gather(
                a.decide(STATE, {NOUL.id: NOUL}, policy=POLICY),
                b.decide(STATE, {NOUL.id: NOUL}, policy=POLICY),
            )
        )

    # Loop 1: transient failure (retry in 30 s) while two tasks contend on the lock.
    first = FakeOllama(raise_on={"/api/show"}, version_delay_s=0.05)
    asyncio.run(contend(first))
    now[0] += 31
    # Loop 2: the same key contends again on a fresh event loop.
    second = FakeOllama(version_delay_s=0.05)
    results = asyncio.run(contend(second))
    assert all(not r[NOUL.id].abstained for r in results)
