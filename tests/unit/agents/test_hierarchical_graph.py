"""Tests for the Phase 40 / SPEC-042 hierarchical multi-agent graph.

Covers AC-042-1 (domain orchestrator topology), AC-042-2 (root
supervisor routing + simplified prompt), AC-042-3 (domain supervisor
factory loop-breaker + iteration cap), AC-042-4 (flat mode remains
unchanged when ``LIGHTAGENT_HIERARCHICAL_MODE=false``), and AC-042-5
(the explicit test coverage requirement).

These tests never invoke real LLMs or real leaf agents — every LLM
call is mocked via ``ProviderRegistry`` and every orchestrator leaf
that matters to a given test is mocked via ``patch`` at the module
level. Topology assertions run against the compiled
``CompiledStateGraph`` nodes mapping and the un-compiled
``SubgraphDefinition`` objects.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.domain_supervisor import (
    _MAX_DOMAIN_ITERATIONS,
    make_domain_supervisor,
)
from lightagent.agents.graph import _hierarchical_router
from lightagent.agents.supervisor import (
    HIERARCHICAL_MEMBERS,
    hierarchical_supervisor_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(
    *,
    message: str = "do something",
    current_agent: str = "",
    metadata: dict[str, Any] | None = None,
    extra_messages: list[Any] | None = None,
) -> Any:
    """Build the minimal state dict the supervisors expect.

    Typed as :class:`~typing.Any` because the full ``AgentState``
    ``TypedDict`` has many required fields we don't touch here.
    """
    messages: list[Any] = [HumanMessage(content=message)]
    if extra_messages:
        messages.extend(extra_messages)
    return {
        "messages": messages,
        "current_agent": current_agent,
        "next_agent": None,
        "task_plan": [],
        "completed_tasks": [],
        "pending_tasks": [],
        "metadata": metadata or {},
        "session_id": "u-1",
        "created_at": "",
        "iteration_count": 0,
        "token_count": 0,
        "estimated_cost_usd": 0.0,
        "risk_score": 0.0,
        "permissions_granted": [],
        "security_flags": [],
        "retrieved_docs": [],
        "doc_grades": [],
        "tool_results": [],
        "tool_errors": [],
        "parallel_results": [],
        "dev_pipeline_modules": [],
    }


def _mock_llm(*contents: str) -> MagicMock:
    """Build an LLM mock whose ``ainvoke`` returns ``contents`` in order."""
    responses = [MagicMock(content=c) for c in contents]

    async def _ainvoke(*_args: Any, **_kwargs: Any) -> Any:
        return responses.pop(0) if len(responses) > 1 else responses[0]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=_ainvoke)
    return llm


def _patch_supervisor_llm(llm: MagicMock) -> Any:
    """Context manager patching every external dependency hit by
    ``hierarchical_supervisor_node``: provider, memory recall, settings.
    """
    import contextlib

    @contextlib.contextmanager
    def _cm() -> Any:
        with (
            patch(
                "lightagent.agents.supervisor.ProviderRegistry"
            ) as provider,
            patch(
                "lightagent.agents.supervisor._recall_memory_context",
                new=AsyncMock(return_value=""),
            ),
            patch(
                "lightagent.agents.supervisor.get_settings"
            ) as mock_settings,
        ):
            provider.return_value.get_llm_with_fallback.return_value = llm
            mock_settings.return_value.memory_recall_limit = 0
            mock_settings.return_value.memory_extraction_enabled = False
            yield provider

    return _cm()


# ---------------------------------------------------------------------------
# _hierarchical_router (AC-042-2)
# ---------------------------------------------------------------------------


class TestHierarchicalRouter:
    """The root supervisor conditional edge is a tiny mapper — these
    tests pin its behaviour so a future refactor can't silently break
    END routing or cross-domain leakage.
    """

    def test_none_routes_to_end(self) -> None:
        assert _hierarchical_router(_state()) == "__end__"

    def test_literal_end_routes_to_end(self) -> None:
        state = _state()
        state["next_agent"] = "END"
        assert _hierarchical_router(state) == "__end__"

    def test_research_orchestrator(self) -> None:
        state = _state()
        state["next_agent"] = "research_orchestrator"
        assert (
            _hierarchical_router(state) == "research_orchestrator"
        )

    def test_engineering_orchestrator(self) -> None:
        state = _state()
        state["next_agent"] = "engineering_orchestrator"
        assert (
            _hierarchical_router(state) == "engineering_orchestrator"
        )

    def test_analysis_orchestrator(self) -> None:
        state = _state()
        state["next_agent"] = "analysis_orchestrator"
        assert (
            _hierarchical_router(state) == "analysis_orchestrator"
        )

    def test_cron_manager(self) -> None:
        state = _state()
        state["next_agent"] = "cron_manager"
        assert _hierarchical_router(state) == "cron_manager"


# ---------------------------------------------------------------------------
# hierarchical_supervisor_node — AC-042-2 routing + direct answer path
# ---------------------------------------------------------------------------


class TestHierarchicalSupervisorNodeRouting:
    async def test_research_domain_routing(self) -> None:
        """AC-042-2: 'investigate' keyword → research_orchestrator."""
        llm = _mock_llm("research_orchestrator")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="investigate the AAPL earnings report")
            )
        assert out["current_agent"] == "supervisor"
        assert out["next_agent"] == "research_orchestrator"
        assert out["messages"] == []

    async def test_engineering_domain_routing(self) -> None:
        llm = _mock_llm("engineering_orchestrator")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="refactor the payments module")
            )
        assert out["next_agent"] == "engineering_orchestrator"

    async def test_analysis_domain_routing(self) -> None:
        llm = _mock_llm("analysis_orchestrator")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="run a technical analysis on BTC-USD")
            )
        assert out["next_agent"] == "analysis_orchestrator"

    async def test_cron_manager_routing(self) -> None:
        llm = _mock_llm("cron_manager")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="schedule a daily reminder at 8am")
            )
        assert out["next_agent"] == "cron_manager"

    async def test_case_insensitive_matching(self) -> None:
        """``"RESEARCH_ORCHESTRATOR"`` quoted by the LLM must still match."""
        llm = _mock_llm('"RESEARCH_ORCHESTRATOR"')
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="find info about quantum computing")
            )
        assert out["next_agent"] == "research_orchestrator"

    async def test_invalid_routing_defaults_to_end(self) -> None:
        """Nonsense LLM responses route to END without raising."""
        # The END path calls the LLM a second time for the direct answer,
        # so we pre-seed two responses.
        llm = _mock_llm("go to the moon", "I cannot help with that.")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(_state())
        assert out["next_agent"] is None

    async def test_cross_domain_leaf_rejected(self) -> None:
        """If the LLM picks a flat leaf (e.g. ``"coder"``), default to END."""
        llm = _mock_llm("coder", "Sure, here's the answer.")
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(
                _state(message="write a helper function")
            )
        assert out["next_agent"] is None


class TestHierarchicalSupervisorNodeLoopBreaker:
    """SPEC-042 shares the loop-breaker semantics with the flat
    ``supervisor_node`` — one specialist response per turn.
    """

    async def test_loop_breaker_triggers_on_ai_from_specialist(
        self,
    ) -> None:
        """A trailing AIMessage whose ``current_agent`` is not the
        supervisor terminates the turn without calling the LLM.
        """
        llm = _mock_llm("engineering_orchestrator")  # should not run
        state = _state(
            message="original request",
            current_agent="research_orchestrator",
            extra_messages=[AIMessage(content="research result")],
        )
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(state)

        assert out["next_agent"] is None
        assert out["messages"] == []
        # LLM must NOT have been invoked.
        llm.ainvoke.assert_not_called()

    async def test_loop_breaker_does_not_trigger_for_self_ai_message(
        self,
    ) -> None:
        """Own AIMessages (generated by the direct-answer path) don't
        accidentally break the loop on the next turn.
        """
        llm = _mock_llm(
            "research_orchestrator"
        )  # would be called if loop breaker misfires
        state = _state(
            message="continue the research",
            current_agent="supervisor",
            extra_messages=[AIMessage(content="previous self answer")],
        )
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(state)

        assert out["next_agent"] == "research_orchestrator"
        llm.ainvoke.assert_awaited_once()


class TestHierarchicalSupervisorNodeDirectAnswer:
    async def test_small_talk_returns_direct_answer(self) -> None:
        """Small-talk → END with a direct AIMessage from the answer path."""
        llm = _mock_llm("END", "¡Hola! ¿En qué puedo ayudarte hoy?")
        with _patch_supervisor_llm(llm), patch(
            "lightagent.agents.supervisor.ProfileManager"
        ) as mock_profile:
            mock_profile.return_value.load_system_prompt.return_value = None
            out = await hierarchical_supervisor_node(
                _state(message="hola")
            )

        assert out["next_agent"] is None
        messages_out = list(out["messages"])  # type: ignore[call-overload]
        assert len(messages_out) == 1
        assert "Hola" in messages_out[0].content

    async def test_end_without_human_tail_skips_answer(self) -> None:
        """If the tail isn't a human turn, the direct-answer branch is
        not taken and no message is emitted.
        """
        llm = _mock_llm("END")  # only one response used
        state = _state(
            message="something",
            current_agent="supervisor",
            extra_messages=[AIMessage(content="self msg")],
        )
        with _patch_supervisor_llm(llm):
            out = await hierarchical_supervisor_node(state)
        # Loop breaker should trigger here first, so next_agent is None
        # and LLM never runs.
        assert out["next_agent"] is None
        assert out["messages"] == []


# ---------------------------------------------------------------------------
# Domain supervisor factory — loop-breaker & iteration cap (AC-042-3)
# ---------------------------------------------------------------------------


class TestDomainSupervisorLoopBreaker:
    """AC-042-3: each domain supervisor has its own loop-breaker and
    independent iteration counter. These tests drive the factory
    directly (not through a compiled subgraph) for speed.
    """

    async def test_loop_breaker_fires_after_specialist_response(
        self,
    ) -> None:
        node = make_domain_supervisor(
            "research",
            ["researcher", "rag_agent"],
            "web + internal lookup",
        )
        llm = _mock_llm("researcher")
        state = _state(
            current_agent="researcher",
            extra_messages=[AIMessage(content="done")],
        )
        with patch(
            "lightagent.agents.domain_supervisor.ProviderRegistry"
        ) as pr:
            pr.return_value.get_llm_with_fallback.return_value = llm
            out = await node(state)
        assert out["next_agent"] is None
        llm.ainvoke.assert_not_called()

    async def test_iteration_cap_forces_end(self) -> None:
        """When the per-domain counter hits the cap the supervisor
        force-routes to END without calling the LLM.
        """
        node = make_domain_supervisor(
            "research", ["researcher"], "description"
        )
        state = _state(
            metadata={
                "domain_research": {
                    "iteration_count": _MAX_DOMAIN_ITERATIONS
                }
            }
        )
        llm = _mock_llm("researcher")
        with patch(
            "lightagent.agents.domain_supervisor.ProviderRegistry"
        ) as pr:
            pr.return_value.get_llm_with_fallback.return_value = llm
            out = await node(state)

        assert out["next_agent"] is None
        llm.ainvoke.assert_not_called()
        slot = out["metadata"]["domain_research"]
        assert slot["iteration_count"] == _MAX_DOMAIN_ITERATIONS + 1

    async def test_per_domain_isolation(self) -> None:
        """Two factories write to independent metadata slots."""
        research = make_domain_supervisor(
            "research", ["researcher"], "desc"
        )
        engineering = make_domain_supervisor(
            "engineering", ["coder"], "desc"
        )
        with patch(
            "lightagent.agents.domain_supervisor.ProviderRegistry"
        ) as pr:
            pr.return_value.get_llm_with_fallback.return_value = (
                _mock_llm("researcher")
            )
            out1 = await research(_state())
            pr.return_value.get_llm_with_fallback.return_value = (
                _mock_llm("coder")
            )
            out2 = await engineering(
                _state(metadata=out1["metadata"])
            )
        # Each domain has its own independent counter.
        assert out2["metadata"]["domain_research"]["iteration_count"] == 1
        assert (
            out2["metadata"]["domain_engineering"]["iteration_count"] == 1
        )

    async def test_cross_domain_routing_rejected(self) -> None:
        """A domain supervisor must never route to an agent outside its
        declared list — even if the LLM returns one.
        """
        node = make_domain_supervisor(
            "research", ["researcher", "rag_agent"], "desc"
        )
        llm = _mock_llm("coder")  # coder is not in RESEARCH_AGENTS
        with patch(
            "lightagent.agents.domain_supervisor.ProviderRegistry"
        ) as pr:
            pr.return_value.get_llm_with_fallback.return_value = llm
            out = await node(_state())
        assert out["next_agent"] is None


# ---------------------------------------------------------------------------
# AC-042-4: backward compatibility — flat mode untouched
# ---------------------------------------------------------------------------


class TestFlatModeUnchangedWhenHierarchicalDisabled:
    async def test_flat_mode_does_not_invoke_hierarchical_builder(
        self,
    ) -> None:
        """With ``hierarchical_mode=False`` (the default),
        ``get_async_compiled_graph()`` must not call
        ``_build_hierarchical_graph()``. This is the contract that
        guarantees zero regression when the feature flag is off.
        """
        import lightagent.agents.graph as graph_module

        graph_module._async_graph = None  # reset singleton
        try:
            with (
                patch(
                    "lightagent.agents.graph.get_settings"
                ) as mock_settings,
                patch(
                    "lightagent.agents.graph._build_hierarchical_graph",
                    new=AsyncMock(),
                ) as mock_hier,
            ):
                mock_settings.return_value.hierarchical_mode = False
                mock_settings.return_value.db_url = ""
                mock_settings.return_value.enable_subgraphs = False
                compiled = await graph_module.get_async_compiled_graph()

            mock_hier.assert_not_called()
            # And the flat leaves are wired at the root.
            assert "researcher" in compiled.nodes
            assert "coder" in compiled.nodes
            assert "codeact" in compiled.nodes
            assert "supervisor" in compiled.nodes
            # Hierarchical orchestrators must NOT be at the root.
            assert "research_orchestrator" not in compiled.nodes
            assert "engineering_orchestrator" not in compiled.nodes
            assert "analysis_orchestrator" not in compiled.nodes
        finally:
            graph_module._async_graph = None

    async def test_hierarchical_mode_root_has_only_domain_targets(
        self,
    ) -> None:
        """With ``hierarchical_mode=True``, the compiled root graph
        must expose exactly the 4 routing targets (+ supervisor) and
        NONE of the flat leaves.
        """
        import lightagent.agents.graph as graph_module

        graph_module._async_graph = None
        try:
            with patch(
                "lightagent.agents.graph.get_settings"
            ) as mock_settings:
                mock_settings.return_value.hierarchical_mode = True
                mock_settings.return_value.db_url = ""
                mock_settings.return_value.enable_subgraphs = True
                mock_settings.return_value.memory_recall_limit = 0
                mock_settings.return_value.memory_extraction_enabled = (
                    False
                )
                compiled = await graph_module.get_async_compiled_graph()

            node_names = set(compiled.nodes.keys())
            assert "supervisor" in node_names
            for target in HIERARCHICAL_MEMBERS:
                assert target in node_names, target
            # Flat leaves must NOT be at the root of the hierarchical
            # graph — they live inside the orchestrators.
            for flat_leaf in (
                "researcher",
                "coder",
                "codeact",
                "rag_agent",
                "planner",
                "data_analyst",
                "file_manager",
                "skill_manager",
            ):
                assert flat_leaf not in node_names, flat_leaf
        finally:
            graph_module._async_graph = None

    async def test_graph_singleton_is_reused(self) -> None:
        """``get_async_compiled_graph()`` returns the cached instance."""
        import lightagent.agents.graph as graph_module

        graph_module._async_graph = None
        try:
            with patch(
                "lightagent.agents.graph.get_settings"
            ) as mock_settings:
                mock_settings.return_value.hierarchical_mode = True
                mock_settings.return_value.db_url = ""
                mock_settings.return_value.enable_subgraphs = True
                mock_settings.return_value.memory_recall_limit = 0
                mock_settings.return_value.memory_extraction_enabled = (
                    False
                )
                first = await graph_module.get_async_compiled_graph()
                second = await graph_module.get_async_compiled_graph()
            assert first is second
        finally:
            graph_module._async_graph = None


# ---------------------------------------------------------------------------
# HIERARCHICAL_MEMBERS contract
# ---------------------------------------------------------------------------


class TestHierarchicalMembers:
    def test_contains_exactly_four_targets(self) -> None:
        assert len(HIERARCHICAL_MEMBERS) == 4

    def test_domain_orchestrators_and_cron(self) -> None:
        assert set(HIERARCHICAL_MEMBERS) == {
            "research_orchestrator",
            "engineering_orchestrator",
            "analysis_orchestrator",
            "cron_manager",
        }

    def test_order_deterministic(self) -> None:
        """Order matters for prompt determinism — pin it explicitly."""
        assert HIERARCHICAL_MEMBERS == [
            "research_orchestrator",
            "engineering_orchestrator",
            "analysis_orchestrator",
            "cron_manager",
        ]
