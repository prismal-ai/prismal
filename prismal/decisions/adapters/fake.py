"""Deterministic, I/O-free decision backend for tests (Phase DEC — SPEC-DEC-ADP-005)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from prismal.core.exceptions import DecisionBackendError
from prismal.decisions.ports import guarded_decide
from prismal.decisions.types import Decision, DecisionKind

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from prismal.decisions.types import GatePolicy, QuestionSpec

    Scripted = Decision | Callable[[Mapping[str, str]], Decision]


class FakeDecisionAdapter:
    """Returns scripted decisions; unscripted ids abstain with ``reason="unsupported"``."""

    def __init__(
        self,
        scripted: Mapping[str, Scripted],
        *,
        name: str = "fake",
        model: str = "fake-1",
        fail: bool = False,
        delay_s: float = 0.0,
        max_choice_options: int = 255,
        languages: Collection[str] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._scripted = dict(scripted)
        self._fail = fail
        self._delay_s = delay_s
        self._max_choice_options = max_choice_options
        self._languages = frozenset(languages) if languages is not None else None
        self.calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
        self.closed = False

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        if self._languages is not None and language not in self._languages:
            return False
        return not (
            question.kind is DecisionKind.CHOICE
            and len(question.options) > self._max_choice_options
        )

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
        language: str = "en",
    ) -> Mapping[str, Decision]:
        return await guarded_decide(
            self, questions, policy, lambda qs: self._answer(state, qs), language=language
        )

    async def _answer(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Decision]:
        self.calls.append((dict(state), tuple(questions)))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._fail:
            raise DecisionBackendError("scripted failure")
        out: dict[str, Decision] = {}
        for qid, q in questions.items():
            entry = self._scripted.get(qid)
            if entry is None:
                out[qid] = Decision.abstain(
                    q, backend=self.name, model=self.model, reason="unsupported"
                )
                continue
            d = entry if isinstance(entry, Decision) else entry(state)
            out[qid] = replace(d, backend=self.name, model=self.model) if not d.abstained else d
        return out

    async def aclose(self) -> None:
        self.closed = True
