"""Multimodal fusion (Fase F, SPEC-MM-AGT-005).

Combines per-modality agent outputs into a single answer using one of three
strategies: ``concat`` (deterministic), ``moderator`` (one LLM synthesises) or
``moa`` (delegates to :class:`MixtureOfAgents` for proposer+aggregator synthesis).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prismal.core.exceptions import MultimodalFusionError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.multimodal.modality_router import Modality
    from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.multimodal.multimodal_fusion")

_VALID_STRATEGIES = ("moa", "moderator", "concat")
_FusionStrategy = Literal["moa", "moderator", "concat"]


@dataclass(frozen=True)
class ModalContribution:
    """A single modal agent's contribution to the fused answer."""

    modality: Modality
    content: str
    agent_id: str
    confidence: float


@dataclass(frozen=True)
class FusionResult:
    """Outcome of fusing modal contributions."""

    answer: str
    contributions: list[ModalContribution]
    strategy_used: _FusionStrategy


def _render_contributions(contributions: list[ModalContribution]) -> str:
    """Render contributions as labelled sections for an LLM/concat output."""
    return "\n\n".join(
        f"[{c.modality.value} · {c.agent_id} · conf={c.confidence:.2f}]\n{c.content}"
        for c in contributions
    )


class MultimodalFusion:
    """Fuses modal contributions into one answer.

    Args:
        strategy: ``"moa"``, ``"moderator"`` or ``"concat"``.
        moa: A :class:`MixtureOfAgents` for ``strategy="moa"``; built lazily if None.
        moderator_fn: ``async (prompt) -> str`` for ``strategy="moderator"``;
            defaults to a multimodal LLM call.
        settings: Injectable settings.
    """

    def __init__(
        self,
        *,
        strategy: _FusionStrategy = "moderator",
        moa: MixtureOfAgents | None = None,
        moderator_fn: Callable[[str], Awaitable[str]] | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Validate the strategy and store collaborators."""
        if strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"unknown fusion strategy: {strategy!r} (expected {_VALID_STRATEGIES})"
            )
        self._strategy: _FusionStrategy = strategy
        self._moa = moa
        self._moderator_fn = moderator_fn
        self._settings = settings

    async def combine(
        self,
        contributions: list[ModalContribution],
        *,
        context: str | None = None,
    ) -> FusionResult:
        """Combine contributions into a single :class:`FusionResult`."""
        otel = OTelManager()
        with otel.start_span(
            "mm.fusion.combine", attributes={"prismal.fusion.strategy": self._strategy}
        ):
            if self._strategy == "concat":
                answer = _render_contributions(contributions)
            elif self._strategy == "moderator":
                answer = await self._combine_moderator(contributions, context)
            else:
                answer = await self._combine_moa(contributions, context)
        logger.info("fusion_combined", strategy=self._strategy, n=len(contributions))
        return FusionResult(
            answer=answer, contributions=contributions, strategy_used=self._strategy
        )

    def _build_prompt(self, contributions: list[ModalContribution], context: str | None) -> str:
        """Build the synthesis prompt shared by moderator/moa strategies."""
        rendered = _render_contributions(contributions)
        parts = ["Synthesise a single coherent answer from these modal observations:", rendered]
        if context:
            parts.append(f"Additional context: {context}")
        return "\n\n".join(parts)

    async def _combine_moderator(
        self, contributions: list[ModalContribution], context: str | None
    ) -> str:
        prompt = self._build_prompt(contributions, context)
        if self._moderator_fn is not None:
            return await self._moderator_fn(prompt)
        return await self._default_moderator(prompt)

    async def _default_moderator(self, prompt: str) -> str:
        """Synthesise via a multimodal LLM through SecurePromptBuilder."""
        from langchain_core.messages import HumanMessage, SystemMessage

        from prismal.providers.multimodal import get_multimodal_llm
        from prismal.security.prompt_builder import SecurePromptBuilder

        llm = get_multimodal_llm(settings=self._settings)
        builder = SecurePromptBuilder()
        messages = builder.build(
            system="You are a moderator fusing multimodal observations into one answer.",
            user=prompt,
        )
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=messages[0]["content"]),
                    HumanMessage(content=messages[1]["content"]),
                ]
            )
        except Exception as exc:
            raise MultimodalFusionError(f"moderator fusion failed: {exc!r}") from exc
        return str(response.content).strip()

    async def _combine_moa(
        self, contributions: list[ModalContribution], context: str | None
    ) -> str:
        prompt = self._build_prompt(contributions, context)
        moa = self._moa or self._default_moa()
        try:
            result = await moa.generate(prompt, {})
        except Exception as exc:
            raise MultimodalFusionError(f"moa fusion failed: {exc!r}") from exc
        return str(result.final_answer).strip()

    def _default_moa(self) -> MixtureOfAgents:
        """Build a default MixtureOfAgents from settings."""
        from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
        from prismal.core.config import get_settings

        settings = self._settings or get_settings()
        return MixtureOfAgents(proposer_models=[settings.multimodal_model], settings=settings)


__all__ = [
    "FusionResult",
    "ModalContribution",
    "MultimodalFusion",
]
