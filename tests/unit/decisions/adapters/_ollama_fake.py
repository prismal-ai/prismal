"""In-process fake Ollama server for the ``ollama`` decision adapter tests (DEC2-07…11).

Wire shapes follow the verified facts of the DEC Ollama delta (local Ollama 0.40.0):
``/api/version``, ``/api/show`` (``capabilities`` + whitespace-separated ``parameters``
text), ``/api/tags`` (``name`` + 64-hex ``digest``) and ``/v1/systemone`` (Jev wire,
``model`` echoes the request name without digest).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

FAST = "tev1:4b"
ACCURATE = "nimble:9b-q4_K_M"
FAST_DIGEST = "9b5bb969e46c" + "a" * 52
ACCURATE_DIGEST = "572f1f4c801d" + "b" * 52


@dataclass
class FakeModel:
    capabilities: list[str] = field(default_factory=lambda: ["decision"])
    num_ctx: int | None = 2050
    digest: str = FAST_DIGEST
    tagged: bool = True


class FakeOllama:
    """Routes by path; records every request; scripted failures per endpoint."""

    def __init__(
        self,
        *,
        version: str = "0.40.0",
        models: dict[str, FakeModel] | None = None,
        systemone_status: int = 200,
        systemone_delay_s: float = 0.0,
        served_model: str | None = None,
        raise_on: set[str] | None = None,
        version_delay_s: float = 0.0,
    ) -> None:
        self.version = version
        self.models = (
            models
            if models is not None
            else {
                FAST: FakeModel(num_ctx=2050, digest=FAST_DIGEST),
                ACCURATE: FakeModel(num_ctx=8194, digest=ACCURATE_DIGEST),
            }
        )
        self.systemone_status = systemone_status
        self.systemone_delay_s = systemone_delay_s
        self.served_model = served_model
        self.raise_on = raise_on if raise_on is not None else set()
        self.version_delay_s = version_delay_s
        self.requests: list[httpx.Request] = []

    # ── views ────────────────────────────────────────────────────────────────

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def systemone_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.calls("/v1/systemone")]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    # ── handler ──────────────────────────────────────────────────────────────

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.raise_on:
            raise httpx.ConnectError("connection refused", request=request)
        if path == "/api/version":
            if self.version_delay_s:
                await asyncio.sleep(self.version_delay_s)
            return httpx.Response(200, json={"version": self.version})
        if path == "/api/show":
            name = json.loads(request.content)["model"]
            model = self.models.get(name)
            if model is None:
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            params = "temperature                    0"
            if model.num_ctx is not None:
                params = f"num_ctx                        {model.num_ctx}\n{params}"
            return httpx.Response(
                200,
                json={
                    "capabilities": model.capabilities,
                    "parameters": params,
                    "model_info": {"qwen35.context_length": 262144},
                },
            )
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": n, "digest": m.digest} for n, m in self.models.items() if m.tagged
                    ]
                },
            )
        if path == "/v1/systemone":
            if self.systemone_delay_s:
                await asyncio.sleep(self.systemone_delay_s)
            body = json.loads(request.content)
            if self.systemone_status >= 400:
                return httpx.Response(
                    self.systemone_status,
                    json={"error": "prompt 0 has 4123 tokens; expected 1–2050"},
                )
            return httpx.Response(
                200,
                json={
                    "model": self.served_model or body["model"],
                    "answers": {qid: _answer(q) for qid, q in body["questions"].items()},
                    "usage": {"input_tokens": 120, "output_tokens": 3},
                },
            )
        return httpx.Response(404, json={"error": "not found"})


def _answer(q: dict[str, Any]) -> dict[str, Any]:
    if q["type"] == "noul":
        return {"type": "noul", "noul": 0.8}
    criteria = q["criteria"]
    keys = list(criteria) if isinstance(criteria, dict) else [str(i) for i in range(len(criteria))]
    rest = 0.1 / max(len(keys) - 1, 1)
    probs = {k: (0.9 if i == 0 else rest) for i, k in enumerate(keys)}
    return {"type": q["type"], "probabilities": probs, "confidence": 0.7}
