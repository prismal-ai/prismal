"""Builder for the kokoro subgraph (SPEC-KOK-SG-001).

Linear pipeline::

    load_souls → deliberate → judge → act → output

``load_souls`` resolves the triad via :class:`SoulsManager` (fail-fast before
any LLM call); ``deliberate`` runs the bounded multi-soul rounds; ``judge``
renders the :class:`Verdict`; ``act`` executes one gated action when
``kokoro_execute_actions`` is enabled (otherwise a pass-through); ``output``
appends the final assistant message.  All runtime state lives under
``state["metadata"]["kokoro"]`` (RF-KOK-12).

Every backend is callable-injected (DD-KOK-004) so the subgraph runs
end-to-end with fakes and no provider import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.agents.kokoro.judge import KokoroJudgeAgent
from prismal.agents.subgraphs.kokoro.act_node import make_act_node
from prismal.agents.subgraphs.kokoro.deliberate_node import make_deliberate_node
from prismal.agents.subgraphs.kokoro.judge_node import make_judge_node
from prismal.agents.subgraphs.kokoro.load_souls_node import make_load_souls_node
from prismal.agents.subgraphs.kokoro.output_node import output_node
from prismal.agents.subgraphs.registry import SubgraphDefinition
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.agents.kokoro.deliberation import AgreementFn
    from prismal.agents.kokoro.judge import JudgeFn, ToolExecutor
    from prismal.agents.kokoro.soul_agent import PersonaGenerateFn
    from prismal.agents.subgraphs.registry import SubgraphRegistry
    from prismal.core.config import Settings
    from prismal.souls.manager import SoulsManager

logger = get_logger("prismal.subgraphs.kokoro.builder")

_NAME = "kokoro"
_DESCRIPTION = (
    "Kokoro deliberation: load_souls → deliberate (spirit|mind|heart) → judge → act → output"
)


def build_kokoro_subgraph(
    settings: Settings | None = None,
    *,
    souls_manager: SoulsManager | None = None,
    soul_ids: list[str] | None = None,
    generate_fn: PersonaGenerateFn | None = None,
    agreement_fn: AgreementFn | None = None,
    judge_agent: KokoroJudgeAgent | None = None,
    judge_fn: JudgeFn | None = None,
    tool_executor: ToolExecutor | None = None,
) -> SubgraphDefinition:
    """Build the kokoro :class:`SubgraphDefinition` (nodes/edges/entry_point).

    Args:
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.
        souls_manager: Injected souls manager; ``None`` builds the default.
        soul_ids: Triad override; ``None`` uses ``settings.kokoro_souls``.
        generate_fn: Injected persona backend shared by the three souls.
        agreement_fn: Injected agreement metric (default ``pairwise_jaccard``).
        judge_agent: Fully-built judge.  Takes precedence over ``judge_fn`` /
            ``tool_executor``, which are convenience shortcuts for building
            the default judge.
        judge_fn: Injected judge backend (used when *judge_agent* is None).
        tool_executor: Injected action backend (used when *judge_agent* is None).

    Returns:
        :class:`SubgraphDefinition` with 5 nodes and linear edges.
    """
    if souls_manager is None:
        from prismal.souls.manager import SoulsManager as _SoulsManager

        souls_manager = _SoulsManager(settings=settings)
    if judge_agent is None:
        judge_agent = KokoroJudgeAgent(
            judge_fn=judge_fn,
            tool_executor=tool_executor,
            settings=settings,
        )

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="load_souls",
        nodes={
            "load_souls": make_load_souls_node(souls_manager, soul_ids),
            "deliberate": make_deliberate_node(
                generate_fn=generate_fn,
                agreement_fn=agreement_fn,
                settings=settings,
            ),
            "judge": make_judge_node(judge_agent),
            "act": make_act_node(judge_agent),
            "output": output_node,
        },
        edges=[
            ("load_souls", "deliberate"),
            ("deliberate", "judge"),
            ("judge", "act"),
            ("act", "output"),
        ],
    )
    logger.info("kokoro_subgraph_built", nodes=list(definition.nodes.keys()))
    return definition


async def register_kokoro(
    registry: SubgraphRegistry | None = None,
    *,
    settings: Settings | None = None,
) -> None:
    """Idempotently register the kokoro subgraph (mirrors ``register_debate_consensus``)."""
    from prismal.agents.subgraphs.registry import SubgraphRegistry as _SubgraphRegistry

    registry = registry or _SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("kokoro.already_registered")
        return
    definition = build_kokoro_subgraph(settings=settings)
    await registry.register(_NAME, definition)
    logger.info("kokoro.registered")


__all__ = ["build_kokoro_subgraph", "register_kokoro"]
