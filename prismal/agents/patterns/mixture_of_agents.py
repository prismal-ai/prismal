"""Mixture of Agents — parallel proposers + aggregator synthesis.

Implements SPEC-PAT-006. A pool of *proposer* models each answers the query
independently and in parallel (different providers/models produce
complementary perspectives). An *aggregator* model then synthesises a single
final answer from those proposals. Optionally, the aggregation step can be
repeated (``n_aggregator_layers > 1``) to refine further.

Partial-failure tolerance: if some proposers crash or time out, the
remaining proposals still feed the aggregator. Only when **every** proposer
fails does :class:`MoAError` bubble up.

Example::

    moa = MixtureOfAgents(
        proposer_models=["gpt-4o", "claude-sonnet-4-6", "gemini-1.5-pro"],
        aggregator_model="claude-opus-4-6",
    )
    result = await moa.generate(query="Summarise this paper…", state=state)
    print(result.final_answer)
    print(result.providers_used)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.config import get_settings
from prismal.core.exceptions import MoAError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("lightagent.agents.patterns.mixture_of_agents")

_PROPOSER_PROMPT = (
    "You are one of several independent experts answering the user's query. "
    "Respond directly and concisely with your best answer. Do not hedge about "
    "other experts' views — another layer will synthesise later."
)

_AGGREGATOR_PROMPT = (
    "You are an aggregator. Several experts have answered the user's query. "
    "Produce one cohesive final answer that integrates the stronger points "
    "from each expert response. Paraphrase — do not copy any single response "
    "verbatim."
)


@dataclass
class MoAResult:
    """Outcome of :meth:`MixtureOfAgents.generate`.

    Attributes:
        final_answer: The aggregator's last pass output.
        layer_outputs: Per-layer list of outputs. ``layer_outputs[0]`` is the
            proposer outputs (order matches ``providers_used``); each
            subsequent entry is one aggregator pass (single-element list).
        providers_used: Proposer model ids whose calls succeeded. Failed
            proposers are omitted.
    """

    final_answer: str
    layer_outputs: list[list[str]] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)


class MixtureOfAgents:
    """Parallel proposers + aggregator synthesis.

    Args:
        proposer_models: Ordered list of model ids; each produces one
            proposal in parallel. Must be non-empty.
        aggregator_model: Model id for the synthesis step. ``None`` reuses
            ``proposer_models[0]`` as a sensible default.
        n_aggregator_layers: Number of aggregator passes (each refines the
            previous). Must be ≥ 1.
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        proposer_models: list[str],
        aggregator_model: str | None = None,
        n_aggregator_layers: int = 1,
        settings: Settings | None = None,
    ) -> None:
        if not proposer_models:
            raise ValueError("proposer_models must be non-empty")
        if n_aggregator_layers < 1:
            raise ValueError(f"n_aggregator_layers must be >= 1; got {n_aggregator_layers}")
        self._proposer_models = list(proposer_models)
        self._aggregator_model = aggregator_model or proposer_models[0]
        self._n_aggregator_layers = n_aggregator_layers
        self._settings = settings if settings is not None else get_settings()

    async def generate(self, query: str, state: Any) -> MoAResult:
        """Run proposers in parallel, then ``n_aggregator_layers`` refinements."""
        del state  # reserved for future hooks
        otel = OTelManager()
        with otel.start_span("moa.generate") as span:
            span.set_attribute("lightagent.moa.query_len", len(query))
            span.set_attribute("lightagent.moa.n_proposers", len(self._proposer_models))
            span.set_attribute("lightagent.moa.n_aggregator_layers", self._n_aggregator_layers)

            registry = ProviderRegistry(settings=self._settings)
            proposer_outputs, providers_used = await self._run_proposers(registry, query)
            if not proposer_outputs:
                raise MoAError("All proposer models failed to produce an output")

            layer_outputs: list[list[str]] = [list(proposer_outputs)]
            aggregator_llm = registry.get_llm(self._aggregator_model)
            current = list(proposer_outputs)
            for layer_idx in range(self._n_aggregator_layers):
                synth = await self._aggregate(aggregator_llm, query, current)
                layer_outputs.append([synth])
                current = [synth]
                logger.info(
                    "moa_aggregator_pass_done",
                    layer=layer_idx + 1,
                    output_len=len(synth),
                )

            final_answer = current[0]
            span.set_attribute("lightagent.moa.answer_len", len(final_answer))
            logger.info(
                "moa_generate_done",
                n_proposers_success=len(providers_used),
                n_proposers_total=len(self._proposer_models),
                layers=self._n_aggregator_layers,
            )
            return MoAResult(
                final_answer=final_answer,
                layer_outputs=layer_outputs,
                providers_used=providers_used,
            )

    async def _run_proposers(
        self, registry: ProviderRegistry, query: str
    ) -> tuple[list[str], list[str]]:
        """Invoke each proposer in parallel; drop failing ones.

        Returns ``(outputs, model_ids)`` — positions match, both filtered to
        successful calls only.
        """

        async def _propose_one(model: str) -> str:
            llm = registry.get_llm(model)
            return await self._propose(llm, query)

        results = await asyncio.gather(
            *(_propose_one(m) for m in self._proposer_models),
            return_exceptions=True,
        )
        outputs: list[str] = []
        providers: list[str] = []
        for model, result in zip(self._proposer_models, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("moa_proposer_failed", model=model, error=str(result))
                continue
            outputs.append(result)
            providers.append(model)
        return outputs, providers

    async def _propose(self, llm: Any, query: str) -> str:
        builder = SecurePromptBuilder()
        messages = builder.build(system=_PROPOSER_PROMPT, user=query)
        response = await llm.ainvoke(
            [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
        )
        return str(response.content).strip()

    async def _aggregate(self, llm: Any, query: str, proposals: list[str]) -> str:
        proposals_text = "\n\n".join(f"[Expert {i + 1}] {p}" for i, p in enumerate(proposals))
        user = f"Query: {query}\n\nExpert responses:\n{proposals_text}"
        builder = SecurePromptBuilder()
        messages = builder.build(system=_AGGREGATOR_PROMPT, user=user)
        response = await llm.ainvoke(
            [
                SystemMessage(content=messages[0]["content"]),
                HumanMessage(content=messages[1]["content"]),
            ]
        )
        return str(response.content).strip()


__all__ = ["MixtureOfAgents", "MoAError", "MoAResult"]
