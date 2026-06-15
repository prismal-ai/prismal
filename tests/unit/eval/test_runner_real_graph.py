"""EvalRunner against the REAL compiled graph with fakes (Phase V — V2 done-when).

This is the scaffold-gap proof at the harness level: a case runs through
``get_async_compiled_graph()`` (the real supervisor graph) with a deterministic
fake LLM and ``build_test_runtime`` fakes, and yields a populated, terminated
``Trajectory``. No API keys, no network.

Marked ``eval`` so it runs under ``pytest -m eval`` with fakes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from prismal.eval.runner import EvalRunner
from prismal.eval.types import EvalCase

pytestmark = pytest.mark.eval


async def test_run_case_through_real_graph_with_fakes() -> None:
    """A case runs end-to-end through the real graph and is captured + terminated."""

    async def fake_ainvoke(_messages: object) -> AIMessage:
        return AIMessage(content="END")

    case = EvalCase(
        id="real-001",
        input="Please reply with a short greeting, nothing else.",
        assertions=[],
        setup={"tool_provider": "fake", "vector_store": "fake", "seed": 7},
    )

    with (
        patch("prismal.providers.registry.ProviderRegistry.get_llm_with_fallback") as mock_llm,
        patch("prismal.agents.supervisor._recall_memory_context", new=AsyncMock(return_value="")),
        patch("prismal.agents.supervisor._spawn_memory_extraction", new=MagicMock()),
    ):
        mock_llm.return_value.ainvoke = fake_ainvoke
        mock_llm.return_value.bind_tools.return_value = mock_llm.return_value

        runner = EvalRunner()  # default real graph + build_test_runtime fakes
        result = await runner.run_case(case)

    assert result.case_id == "real-001"
    assert result.trajectory.terminated is True
    assert result.passed is True  # no assertions + terminated
    # The supervisor node was visited and an answer was produced.
    assert "supervisor" in result.trajectory.visited_nodes
    assert isinstance(result.trajectory.final_answer, str)
