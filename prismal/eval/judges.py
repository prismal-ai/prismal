"""LLM-as-judge for rubric scoring and groundedness (SPEC-EVL-JDG-001).

The judge isolates the (untrusted) answer/context through ``SecurePromptBuilder``
(Critical Rule #1) before scoring. The default ``judge_fn`` wires the
model-agnostic ``ProviderRegistry().get_llm()`` and parses a ``[0, 1]`` score
from the reply; in tests a deterministic ``judge_fn`` is injected so no LLM is
called. Default runs are gated behind ``live_api`` by the caller.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.core.config import Settings

_JUDGE_SYSTEM = (
    "You are a strict evaluation judge. Read the rubric and the answer (and any "
    "retrieved context) and reply with a single number in [0.0, 1.0] scoring how "
    "well the answer satisfies the rubric. Reply with only the number."
)

_SCORE_RE = re.compile(r"-?\d+(?:\.\d+)?")


class Judge:
    """Score an answer against a rubric, optionally grounded in context."""

    def __init__(
        self,
        *,
        judge_fn: Callable[[str], Awaitable[float]] | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialise the judge.

        Args:
            judge_fn: Async ``prompt -> score`` function. Defaults to the live
                LLM path (model-agnostic via ``ProviderRegistry``).
            settings: Optional settings (judge model override).
        """
        self._judge_fn = judge_fn
        self._settings = settings

    async def score(self, *, rubric: str, answer: str, context: str = "") -> float:
        """Return a ``[0, 1]`` score for *answer* under *rubric* and *context*."""
        prompt = self._build_prompt(rubric=rubric, answer=answer, context=context)
        fn = self._judge_fn or self._default_judge_fn
        raw = await fn(prompt)
        return _clamp01(float(raw))

    def _build_prompt(self, *, rubric: str, answer: str, context: str) -> str:
        """Render a single judge prompt with the answer/context isolated."""
        builder = SecurePromptBuilder()
        system = f"{_JUDGE_SYSTEM}\n\nRubric:\n{rubric}"
        docs = [context] if context else None
        messages = builder.build(system, answer, docs)
        return "\n\n".join(m["content"] for m in messages)

    async def _default_judge_fn(self, prompt: str) -> float:
        """Live path: call the model-agnostic LLM and parse a numeric score."""
        from prismal.providers.registry import ProviderRegistry

        model = self._settings.eval_judge_model if self._settings else ""
        llm = ProviderRegistry().get_llm(model or None, temperature=0.0)
        reply = await llm.ainvoke(prompt)
        return _parse_score(_text(getattr(reply, "content", reply)))


def _parse_score(text: str) -> float:
    """Extract the first number from *text* and clamp to ``[0, 1]`` (0.0 if none)."""
    match = _SCORE_RE.search(text)
    if not match:
        return 0.0
    try:
        return _clamp01(float(match.group()))
    except ValueError:
        return 0.0


def _clamp01(value: float) -> float:
    """Clamp *value* into ``[0.0, 1.0]``."""
    return max(0.0, min(1.0, value))


def _text(content: Any) -> str:
    """Coerce LLM reply content to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


__all__ = ["Judge"]
