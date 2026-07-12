"""Blind Review Pipeline subgraph (Phase BRP).

An opt-in subgraph in which a spec agent and an implementer agent produce an
artifact that two **independent, blind** reviewer agents assess without any
visibility into ``state["messages"]`` — seeing only the spec and the artifact —
before a deterministic synthesis and a bounded correction loop.

Gated by ``settings.blind_review_pipeline_enabled`` (default ``False``); with
the flag off the compiled supervisor graph is byte-for-byte unchanged.

Public value objects (Phase BRP1)::

    from prismal.agents.subgraphs.blind_review_pipeline import SynthesisResult
"""

from __future__ import annotations

from prismal.agents.subgraphs.blind_review_pipeline.builder import (
    build_blind_review_pipeline_subgraph,
    register_blind_review_pipeline,
)
from prismal.agents.subgraphs.blind_review_pipeline.implementer_agent import (
    ImplementerFn,
    make_implementer_agent_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.reviewer_node import (
    BlindnessGuard,
    ReviewerFn,
    make_reviewer_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.spec_agent import (
    SpecFn,
    make_spec_agent_node,
)
from prismal.agents.subgraphs.blind_review_pipeline.synthesis import (
    SynthesisResult,
    make_synthesis_node,
    synthesize_verdicts,
)

__all__ = [
    "BlindnessGuard",
    "ImplementerFn",
    "ReviewerFn",
    "SpecFn",
    "SynthesisResult",
    "build_blind_review_pipeline_subgraph",
    "make_implementer_agent_node",
    "make_reviewer_node",
    "make_spec_agent_node",
    "make_synthesis_node",
    "register_blind_review_pipeline",
    "synthesize_verdicts",
]
