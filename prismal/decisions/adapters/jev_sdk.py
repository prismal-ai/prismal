"""Jev via ``typesafe-sdk`` (Phase DEC — SPEC-DEC-ADP-003; extra ``[jev]``).

All SDK access is isolated behind :meth:`JevSdkDecisionAdapter._call` (DEC0-02:
``AsyncTypeSafeClient(api_key=, model=, base_url=).system_one(state, questions,
model=)``; the SDK base URL is the host only and it appends ``/v1/systemone``).
The import is deferred to first use.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import DecisionBackendError
from prismal.core.logging import get_logger
from prismal.decisions.adapters._wire import count_model_mismatch, parse_payload, wire_questions
from prismal.decisions.ports import abstain_all, guarded_decide
from prismal.decisions.types import DecisionKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import SecretStr

    from prismal.core.config import Settings
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)


class JevSdkDecisionAdapter:
    """``DecisionPort`` over the official async SDK."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str | None = None,
        name: str = "jev",
        max_choice_options: int = 255,
        settings: Settings | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._api_key = api_key
        self._base_url = _sdk_base(base_url)
        self._max_choice_options = max_choice_options
        self._settings = settings
        self._client: Any = None

    def __repr__(self) -> str:
        return f"JevSdkDecisionAdapter(name={self.name!r}, model={self.model!r})"

    def supports(self, question: QuestionSpec, *, language: str) -> bool:  # noqa: ARG002
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
    ) -> Mapping[str, Decision]:
        return await guarded_decide(self, questions, policy, lambda qs: self._decide(state, qs))

    async def _decide(
        self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec]
    ) -> dict[str, Decision]:
        start = time.perf_counter()
        payload = await self._call(dict(state), wire_questions(questions))
        latency = (time.perf_counter() - start) * 1000.0
        parsed = parse_payload(
            payload, questions, backend=self.name, model=self.model, latency_ms=latency
        )
        if parsed is None:
            count_model_mismatch(self.name)
            return abstain_all(
                questions,
                backend=self.name,
                model=self.model,
                reason="model_mismatch",
                latency_ms=latency,
            )
        return parsed

    async def _call(self, state: dict[str, str], questions: dict[str, Any]) -> Any:
        """The single SDK seam (names verified in DEC0-02)."""
        if self._client is None:
            try:
                from typesafe_sdk import AsyncTypeSafeClient
            except ImportError as exc:
                raise DecisionBackendError(
                    "typesafe-sdk is not installed (pip install prismal[jev])"
                ) from exc
            kwargs: dict[str, Any] = {
                "api_key": self._api_key.get_secret_value(),
                "model": self.model,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncTypeSafeClient(**kwargs)
        response = await self._client.system_one(state, questions, model=self.model)
        return response.model_dump() if hasattr(response, "model_dump") else response

    async def aclose(self) -> None:
        client, self._client = self._client, None
        closer = getattr(client, "aclose", None) or getattr(client, "close", None)
        if closer is not None:
            try:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("jev_sdk_close_failed", error_type=type(exc).__name__)


def _sdk_base(base_url: str | None) -> str | None:
    if not base_url:
        return None
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base
