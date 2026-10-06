"""DEC-RAG wiring inside ``CRAGPipeline`` (Phase DEC — SPEC-DEC-INT-005, DEC5-03)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.core.config import Settings
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord, CalibrationStore
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.integrations.rag import RAG_DECISION_ID
from prismal.decisions.runtime import DecisionRuntime, set_decision_runtime
from prismal.decisions.shadow import ShadowRunner, reset_demotions
from prismal.decisions.types import Decision
from prismal.rag.crag import CRAGPipeline


@pytest.fixture(autouse=True)
def _reset() -> Any:
    set_decision_runtime(None)
    reset_demotions()
    yield
    set_decision_runtime(None)
    reset_demotions()


def _doc(content: str) -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": "f.txt"}
    return doc


class ByContent(FakeDecisionAdapter):
    async def _answer(self, state: Any, questions: Any) -> dict[str, Decision]:
        self.calls.append((dict(state), tuple(questions)))
        q = questions[RAG_DECISION_ID]
        chunk = state["chunk"]
        p = 0.99 if "paris" in chunk else 0.01 if "weather" in chunk else 0.5
        return {q.id: Decision.from_probabilities(q, {"true": p}, backend="laya", model="lm")}


def _settings(tmp_path: Path, mode: str) -> Settings:
    base = Settings().model_dump()
    base.update(
        decisions_enabled=True,
        decision_rag_relevance_enabled=True,
        decision_modes={"rag": mode},
        decision_calibration_dir=str(tmp_path),
    )
    return Settings.model_construct(**base)


def _install(tmp_path: Path, s: Settings) -> ShadowRunner:
    store = CalibrationStore(tmp_path)
    store.save(
        CalibrationRecord.from_dict(
            {
                "decision_id": RAG_DECISION_ID,
                "model": "lm",
                "method": "temperature",
                "params": {"temperature": 1.0},
                "tau": 0.85,
                "target_precision": 0.95,
                "ece_before": 0.3,
                "ece_after": 0.02,
                "n_by_language": {"en": 900, "es": 900},
                "precision_by_language": {"en": 0.99, "es": 0.99},
                "version": "v1",
            }
        )
    )
    cascade = DecisionCascade(
        [ByContent({}, name="laya", model="lm")], calibration=store, settings=s
    )
    runner = ShadowRunner(cascade, settings=s)
    set_decision_runtime(DecisionRuntime(cascade=cascade, settings=s, shadow=runner))
    return runner


def _llm(grades: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[MagicMock(content=g) for g in grades] + [MagicMock(content="answer")]
    )
    return llm


async def _run(s: Settings, llm: MagicMock) -> Any:
    store = MagicMock()
    store.similarity_search.return_value = [
        (_doc("paris is the capital"), 0.9),
        (_doc("weather report"), 0.8),
        (_doc("ambiguous"), 0.7),
    ]
    with patch("prismal.rag.crag.ProviderRegistry") as registry:
        registry.return_value.get_llm.return_value = llm
        return await CRAGPipeline(vector_store=store, settings=s).run("capital of france")


async def test_enforce_grades_accepted_chunks_without_llm(tmp_path: Path) -> None:
    s = _settings(tmp_path, "enforce")
    _install(tmp_path, s)
    llm = _llm(["0.9"])  # only the ambiguous chunk reaches the LLM grader
    result = await _run(s, llm)
    assert llm.ainvoke.await_count == 2  # 1 grade + 1 generate
    scores = {c.content: c.relevance_score for c in result.sources}
    assert scores == {"paris is the capital": 1.0, "ambiguous": 0.9}


async def test_shadow_keeps_llm_grades_and_records(tmp_path: Path) -> None:
    s = _settings(tmp_path, "shadow")
    runner = _install(tmp_path, s)
    llm = _llm(["0.9", "0.1", "0.2"])
    result = await _run(s, llm)
    assert llm.ainvoke.await_count == 4
    assert [c.content for c in result.sources] == ["paris is the capital"]
    import asyncio

    for _ in range(100):
        if runner.report("rag").n == 3:
            break
        await asyncio.sleep(0.01)
    assert runner.report("rag").n == 3


async def test_off_unchanged(tmp_path: Path) -> None:
    llm = _llm(["0.9", "0.1", "0.2"])
    result = await _run(Settings(), llm)
    assert llm.ainvoke.await_count == 4
    assert len(result.sources) == 1
