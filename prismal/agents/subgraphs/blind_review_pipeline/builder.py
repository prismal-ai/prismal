"""Builder for the Blind Review Pipeline subgraph (Phase BRP4, SPEC-BRP-SUB-001).

Assembles the four-agent pipeline (spec → implementer → two blind reviewers →
deterministic synthesis) with a bounded correction loop and the reused HITL
approval trio, and registers it with the
:class:`~prismal.agents.subgraphs.registry.SubgraphRegistry`.

Topology (sequential reviewers — see the design note below)::

    spec_agent → implementer → reviewer_a → reviewer_b → synthesis
                   ^                                        │
                   └────────── score_gate (fail) ──────────┘
    synthesis → score_gate (pass) → approval_seed → human_approval → hitl_gate → END

**Design note (deviation from ARCHITECTURE.md §3.2 fan-out).** The spec's
two-way ``implementer → {reviewer_a, reviewer_b}`` fan-out is not viable on the
shared ``AgentState``: both reviewers write the ``metadata`` channel, which has
no reducer, so a concurrent superstep raises LangGraph's ``InvalidUpdateError``
("can receive only one value per step"). Rather than fork ``AgentState``
repo-wide for one feature (rejected by DD-BRP-001), the reviewers run
sequentially. Independence/blindness is unaffected — it is guaranteed by the
narrow ``(spec, artifact)`` input contract and the AST/runtime guards, not by
execution concurrency; the only cost is reviewer latency (a non-functional
concern, ARCHITECTURE.md §7).
"""

from __future__ import annotations

import structlog

from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
    ImplementerFn,
    make_implementer_agent_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import (
    ReviewerFn,
    make_reviewer_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import (
    SpecFn,
    make_spec_agent_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.synthesis import (
    SynthesizeFn,
    make_synthesis_node,
)
from prismal.agents.subgraphs.factory import SubgraphFactory
from prismal.agents.subgraphs.gates import (
    hitl_gate,
    human_approval_node,
    score_gate,
    seed_hitl_metadata,
)
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from prismal.core.config import Settings, get_settings

logger = structlog.get_logger("prismal.subgraphs.blind_review_pipeline.builder")

_NAME = "blind_review_pipeline"
_DESCRIPTION = "Blind Review Pipeline: spec → implement → two blind reviewers → synthesis → HITL"
_ARTIFACT_FIELD = "blind_review.implementation_artifact"

_COMPILED_GRAPHS: dict[str, object] = {}


def build_blind_review_pipeline_subgraph(
    spec_fn: SpecFn | None = None,
    implementer_fn: ImplementerFn | None = None,
    reviewer_a_fn: ReviewerFn | None = None,
    reviewer_b_fn: ReviewerFn | None = None,
    synthesize_fn: SynthesizeFn | None = None,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the ``blind_review_pipeline`` :class:`SubgraphDefinition`.

    Every role accepts an injected callable so the subgraph runs end-to-end with
    fakes (no LLM backend). ``score_gate`` and the HITL trio
    (``seed_hitl_metadata`` / ``human_approval_node`` / ``hitl_gate``) are reused
    from ``gates.py`` unmodified.
    """
    s = settings or get_settings()

    reviewer_gate = score_gate(
        field="blind_review.synthesis.report.score",
        threshold=s.blind_review_approval_threshold,
        on_pass="approval_seed",  # noqa: S106
        on_fail="implementer",
        max_iterations=s.blind_review_max_iterations,
    )

    def synthesis_gate(state: dict[str, object]) -> str:
        """Score gate (reused) plus a HITL-enablement branch.

        On a passing score the run enters the HITL approval sub-flow only when
        ``settings.hitl_enabled`` is True; otherwise it routes straight to END
        (``human_approval_node`` always raises ``interrupt()``, so bypassing the
        gate alone would still pause the run — the enablement check must gate
        entry into the sub-flow, not just its exit routing).
        """
        decision = reviewer_gate(state)
        if decision == "approval_seed" and not get_settings().hitl_enabled:
            return "__end__"
        return decision

    approval_seed = seed_hitl_metadata(artifact_field=_ARTIFACT_FIELD, risk_level="HIGH")
    hitl = hitl_gate(
        artifact_field=_ARTIFACT_FIELD,
        on_approve="__end__",
        on_reject="implementer",
        risk_level="HIGH",
        bypass_condition=lambda _s: not get_settings().hitl_enabled,
    )

    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="spec_agent",
        nodes={
            "spec_agent": make_spec_agent_node(spec_fn, settings=s),
            "implementer": make_implementer_agent_node(implementer_fn, settings=s),
            "reviewer_a": make_reviewer_node(
                "reviewer_a",
                model_id=s.blind_review_reviewer_a_model,
                capabilities=s.blind_review_reviewer_a_capabilities,
                reviewer_fn=reviewer_a_fn,
                settings=s,
            ),
            "reviewer_b": make_reviewer_node(
                "reviewer_b",
                model_id=s.blind_review_reviewer_b_model,
                capabilities=s.blind_review_reviewer_b_capabilities,
                reviewer_fn=reviewer_b_fn,
                settings=s,
            ),
            "synthesis": make_synthesis_node(synthesize_fn, settings=s),
            "approval_seed": approval_seed,
            "human_approval": human_approval_node,
        },
        edges=[
            ("spec_agent", "implementer"),
            ("implementer", "reviewer_a"),
            ("reviewer_a", "reviewer_b"),
            ("reviewer_b", "synthesis"),
            ("approval_seed", "human_approval"),
        ],
        conditional_edges={
            "synthesis": synthesis_gate,
            "human_approval": hitl,
        },
    )


async def register_blind_review_pipeline(
    checkpointer_path: str = "data/db/checkpoints_subgraph_blind_review_pipeline.db",
) -> None:
    """Build and register the subgraph. Idempotent — skips if already registered."""
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("blind_review_pipeline.already_registered")
        return

    definition = build_blind_review_pipeline_subgraph()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("blind_review_pipeline.registered")


__all__ = ["build_blind_review_pipeline_subgraph", "register_blind_review_pipeline"]
