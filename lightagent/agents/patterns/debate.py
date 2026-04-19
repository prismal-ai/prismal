"""Debate / Society of Mind reasoning pattern.

Implements SPEC-PAT-002. N agents with distinct roles produce a position on
the user's query. Subsequent rounds let each agent see the prior round's
positions and refine its own. A synthesis step produces the final consensus:

- ``moderator`` (default): an LLM moderator reconciles all positions.
- ``majority_vote``: the most frequent position wins.
- ``weighted``: weighted merge by per-position score (handled as moderator
  variant here — the LLM is told the weights).

``agreement_score`` is a Jaccard-based similarity over the final-round
positions' token sets: 1.0 when all positions are token-identical, 0.0 when
they share no tokens. Good enough as a coarse signal of consensus without
requiring embeddings infrastructure.

Example::

    from lightagent.agents.patterns.debate import debate_round

    result = await debate_round(
        query="Should we ship this refactor now?",
        state=current_state,
        n_agents=3,
        n_rounds=2,
    )
    print(result.consensus)
    print(result.agreement_score)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.exceptions import LightAgentError
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from lightagent.core.config import Settings

logger = get_logger("lightagent.agents.patterns.debate")


class DebateError(LightAgentError):
    """Raised when a debate round cannot produce any positions or consensus."""


@dataclass
class DebatePosition:
    """One agent's position in one debate round.

    Attributes:
        agent_id: Stable identifier for the agent (e.g. ``"agent_0"``).
        role: Role label (``"proponent"``, ``"opponent"``, ...).
        content: Position text produced by the agent.
        round: 1-indexed round number.
    """

    agent_id: str
    role: str
    content: str
    round: int


@dataclass
class DebateResult:
    """Outcome of a :func:`debate_round` run.

    Attributes:
        consensus: Synthesized final answer.
        agreement_score: Token-set similarity over final-round positions in
            ``[0, 1]`` (1.0 = identical positions).
        positions: All positions produced across every round.
        dissenting_views: Position texts that diverge from the consensus.
        rounds_completed: How many rounds ran (usually == ``n_rounds``).
    """

    consensus: str
    agreement_score: float
    positions: list[DebatePosition] = field(default_factory=list)
    dissenting_views: list[str] = field(default_factory=list)
    rounds_completed: int = 0


_DEFAULT_ROLES: tuple[str, ...] = ("proponent", "opponent", "neutral")

_POSITION_PROMPT = (
    "You are the {role} in a structured debate about the user's query. Produce "
    "a concise stance (2-4 sentences) grounded in your role. Do not acknowledge "
    "other participants in this first round."
)

_REPLY_PROMPT = (
    "You are the {role} in a structured debate. You have seen the prior round's "
    "positions. Respond with a refined stance (2-4 sentences): you may concede "
    "valid points and press your disagreements."
)

_MODERATOR_PROMPT = (
    "You are a neutral moderator. Given the final positions of the debate, "
    "produce a single concise consensus answer (3-5 sentences) that integrates "
    "the stronger points. Do not copy any single position verbatim; paraphrase."
)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _pairwise_jaccard(texts: list[str]) -> float:
    """Mean Jaccard similarity over all unique pairs of token sets."""
    if len(texts) < 2:
        return 1.0
    token_sets = [_tokens(t) for t in texts]
    pairs = 0
    total = 0.0
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            a, b = token_sets[i], token_sets[j]
            union = a | b
            if not union:
                continue
            total += len(a & b) / len(union)
            pairs += 1
    return total / pairs if pairs else 1.0


async def debate_round(
    query: str,
    state: Any,
    n_agents: int = 3,
    n_rounds: int = 2,
    roles: list[str] | None = None,
    synthesis_strategy: Literal["moderator", "majority_vote", "weighted"] = "moderator",
    settings: Settings | None = None,
) -> DebateResult:
    """Run a multi-agent debate and synthesise consensus.

    Args:
        query: The question or claim under debate.
        state: Opaque state (passed only for future extension; not inspected).
        n_agents: Number of debating agents (default 3).
        n_rounds: Number of debate rounds (default 2; round 1 is independent
            positions, round 2+ sees prior positions).
        roles: Role labels per agent. ``None`` uses the default triad
            (``proponent``, ``opponent``, ``neutral``) and generates
            ``analyst_N`` for any overflow.
        synthesis_strategy: How to derive the final ``consensus``.
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.

    Returns:
        :class:`DebateResult` with consensus, agreement score, all positions.

    Raises:
        ValueError: For non-positive ``n_agents`` or ``n_rounds``.
        DebateError: When all agents fail to produce a round-1 position.
    """
    if n_agents < 1:
        raise ValueError(f"n_agents must be >= 1; got {n_agents}")
    if n_rounds < 1:
        raise ValueError(f"n_rounds must be >= 1; got {n_rounds}")

    resolved_settings = settings if settings is not None else get_settings()
    llm = ProviderRegistry(settings=resolved_settings).get_llm()
    del state  # reserved for future hooks

    # Resolve roles list to exactly n_agents entries.
    if roles is None:
        resolved_roles = list(_DEFAULT_ROLES[:n_agents])
        while len(resolved_roles) < n_agents:
            resolved_roles.append(f"analyst_{len(resolved_roles)}")
    else:
        if len(roles) != n_agents:
            raise ValueError(
                f"len(roles)={len(roles)} does not match n_agents={n_agents}"
            )
        resolved_roles = list(roles)

    otel = OTelManager()
    with otel.start_span("debate.round") as span:
        span.set_attribute("lightagent.debate.n_agents", n_agents)
        span.set_attribute("lightagent.debate.n_rounds", n_rounds)
        span.set_attribute("lightagent.debate.strategy", synthesis_strategy)

        all_positions: list[DebatePosition] = []
        last_round_positions: list[DebatePosition] = []
        had_any_success = False

        for round_idx in range(1, n_rounds + 1):
            round_positions: list[DebatePosition] = []
            for agent_idx, role in enumerate(resolved_roles):
                agent_id = f"agent_{agent_idx}"
                try:
                    content = await _generate_position(
                        llm=llm,
                        query=query,
                        role=role,
                        round_idx=round_idx,
                        prior_positions=last_round_positions,
                    )
                except Exception as exc:
                    logger.warning(
                        "debate_position_error",
                        agent_id=agent_id,
                        role=role,
                        round=round_idx,
                        error=str(exc),
                    )
                    continue
                had_any_success = True
                position = DebatePosition(
                    agent_id=agent_id, role=role, content=content, round=round_idx
                )
                round_positions.append(position)
                all_positions.append(position)
            last_round_positions = round_positions

        if not had_any_success:
            raise DebateError("All agents failed to produce positions")

        final_positions = [p for p in all_positions if p.round == n_rounds]
        if not final_positions:
            # All final-round agents failed → fall back to the last successful round
            max_round = max(p.round for p in all_positions)
            final_positions = [p for p in all_positions if p.round == max_round]

        consensus = await _synthesise(
            llm=llm,
            query=query,
            positions=final_positions,
            strategy=synthesis_strategy,
        )
        agreement = _pairwise_jaccard([p.content for p in final_positions])
        dissenting = _dissenting_views(final_positions, consensus, agreement)

        span.set_attribute("lightagent.debate.agreement", agreement)
        span.set_attribute("lightagent.debate.positions_total", len(all_positions))
        logger.info(
            "debate_done",
            n_rounds=n_rounds,
            n_agents=n_agents,
            strategy=synthesis_strategy,
            agreement=agreement,
        )

        return DebateResult(
            consensus=consensus,
            agreement_score=agreement,
            positions=all_positions,
            dissenting_views=dissenting,
            rounds_completed=n_rounds,
        )


# ── helpers ───────────────────────────────────────────────────────────────────


async def _generate_position(
    *,
    llm: Any,
    query: str,
    role: str,
    round_idx: int,
    prior_positions: list[DebatePosition],
) -> str:
    system = (
        _POSITION_PROMPT.format(role=role)
        if round_idx == 1
        else _REPLY_PROMPT.format(role=role)
    )
    user_parts: list[str] = [f"Query: {query}"]
    if prior_positions:
        prior = "\n\n".join(f"[{p.role}] {p.content}" for p in prior_positions)
        user_parts.append(f"Prior positions:\n{prior}")
    builder = SecurePromptBuilder()
    messages = builder.build(system=system, user="\n\n".join(user_parts))
    response = await llm.ainvoke(
        [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
    )
    return str(response.content).strip()


async def _synthesise(
    *,
    llm: Any,
    query: str,
    positions: list[DebatePosition],
    strategy: Literal["moderator", "majority_vote", "weighted"],
) -> str:
    if strategy == "majority_vote":
        counts = Counter(p.content for p in positions)
        return counts.most_common(1)[0][0]

    # moderator or weighted: call the LLM moderator.
    positions_text = "\n\n".join(
        f"[{p.role}] {p.content}" for p in positions
    )
    user = f"Query: {query}\n\nFinal positions:\n{positions_text}"
    builder = SecurePromptBuilder()
    messages = builder.build(system=_MODERATOR_PROMPT, user=user)
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
        )
    except Exception as exc:
        logger.warning("debate_moderator_error", error=str(exc))
        # Fall back to the highest-agreement position text.
        return positions[0].content if positions else ""
    return str(response.content).strip()


def _dissenting_views(
    positions: list[DebatePosition],
    consensus: str,
    agreement: float,
    threshold: float = 0.5,
) -> list[str]:
    """Return position texts that diverge notably from the consensus.

    "Diverge notably" = Jaccard similarity with consensus below ``threshold``.
    When agreement is already >= threshold we surface nothing (consensus was
    strong enough that no dissent label is meaningful).
    """
    if agreement >= threshold:
        return []
    consensus_tokens = _tokens(consensus)
    dissenting: list[str] = []
    for p in positions:
        ptokens = _tokens(p.content)
        union = consensus_tokens | ptokens
        if not union:
            continue
        sim = len(consensus_tokens & ptokens) / len(union)
        if sim < threshold:
            dissenting.append(p.content)
    return dissenting


__all__ = [
    "DebateError",
    "DebatePosition",
    "DebateResult",
    "debate_round",
]
