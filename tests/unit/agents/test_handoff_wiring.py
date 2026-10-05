"""Handoff-envelope wiring into multi-agent patterns (Phase TKE — SPEC-TKE-HND-001).

TKE7-02/03/04: with ``token_efficiency_enabled`` + ``handoff_envelope_enabled``
on, producers (Skynet workers, MoA proposers, Kokoro souls, BRP roles,
``parallel_research`` workers, spawner tasks) attach a :class:`HandoffEnvelope`
and reducers/aggregators read its bounded summary instead of the raw text; the
full text stays reachable through the per-run artifact store (on demand only).
With the flags off every shape and output is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.handoff import (
    HandoffEnvelope,
    build_envelope,
    clear_run_artifact_store,
    envelope_to_message_text,
    get_run_artifact_store,
    handoff_scope,
    handoff_text,
)
from prismal.agents.skynet.types import SwarmOrder, WorkerResult
from prismal.core.config import Settings

# ── shared fixtures ──────────────────────────────────────────────────────────


def _on(**kw: Any) -> Settings:
    return Settings(
        token_efficiency_enabled=True,
        handoff_envelope_enabled=True,
        handoff_summary_max_tokens=40,
        **kw,
    )


def _off() -> Settings:
    return Settings()


def _long(tag: str = "X", n: int = 200) -> str:
    return " ".join(f"{tag} sentence number {i} explains a finding." for i in range(n))


LONG = _long()
#: A fragment only present far beyond the summary cap.
TAIL = "sentence number 199 explains"


@pytest.fixture(autouse=True)
def _clean_run_stores() -> Any:
    yield
    for run in ("r1", "sess", "s-moa", "k1", "sess-k", "brp", "pr", "sp"):
        clear_run_artifact_store(run)


def _env(text: str = LONG, run_id: str = "r1") -> HandoffEnvelope:
    s = _on()
    return build_envelope(
        text,
        store=get_run_artifact_store(run_id, s),
        run_id=run_id,
        tenant="default",
        max_summary_tokens=s.handoff_summary_max_tokens,
    )


# ── helpers in prismal.agents.handoff ────────────────────────────────────────


class TestHelpers:
    def test_handoff_scope_reads_session_and_org(self) -> None:
        state = {"session_id": "s1", "metadata": {"org_id": "acme"}}
        assert handoff_scope(state) == ("s1", "acme")

    def test_handoff_scope_defaults(self) -> None:
        assert handoff_scope({}) == ("run", "default")
        assert handoff_scope(None) == ("run", "default")

    def test_handoff_text_without_envelope_is_raw(self) -> None:
        assert handoff_text("raw text", None) == "raw text"

    def test_handoff_text_with_envelope_is_summary(self) -> None:
        env = _env()
        assert handoff_text(LONG, env) == envelope_to_message_text(env)


# ── Skynet (TKE7-02) ─────────────────────────────────────────────────────────


def _worker(reply: str, settings: Settings) -> Any:
    from prismal.agents.skynet.worker import SwarmWorker

    async def worker_fn(messages: list[dict[str, str]]) -> str:
        return reply

    return SwarmWorker(worker_fn=worker_fn, settings=settings)


class TestSkynetWorker:
    async def test_flag_on_attaches_envelope_and_keeps_raw_output(self) -> None:
        result = await _worker(LONG, _on()).execute(
            SwarmOrder(order_id="o1", instruction="do"), run_id="r1", tenant="default"
        )
        assert result.output == LONG
        assert result.envelope is not None
        assert TAIL not in result.envelope.summary
        ref = result.envelope.artifacts[0]
        full = get_run_artifact_store("r1").get(ref.id, run_id="r1", tenant="default")
        assert full.content == LONG

    async def test_flag_off_no_envelope(self) -> None:
        result = await _worker(LONG, _off()).execute(SwarmOrder(order_id="o1", instruction="do"))
        assert result.envelope is None

    async def test_failed_worker_has_no_envelope(self) -> None:
        from prismal.agents.skynet.worker import SwarmWorker

        async def boom(messages: list[dict[str, str]]) -> str:
            raise RuntimeError("down")

        result = await SwarmWorker(worker_fn=boom, settings=_on()).execute(
            SwarmOrder(order_id="o1", instruction="do"), run_id="r1"
        )
        assert result.success is False
        assert result.envelope is None


class TestSkynetReduce:
    async def test_concat_reads_envelope_summary(self) -> None:
        from prismal.agents.skynet.reduce import reduce_results

        env = _env()
        r = WorkerResult(order_id="o1", output=LONG, success=True, envelope=env)
        answer = await reduce_results("g", [r], strategy="concat", settings=_on())
        assert TAIL not in answer
        assert envelope_to_message_text(env) in answer

    async def test_first_success_reads_envelope_summary(self) -> None:
        from prismal.agents.skynet.reduce import reduce_results

        env = _env()
        r = WorkerResult(order_id="o1", output=LONG, success=True, envelope=env)
        answer = await reduce_results("g", [r], strategy="first_success", settings=_on())
        assert answer == envelope_to_message_text(env)

    async def test_default_synthesis_prompt_uses_summary(self) -> None:
        from prismal.agents.skynet.reduce import reduce_results

        response = MagicMock()
        response.content = "merged"
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        registry = MagicMock()
        registry.get_llm.return_value = llm

        r = WorkerResult(order_id="o1", output=LONG, success=True, envelope=_env())
        with patch("prismal.providers.registry.ProviderRegistry", return_value=registry):
            answer = await reduce_results("g", [r], settings=_on())
        assert answer == "merged"
        prompt = str(llm.ainvoke.call_args.args[0][1].content)
        assert TAIL not in prompt

    async def test_legacy_result_without_envelope_uses_raw(self) -> None:
        from prismal.agents.skynet.reduce import reduce_results

        r = WorkerResult(order_id="o1", output=LONG, success=True)
        answer = await reduce_results("g", [r], strategy="concat", settings=_off())
        assert answer == f"[o1] {LONG}"


class _SpyAudit:
    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        return None


class TestSkynetEvaluator:
    async def test_evaluator_prompt_uses_summary(self) -> None:
        from prismal.agents.skynet.supervisor import SkynetSupervisor

        seen: list[list[dict[str, str]]] = []

        async def evaluate_fn(messages: list[dict[str, str]]) -> tuple[bool, str]:
            seen.append(messages)
            return True, "done"

        sup = SkynetSupervisor(
            evaluate_fn=evaluate_fn,
            audit=_SpyAudit(),  # type: ignore[arg-type]
            settings=_on(),
        )
        r = WorkerResult(order_id="o1", output=LONG, success=True, envelope=_env())
        await sup.evaluate("g", [r])
        assert TAIL not in seen[0][1]["content"]

    async def test_evaluator_without_envelope_sees_raw(self) -> None:
        from prismal.agents.skynet.supervisor import SkynetSupervisor

        seen: list[list[dict[str, str]]] = []

        async def evaluate_fn(messages: list[dict[str, str]]) -> tuple[bool, str]:
            seen.append(messages)
            return True, "done"

        sup = SkynetSupervisor(
            evaluate_fn=evaluate_fn,
            audit=_SpyAudit(),  # type: ignore[arg-type]
            settings=_off(),
        )
        await sup.evaluate("g", [WorkerResult(order_id="o1", output=LONG, success=True)])
        assert TAIL in seen[0][1]["content"]


class TestSkynetSerialization:
    def test_legacy_dict_without_envelope_key_loads(self) -> None:
        from prismal.agents.subgraphs.skynet._helpers import dicts_to_results

        legacy = {"order_id": "o1", "output": "x", "success": True, "error": None}
        (r,) = dicts_to_results([legacy])
        assert r.envelope is None
        assert r.output == "x"

    def test_result_without_envelope_serializes_to_pre_tke_shape(self) -> None:
        from prismal.agents.subgraphs.skynet._helpers import result_to_dict

        r = WorkerResult(order_id="o1", output="x", success=True)
        d = result_to_dict(r)
        assert "envelope" not in d
        expected = asdict(r)
        expected.pop("envelope")
        assert d == expected

    def test_envelope_round_trips(self) -> None:
        from prismal.agents.subgraphs.skynet._helpers import dicts_to_results, result_to_dict

        env = _env()
        r = WorkerResult(order_id="o1", output=LONG, success=True, envelope=env)
        d = result_to_dict(r)
        json.dumps(d["envelope"])  # checkpoint-safe
        (back,) = dicts_to_results([d])
        assert back.envelope == env


class TestSkynetWorkerNode:
    async def test_flag_on_entry_carries_envelope_scoped_to_session(self) -> None:
        from prismal.agents.subgraphs.skynet.worker_node import make_worker_node

        node = make_worker_node(_worker(LONG, _on()))
        state = {"session_id": "sess", "_order": asdict(SwarmOrder("o1", "do"))}
        update = await node(state)
        result = update["parallel_results"][0]["result"]
        env = HandoffEnvelope.from_dict(result["envelope"])
        ref = env.artifacts[0]
        assert ref.run_id == "sess"
        full = get_run_artifact_store("sess").get(ref.id, run_id="sess", tenant="default")
        assert full.content == LONG

    async def test_flag_off_entry_has_no_envelope_key(self) -> None:
        from prismal.agents.subgraphs.skynet.worker_node import make_worker_node

        node = make_worker_node(_worker(LONG, _off()))
        update = await node({"session_id": "sess", "_order": asdict(SwarmOrder("o1", "do"))})
        assert "envelope" not in update["parallel_results"][0]["result"]


# ── Mixture of Agents (TKE7-03) ──────────────────────────────────────────────


def _moa_registry(proposals: dict[str, str]) -> tuple[MagicMock, MagicMock]:
    def _llm(text: str) -> MagicMock:
        resp = MagicMock()
        resp.content = text
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=resp)
        return llm

    llms = {m: _llm(t) for m, t in proposals.items()}
    agg = _llm("final")
    llms["agg"] = agg
    registry = MagicMock()
    registry.get_llm.side_effect = lambda model=None: llms[model]
    return registry, agg


class TestMixtureOfAgents:
    async def test_flag_on_aggregator_reads_proposer_summaries(self) -> None:
        from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents

        registry, agg = _moa_registry({"a": LONG, "b": _long("Y")})
        moa = MixtureOfAgents(["a", "b"], aggregator_model="agg", settings=_on())
        with patch(
            "prismal.agents.patterns.mixture_of_agents.ProviderRegistry",
            return_value=registry,
        ):
            result = await moa.generate("q", state={"session_id": "s-moa"})
        prompt = str(agg.ainvoke.call_args.args[0][1].content)
        assert TAIL not in prompt
        assert len(result.proposer_envelopes) == 2
        assert result.layer_outputs[0] == [LONG, _long("Y")]  # raw kept
        ref = result.proposer_envelopes[0].artifacts[0]
        assert ref.run_id == "s-moa"

    async def test_flag_off_aggregator_reads_raw(self) -> None:
        from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents

        registry, agg = _moa_registry({"a": LONG, "b": "short"})
        moa = MixtureOfAgents(["a", "b"], aggregator_model="agg", settings=_off())
        with patch(
            "prismal.agents.patterns.mixture_of_agents.ProviderRegistry",
            return_value=registry,
        ):
            result = await moa.generate("q", state=None)
        prompt = str(agg.ainvoke.call_args.args[0][1].content)
        assert TAIL in prompt
        assert result.proposer_envelopes == []


# ── Kokoro (TKE7-03) ─────────────────────────────────────────────────────────


def _soul(name: str, role: str) -> Any:
    from prismal.souls.base import Soul, SoulMetadata

    return Soul(
        metadata=SoulMetadata(name=name, description=f"Test {name}", role=role),
        body=f"You are {name}.",
        source_dir=Path("/tmp/souls/available") / name,
    )


async def _same_reply(messages: list[dict[str, str]]) -> str:
    return LONG


def _triad() -> list[Any]:
    from prismal.agents.kokoro.soul_agent import SoulAgent

    return [
        SoulAgent(_soul(n, r), generate_fn=_same_reply)
        for n, r in (("spirit", "values"), ("mind", "logic"), ("heart", "empathy"))
    ]


class _StubAudit:
    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        return None


async def _judge_prompt(deliberation: Any, settings: Settings) -> str:
    from prismal.agents.kokoro.judge import KokoroJudgeAgent

    seen: list[list[dict[str, str]]] = []

    async def judge_fn(messages: list[dict[str, str]]) -> str:
        seen.append(messages)
        return json.dumps({"decision": "ship", "rationale": "ok"})

    agent = KokoroJudgeAgent(
        judge_fn=judge_fn,
        audit=_StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    await agent.judge("q", deliberation)
    return seen[0][1]["content"]


class TestKokoro:
    async def test_flag_on_souls_enveloped_and_judge_reads_summaries(self) -> None:
        from prismal.agents.kokoro.deliberation import deliberate

        s = _on()
        result = await deliberate("q", _triad(), settings=s, run_id="k1")
        assert len(result.final_envelopes) == 3
        assert result.final_envelopes[0].artifacts[0].run_id == "k1"
        prompt = await _judge_prompt(result, s)
        assert TAIL not in prompt

    async def test_flag_off_no_envelopes_and_judge_reads_raw(self) -> None:
        from prismal.agents.kokoro.deliberation import deliberate

        s = _off()
        result = await deliberate("q", _triad(), settings=s)
        assert result.final_envelopes == ()
        prompt = await _judge_prompt(result, s)
        assert TAIL in prompt

    async def test_deliberate_node_scopes_envelopes_to_session(self) -> None:
        from prismal.agents.subgraphs.kokoro.deliberate_node import make_deliberate_node

        node = make_deliberate_node(generate_fn=_same_reply, settings=_on())
        state = {
            "session_id": "sess-k",
            "messages": [HumanMessage(content="q")],
            "metadata": {
                "kokoro": {
                    "souls": [
                        _soul("spirit", "values"),
                        _soul("mind", "logic"),
                        _soul("heart", "empathy"),
                    ]
                }
            },
        }
        update = await node(state)
        delib = update["metadata"]["kokoro"]["deliberation"]
        assert delib.final_envelopes[0].artifacts[0].run_id == "sess-k"


# ── Blind Review Pipeline (TKE7-03) ──────────────────────────────────────────


class _NoopInterceptor:
    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **_: Any) -> None:
        return None


class TestBlindReview:
    async def test_spec_agent_flag_on_attaches_envelope_keeps_artifact(self) -> None:
        from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import (
            make_spec_agent_node,
        )

        async def spec_fn(goal: str) -> str:
            return LONG

        node = make_spec_agent_node(spec_fn, settings=_on())
        update = await node({"session_id": "brp", "messages": [HumanMessage(content="g")]})
        br = update["metadata"]["blind_review"]
        assert br["spec_artifact"] == LONG  # reviewers/implementer still get the full spec
        env = HandoffEnvelope.from_dict(br["envelopes"]["spec_agent"])
        assert TAIL not in env.summary
        assert env.artifacts[0].run_id == "brp"

    async def test_implementer_flag_on_attaches_envelope_keeps_artifact(self) -> None:
        from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
            make_implementer_agent_node,
        )

        async def impl_fn(spec: str, issues: Any) -> str:
            return LONG

        node = make_implementer_agent_node(impl_fn, settings=_on(), interceptor=_NoopInterceptor())
        state = {"session_id": "brp", "metadata": {"blind_review": {"spec_artifact": "S"}}}
        update = await node(state)
        br = update["metadata"]["blind_review"]
        assert br["implementation_artifact"] == LONG
        env = HandoffEnvelope.from_dict(br["envelopes"]["implementer"])
        assert TAIL not in env.summary

    async def test_flag_off_no_envelopes_key(self) -> None:
        from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
            make_implementer_agent_node,
        )
        from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import (
            make_spec_agent_node,
        )

        async def spec_fn(goal: str) -> str:
            return LONG

        async def impl_fn(spec: str, issues: Any) -> str:
            return LONG

        spec_update = await make_spec_agent_node(spec_fn, settings=_off())(
            {"messages": [HumanMessage(content="g")]}
        )
        assert "envelopes" not in spec_update["metadata"]["blind_review"]
        impl_update = await make_implementer_agent_node(
            impl_fn, settings=_off(), interceptor=_NoopInterceptor()
        )({"metadata": {"blind_review": {"spec_artifact": "S"}}})
        assert "envelopes" not in impl_update["metadata"]["blind_review"]


# ── parallel_research (TKE7-03) ──────────────────────────────────────────────


class TestParallelResearch:
    async def _run_worker(self, settings: Settings) -> dict[str, Any]:
        from prismal.agents import parallel_research as pr

        response = MagicMock()
        response.content = LONG
        with (
            patch.object(pr, "get_settings", return_value=settings),
            patch.object(pr, "ProviderRegistry"),
            patch.object(pr, "get_tools_for_agent", return_value=[]),
            patch.object(pr, "react_loop", AsyncMock(return_value=response)),
        ):
            update = await pr.parallel_researcher_worker(
                {"session_id": "pr", "_task": "investigate"}  # type: ignore[typeddict-item]
            )
        entry: dict[str, Any] = update["parallel_results"][0]
        return entry

    async def test_worker_flag_on_attaches_envelope(self) -> None:
        entry = await self._run_worker(_on())
        assert entry["result"] == LONG
        env = HandoffEnvelope.from_dict(entry["envelope"])
        assert env.artifacts[0].run_id == "pr"

    async def test_worker_flag_off_shape_unchanged(self) -> None:
        entry = await self._run_worker(_off())
        assert entry == {
            "agent": "parallel_researcher_worker",
            "task": "investigate",
            "result": LONG,
        }

    async def test_aggregator_reads_envelope_summary(self) -> None:
        from prismal.agents.parallel_research import research_aggregator_node

        env = _env(run_id="pr")
        state = {
            "parallel_results": [
                {
                    "agent": "parallel_researcher_worker",
                    "task": "investigate",
                    "result": LONG,
                    "envelope": env.to_dict(),
                }
            ]
        }
        update = await research_aggregator_node(state)  # type: ignore[arg-type]
        content = str(update["messages"][0].content)
        assert TAIL not in content
        assert env.summary in content


# ── spawner (TKE7-03) ────────────────────────────────────────────────────────


class TestSpawner:
    async def test_flag_on_str_result_gets_envelope_raw_result_unchanged(self) -> None:
        from prismal.agents.spawner import SubAgentSpawner

        async def sub() -> str:
            return LONG

        spawner = SubAgentSpawner(settings=_on())
        task = await spawner.spawn(sub, "t1", timeout=2.0, run_id="sp")
        assert await task == LONG
        env = spawner.get_envelope("t1")
        assert env is not None
        assert TAIL not in env.summary
        assert env.artifacts[0].run_id == "sp"

    async def test_flag_off_no_envelope(self) -> None:
        from prismal.agents.spawner import SubAgentSpawner

        async def sub() -> str:
            return LONG

        spawner = SubAgentSpawner(settings=_off())
        task = await spawner.spawn(sub, "t1", timeout=2.0)
        assert await task == LONG
        assert spawner.get_envelope("t1") is None

    async def test_non_str_result_has_no_envelope(self) -> None:
        from prismal.agents.spawner import SubAgentSpawner

        async def sub() -> int:
            return 42

        spawner = SubAgentSpawner(settings=_on())
        task = await spawner.spawn(sub, "t1", timeout=2.0, run_id="sp")
        assert await task == 42
        assert spawner.get_envelope("t1") is None
