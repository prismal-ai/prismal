"""Unit tests for the kokoro subgraph (SPEC-KOK-SG-001, RF-KOK-09/11/12).

Covers the K6 "done when" criterion: the subgraph runs end-to-end with
injected fakes and no provider import (module-level AST guard in
``test_no_provider_imports.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.kokoro import build_kokoro_subgraph, register_kokoro
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from prismal.core.config import Settings
from prismal.souls.manager import SoulsManager

_SOUL_TEMPLATE = """---
name: {name}
description: Test soul {name}
role: {role}
---

You are {name}.
"""

_VERDICT_JSON = json.dumps(
    {
        "decision": "Ship it with safeguards",
        "rationale": "All three lenses agree.",
        "lens_summaries": {"spirit": "ok", "mind": "ok", "heart": "ok"},
        "dissent_retained": [],
    }
)


@pytest.fixture
def souls_root(tmp_path: Path) -> Path:
    for soul_id, role in (("spirit", "values"), ("mind", "logic"), ("heart", "empathy")):
        soul_dir = tmp_path / "available" / soul_id
        soul_dir.mkdir(parents=True)
        (soul_dir / "SOUL.md").write_text(
            _SOUL_TEMPLATE.format(name=soul_id, role=role), encoding="utf-8"
        )
    return tmp_path


def _settings(souls_root: Path, **overrides: object) -> Settings:
    return Settings(souls_dir=str(souls_root), **overrides)  # type: ignore[arg-type]


async def _fake_generate(messages: list[dict[str, str]]) -> str:
    return "we agree on shipping with safeguards"


async def _fake_judge(messages: list[dict[str, str]]) -> str:
    return _VERDICT_JSON


def _build(souls_root: Path, **kwargs: Any) -> SubgraphDefinition:
    settings = kwargs.pop("settings", _settings(souls_root))
    return build_kokoro_subgraph(
        settings=settings,
        souls_manager=SoulsManager(souls_root=souls_root, settings=settings),
        generate_fn=kwargs.pop("generate_fn", _fake_generate),
        judge_fn=kwargs.pop("judge_fn", _fake_judge),
        **kwargs,
    )


# ── topology (K6-02) ─────────────────────────────────────────────────────────


def test_subgraph_has_five_nodes_and_linear_edges(souls_root: Path) -> None:
    definition = _build(souls_root)
    assert definition.name == "kokoro"
    assert definition.entry_point == "load_souls"
    assert list(definition.nodes) == ["load_souls", "deliberate", "judge", "act", "output"]
    assert definition.edges == [
        ("load_souls", "deliberate"),
        ("deliberate", "judge"),
        ("judge", "act"),
        ("act", "output"),
    ]


# ── end-to-end with fakes (RF-KOK-11) ────────────────────────────────────────


async def test_end_to_end_with_fakes(souls_root: Path) -> None:
    definition = _build(souls_root)
    graph = assemble_state_graph(definition).compile()

    result = await graph.ainvoke({"messages": [HumanMessage(content="Should we ship?")]})

    final_message = result["messages"][-1]
    assert "Ship it with safeguards" in str(final_message.content)
    assert "Rationale:" in str(final_message.content)

    kokoro = result["metadata"]["kokoro"]
    assert kokoro["soul_ids"] == ["spirit", "mind", "heart"]
    assert kokoro["deliberation"].rounds_completed >= 1
    assert kokoro["verdict"].decision == "Ship it with safeguards"
    assert "error" not in kokoro


async def test_end_to_end_state_is_namespaced_under_metadata_kokoro(souls_root: Path) -> None:
    """RF-KOK-12: every Kokoro key lives below metadata.kokoro."""
    definition = _build(souls_root)
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="q")]})

    assert set(result["metadata"]) == {"kokoro"}
    assert {"souls", "soul_ids", "deliberation", "verdict"} <= set(result["metadata"]["kokoro"])


async def test_identical_positions_converge_in_one_round(souls_root: Path) -> None:
    """The fake generate returns identical text → Jaccard 1.0 → early stop."""
    definition = _build(souls_root)
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="q")]})

    deliberation = result["metadata"]["kokoro"]["deliberation"]
    assert deliberation.converged is True
    assert deliberation.rounds_completed == 1
    assert len(deliberation.final_positions) == 3


# ── fail-fast on invalid souls (ARCHITECTURE §5) ─────────────────────────────


async def test_missing_soul_fails_fast_without_llm_calls(souls_root: Path) -> None:
    calls = {"generate": 0, "judge": 0}

    async def counting_generate(messages: list[dict[str, str]]) -> str:
        calls["generate"] += 1
        return "x"

    async def counting_judge(messages: list[dict[str, str]]) -> str:
        calls["judge"] += 1
        return "{}"

    settings = _settings(souls_root)
    definition = build_kokoro_subgraph(
        settings=settings,
        souls_manager=SoulsManager(souls_root=souls_root, settings=settings),
        soul_ids=["spirit", "mind", "ghost"],  # ghost does not exist
        generate_fn=counting_generate,
        judge_fn=counting_judge,
    )
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="q")]})

    assert "Kokoro could not deliberate" in str(result["messages"][-1].content)
    assert "ghost" in result["metadata"]["kokoro"]["error"]
    assert calls == {"generate": 0, "judge": 0}  # no LLM call after load failure


async def test_empty_query_yields_error_message(souls_root: Path) -> None:
    definition = _build(souls_root)
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="")]})
    assert "Kokoro could not deliberate" in str(result["messages"][-1].content)


# ── action mode end-to-end (RF-KOK-08) ───────────────────────────────────────


async def test_action_mode_executes_through_injected_judge(souls_root: Path) -> None:
    from prismal.agents.kokoro.judge import KokoroJudgeAgent

    verdict_with_action = json.dumps(
        {
            "decision": "d",
            "rationale": "r",
            "lens_summaries": {},
            "dissent_retained": [],
            "action": {"tool_name": "notify", "args": {"channel": "ops"}},
        }
    )

    async def judge_fn(messages: list[dict[str, str]]) -> str:
        return verdict_with_action

    executor_calls: list[tuple[str, dict[str, Any]]] = []

    async def executor(tool_name: str, args: dict[str, Any]) -> str:
        executor_calls.append((tool_name, args))
        return "notified"

    class AllowInterceptor:
        async def on_tool_start(self, serialized: dict[str, Any], input_str: str) -> None:
            return None

    class StubAudit:
        def log_event(self, event_type: str, payload: dict[str, object]) -> None:
            return None

    settings = _settings(souls_root, kokoro_execute_actions=True)
    judge_agent = KokoroJudgeAgent(
        judge_fn=judge_fn,
        tool_executor=executor,
        interceptor=AllowInterceptor(),  # type: ignore[arg-type]
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    definition = build_kokoro_subgraph(
        settings=settings,
        souls_manager=SoulsManager(souls_root=souls_root, settings=settings),
        generate_fn=_fake_generate,
        judge_agent=judge_agent,
    )
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="q")]})

    assert executor_calls == [("notify", {"channel": "ops"})]
    verdict = result["metadata"]["kokoro"]["verdict"]
    assert verdict.action.executed is True
    assert "Action 'notify' executed: notified" in str(result["messages"][-1].content)


async def test_act_is_passthrough_when_disabled(souls_root: Path) -> None:
    """With kokoro_execute_actions=False the verdict flows through act unchanged."""
    definition = _build(souls_root)
    graph = assemble_state_graph(definition).compile()
    result = await graph.ainvoke({"messages": [HumanMessage(content="q")]})

    verdict = result["metadata"]["kokoro"]["verdict"]
    assert verdict.action is None
    assert "Action" not in str(result["messages"][-1].content)


# ── registration (K6-03) ─────────────────────────────────────────────────────


async def test_register_kokoro_is_idempotent(
    souls_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRISMAL_SOULS_DIR", str(souls_root))
    registry = SubgraphRegistry()

    await register_kokoro(registry, settings=_settings(souls_root))
    first = registry.get("kokoro")
    assert first is not None

    await register_kokoro(registry, settings=_settings(souls_root))  # second call: no-op
    assert registry.get("kokoro") is first
