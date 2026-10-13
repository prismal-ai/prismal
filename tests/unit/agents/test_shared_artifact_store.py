"""One artifact store per process and backend (TKE4/TKE5 follow-up).

The compactor and the tool-output budget each built their own in-memory store,
so an artifact one of them wrote could never be read back through another.
``shared_artifact_store`` returns the same instance for the same backend; the
(tenant, run_id) scope still isolates runs inside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prismal.agents import artifacts
from prismal.agents.artifacts import ArtifactNotFound, shared_artifact_store
from prismal.agents.context_compaction import ContextCompactor
from prismal.agents.tool_output_budget import ToolOutputBudget
from prismal.core.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    artifacts._SHARED_STORES.clear()


def _settings(**update: object):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(update=update)


def test_same_backend_returns_the_same_store() -> None:
    s = _settings(artifact_store_backend="memory")
    assert shared_artifact_store(s) is shared_artifact_store(s)


def test_sqlite_stores_are_keyed_by_path(tmp_path: Path) -> None:
    a = _settings(artifact_store_backend="sqlite", artifact_store_path=str(tmp_path / "a.db"))
    b = _settings(artifact_store_backend="sqlite", artifact_store_path=str(tmp_path / "b.db"))
    assert shared_artifact_store(a) is shared_artifact_store(a)
    assert shared_artifact_store(a) is not shared_artifact_store(b)


def test_runs_stay_isolated_inside_the_shared_store() -> None:
    store = shared_artifact_store(_settings())
    ref = store.put("payload", run_id="run-a", tenant="t", kind="tool_output")
    with pytest.raises(ArtifactNotFound):
        store.get(ref.id, run_id="run-b", tenant="t")


def test_budget_spill_is_readable_from_the_shared_store() -> None:
    s = _settings(token_efficiency_enabled=True, tool_output_budget_enabled=True)
    budget = ToolOutputBudget(settings=s)
    out = budget.apply("big", "x" * 20_000, run_id="run-1", tenant="t")
    assert out.artifact is not None
    got = shared_artifact_store(s).get(out.artifact.id, run_id="run-1", tenant="t")
    assert got.content.startswith("x")


async def test_masked_observation_is_readable_from_the_shared_store() -> None:
    s = _settings(
        token_efficiency_enabled=True,
        context_compaction_enabled=True,
        context_compaction_strategy="mask_observations",
        context_mask_keep_turns=1,
        context_compaction_max_messages=2,
    )
    messages = [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "t", "args": {}}]),
        ToolMessage(content="secret observation " * 50, tool_call_id="c1"),
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "t", "args": {}}]),
        ToolMessage(content="latest", tool_call_id="c2"),
        AIMessage(content="done"),
    ]
    out = await ContextCompactor(settings=s).compact_list(messages, run_id="run-2", tenant="t")
    stub = out[2].content
    assert "artifact=" in stub
    artifact_id = stub.split("artifact=")[1].split()[0]
    got = shared_artifact_store(s).get(artifact_id, run_id="run-2", tenant="t")
    assert got.content.startswith("secret observation")
