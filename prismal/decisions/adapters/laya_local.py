"""In-process Laya adapter (Phase DEC — SPEC-DEC-ADP-002, DD-DEC-005).

``laya`` (extra ``[laya]``) is imported on first use only and inference runs in
``asyncio.to_thread`` so the event loop is never blocked. Laya's ``confidence``
is 1 - normalised entropy (DEC0-06) — stored as ``spread_confidence``; the gate
uses the calibrated max probability. The English ``laya`` checkpoint never
serves non-English states.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import DecisionBackendError
from prismal.core.logging import get_logger
from prismal.decisions.adapters._wire import parse_answers, wire_questions
from prismal.decisions.ports import guarded_decide
from prismal.decisions.types import DecisionKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)

_ENGLISH_ONLY = frozenset({"laya", "laya-typed-decisions"})


class LayaLocalDecisionAdapter:
    """``DecisionPort`` over ``laya.Router().predict`` in-process."""

    name = "laya"

    def __init__(
        self,
        *,
        checkpoint: str = "laya-multilingual",
        preload: bool = False,
        max_loaded: int = 2,
        max_len: int = 1024,
        batch_size: int = 64,
        max_choice_options: int = 20,
        cuda_amp: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.model = checkpoint
        self.preload = preload
        self._max_loaded = max(1, max_loaded)
        self._max_len = max_len
        self._batch_size = batch_size
        self._max_choice_options = max_choice_options
        self._cuda_amp = cuda_amp
        self._settings = settings
        self._loaded: OrderedDict[str, Any] = OrderedDict()

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        """False for non-English on English-only checkpoints or too many Choice options."""
        if language != "en" and self.model in _ENGLISH_ONLY:
            return False
        return not (
            question.kind is DecisionKind.CHOICE
            and len(question.options) > self._max_choice_options
        )

    def _router(self, checkpoint: str) -> Any:
        if checkpoint in self._loaded:
            self._loaded.move_to_end(checkpoint)
            return self._loaded[checkpoint]
        try:
            import laya
        except ImportError as exc:
            raise DecisionBackendError("laya is not installed (pip install prismal[laya])") from exc
        router = laya.Router()
        self._loaded[checkpoint] = router
        while len(self._loaded) > self._max_loaded:
            self._loaded.popitem(last=False)
        device = str(getattr(router, "device", "cpu"))
        if "cuda" in device and (self._cuda_amp or "").lower() != "fp16":
            # bf16 drifts up to ±0.073 vs fp32 (DEC0-08) — calibrate on the serving precision.
            logger.warning("laya_cuda_amp_not_fp16", amp=self._cuda_amp, checkpoint=checkpoint)
        return router

    async def warmup(self) -> None:
        """Force the deferred ``import laya`` + checkpoint load."""
        await asyncio.to_thread(self._router, self.model)

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> Mapping[str, Decision]:
        """Run the local Laya router in a worker thread and parse its answers."""
        return await guarded_decide(self, questions, policy, lambda qs: self._decide(state, qs))

    async def _decide(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Decision]:
        start = time.perf_counter()
        result = await asyncio.to_thread(self._predict, dict(state), wire_questions(questions))
        latency = (time.perf_counter() - start) * 1000.0
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            raise DecisionBackendError("laya returned no answers")
        return parse_answers(
            questions, answers, backend=self.name, model=self.model, latency_ms=latency
        )

    def _predict(self, state: dict[str, str], questions: dict[str, Any]) -> Any:
        router = self._router(self.model)
        return router.predict(state, questions, model=self.model, max_len=self._max_len)

    async def aclose(self) -> None:
        """Drop the loaded checkpoints."""
        self._loaded.clear()
