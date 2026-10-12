"""Provider batch-API adapters (Phase AMR — SPEC-AMR-BEX-001, AMR5-01..03).

``BatchApiPort`` is the seam :class:`prismal.routing.batch.BatchExecutor` talks
to; provider specifics stay here (Critical Rule #4). Verified facts (AMR0-03/04,
2026-10-06):

- **Anthropic Message Batches** — LiteLLM 1.86.2 cannot create batches nor fetch
  results, so the adapter uses raw ``httpx``: ``POST /v1/messages/batches``
  (inline ``requests``), ``GET …/{id}`` (``processing_status``), results JSONL
  at ``results_url``, ``POST …/{id}/cancel``.
- **OpenAI Batch** — LiteLLM supports the full path (``acreate_file`` purpose
  ``batch`` → ``acreate_batch`` → ``aretrieve_batch`` → ``afile_content``); the
  ``litellm`` module is injected as a test seam.
- **OpenRouter ``:batch``** — a true async batch API (``POST /api/v1/batches``,
  ``GET``/``DELETE /api/v1/batches/{id}``). Exact field names are best-effort,
  so request/response handling is deliberately defensive: results are read
  from an inline ``results`` list, a ``results_url`` JSONL or an
  ``output_file_id`` file, and each line may be Anthropic- or OpenAI-shaped.
  A model without a ``:batch`` endpoint answers 400 → :class:`BatchExecutorError`
  → the executor falls back to a synchronous call.

Every transport/parse failure raises :class:`BatchExecutorError` (message never
contains the key). API keys are held as ``SecretStr`` and unwrapped only when a
request header (or the LiteLLM call) is built; ``repr`` never shows them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import httpx

from prismal.budget.types import Usage
from prismal.core.exceptions import BatchExecutorError

if TYPE_CHECKING:
    from pydantic import SecretStr

    from prismal.core.config import Settings

__all__ = [
    "AnthropicBatchAdapter",
    "BatchApiPort",
    "BatchRequest",
    "BatchResult",
    "BatchStatus",
    "KeyResolver",
    "OpenAIBatchAdapter",
    "OpenRouterBatchAdapter",
    "default_adapters",
    "family_of",
    "provider_model",
]

_TIMEOUT_S = 60.0

# GW invariant (``gateways/openrouter.py::build_provider_object``): OpenRouter
# traffic always opts out of data collection and requires zero-data-retention.
_OPENROUTER_PRIVACY: Mapping[str, object] = {"data_collection": "deny", "zdr": True}

KeyResolver = Callable[[str], "SecretStr | None"]


class BatchStatus(StrEnum):
    """Lifecycle of a provider batch job (SPEC-AMR-BEX-001)."""

    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class BatchRequest:
    """One chat completion to run inside a provider batch."""

    custom_id: str
    model_ref: str
    messages: Sequence[Mapping[str, object]]
    max_tokens: int
    response_format: Mapping[str, object] | None = None


@dataclass(frozen=True)
class BatchResult:
    """The outcome of one :class:`BatchRequest` (``error`` set ⇒ no content)."""

    custom_id: str
    content: str | None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None


@runtime_checkable
class BatchApiPort(Protocol):
    """A provider batch API (one family per adapter)."""

    family: str  # "anthropic" | "openai" | "openrouter"

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        """Submit *requests* as one batch job; returns the provider job id."""
        ...

    async def poll(self, job_id: str) -> BatchStatus:
        """Current normalized :class:`BatchStatus` of *job_id*."""
        ...

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        """Per-request results of a completed job."""
        ...

    async def cancel(self, job_id: str) -> None:
        """Cancel *job_id* (best effort)."""
        ...


# ── model-ref helpers ─────────────────────────────────────────────────────────


def family_of(model_ref: str) -> str | None:
    """Return the batch family of *model_ref*, or None when it is not batchable.

    ``anthropic/…`` / ``claude*`` ⇒ anthropic; ``openai/…`` / ``gpt*`` / ``o<digit>…``
    ⇒ openai; ``openrouter/…`` or a ``gw:openrouter/…:batch`` variant ⇒ openrouter.
    A gateway ref without the ``:batch`` variant is not batchable.
    """
    ref = model_ref.strip()
    if ref.startswith("gw:openrouter/"):
        return "openrouter" if ref.endswith(":batch") else None
    if ref.startswith("openrouter/"):
        return "openrouter"
    if ref.startswith(("anthropic/", "claude")):
        return "anthropic"
    if ref.startswith(("openai/", "gpt")):
        return "openai"
    if len(ref) > 1 and ref[0] == "o" and ref[1].isdigit():
        return "openai"
    return None


def provider_model(model_ref: str) -> str:
    """Strip the routing prefix (``anthropic/``, ``openai/``, ``[gw:]openrouter/``)."""
    for prefix in ("gw:openrouter/", "openrouter/", "anthropic/", "openai/"):
        if model_ref.startswith(prefix):
            return model_ref[len(prefix) :]
    return model_ref


# ── shared parsing ────────────────────────────────────────────────────────────


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _mapping(value: object) -> Mapping[str, Any] | None:
    """*value* as a JSON-object mapping (string keys), or None when it is not one."""
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _get(obj: object, key: str) -> Any:
    mapping = _mapping(obj)
    if mapping is not None:
        return mapping.get(key)
    return getattr(obj, key, None)


def _text_of(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in map(_mapping, content)
            if block is not None and block.get("type", "text") == "text"
        ]
        return "".join(parts)
    return None


def _usage(prompt: int, completion: int) -> Usage:
    return Usage(prompt_tokens=prompt, completion_tokens=completion, calls=1)


def _parse_line(item: object) -> BatchResult | None:
    """Parse one result line (Anthropic- or OpenAI-shaped); None if unusable."""
    line = _mapping(item)
    if line is None:
        return None
    cid = line.get("custom_id")
    if not isinstance(cid, str):
        return None
    result = _mapping(line.get("result"))
    if result is not None:  # Anthropic shape
        kind = result.get("type")
        if kind != "succeeded":
            return BatchResult(custom_id=cid, content=None, error=str(kind or "unknown"))
        message = _mapping(result.get("message")) or {}
        usage = _mapping(message.get("usage")) or {}
        return BatchResult(
            custom_id=cid,
            content=_text_of(message.get("content")),
            usage=_usage(_as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))),
        )
    error = line.get("error")
    if error:
        error_map = _mapping(error)
        if error_map is not None:
            reason = error_map.get("code") or error_map.get("message") or "error"
        else:
            reason = error
        return BatchResult(custom_id=cid, content=None, error=str(reason))
    response = _mapping(line.get("response"))
    if response is None:
        return BatchResult(custom_id=cid, content=None, error="missing_response")
    status_code = response.get("status_code")
    if isinstance(status_code, int) and status_code >= 400:
        return BatchResult(custom_id=cid, content=None, error=f"http_{status_code}")
    body = _mapping(response.get("body")) or {}
    choices = body.get("choices")
    content: str | None = None
    if isinstance(choices, list) and choices:
        first = _mapping(choices[0])
        msg = _mapping(first.get("message")) if first is not None else None
        if msg is not None:
            content = _text_of(msg.get("content"))
    usage = _mapping(body.get("usage")) or {}
    return BatchResult(
        custom_id=cid,
        content=content,
        usage=_usage(_as_int(usage.get("prompt_tokens")), _as_int(usage.get("completion_tokens"))),
    )


def _parse_jsonl(text: str) -> list[BatchResult]:
    return _parse_items(_iter_jsonl(text))


def _iter_jsonl(text: str) -> Iterable[object]:
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _parse_items(items: Iterable[object]) -> list[BatchResult]:
    out: list[BatchResult] = []
    for item in items:
        parsed = _parse_line(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _require_key(api_key: SecretStr | None, family: str) -> str:
    raw = api_key.get_secret_value() if api_key is not None else ""
    if not raw:
        raise BatchExecutorError(f"{family} batch adapter has no API key configured")
    return raw


# ── raw-httpx base ────────────────────────────────────────────────────────────


class _HttpBatchAdapter:
    family = ""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(family={self.family!r}, base_url={self._base_url!r})"

    def _headers(self) -> dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _same_origin(self, url: str) -> bool:
        """True when *url* has the same scheme + host + port as ``base_url``."""
        try:
            target, base = httpx.URL(url), httpx.URL(self._base_url)
        except (httpx.InvalidURL, TypeError, ValueError):
            return False
        return (target.scheme, target.host, target.port) == (base.scheme, base.host, base.port)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: object | None = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        # Credentials only go to the adapter's own origin; a provider-returned URL
        # on another host (presigned storage, CDN) is fetched without them.
        # ``_headers()`` raises before any I/O when the key is missing.
        headers = self._headers() if authenticated else {}
        try:
            if self._client is not None:
                response = await self._client.request(
                    method, url, headers=headers, json=json_body, timeout=_TIMEOUT_S
                )
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                    response = await client.request(method, url, headers=headers, json=json_body)
        except httpx.HTTPError as exc:
            raise BatchExecutorError(
                f"{self.family} batch {method} failed: {type(exc).__name__}"
            ) from None
        if response.status_code >= 400:
            raise BatchExecutorError(
                f"{self.family} batch {method} returned {response.status_code}"
            )
        return response

    async def _json(self, method: str, url: str, *, json_body: object | None = None) -> Any:
        response = await self._request(method, url, json_body=json_body)
        try:
            return response.json()
        except ValueError:
            raise BatchExecutorError(f"{self.family} batch {method} returned non-JSON") from None

    async def _text(self, url: str) -> str:
        return (await self._request("GET", url, authenticated=self._same_origin(url))).text


class AnthropicBatchAdapter(_HttpBatchAdapter):
    """Anthropic Message Batches over raw ``httpx`` (LiteLLM lacks the path)."""

    family = "anthropic"
    _STATUS = {
        "in_progress": BatchStatus.RUNNING,
        "canceling": BatchStatus.RUNNING,
        "ended": BatchStatus.COMPLETED,
    }

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        base_url: str = "https://api.anthropic.com",
        client: httpx.AsyncClient | None = None,
        anthropic_version: str = "2023-06-01",
    ) -> None:
        super().__init__(api_key, base_url=base_url, client=client)
        self._version = anthropic_version

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": _require_key(self._api_key, self.family),
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    @property
    def _batches(self) -> str:
        return f"{self._base_url}/v1/messages/batches"

    @staticmethod
    def _params(request: BatchRequest) -> dict[str, object]:
        system = [m for m in request.messages if m.get("role") == "system"]
        params: dict[str, object] = {
            "model": provider_model(request.model_ref),
            "max_tokens": request.max_tokens,
        }
        if system:
            params["system"] = "\n\n".join(str(m.get("content", "")) for m in system)
        params["messages"] = [dict(m) for m in request.messages if m.get("role") != "system"]
        return params

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        """Create a Message Batch; system messages are folded into ``system``.

        Raises :class:`BatchExecutorError` when no job id is returned.
        """
        payload = {
            "requests": [{"custom_id": r.custom_id, "params": self._params(r)} for r in requests]
        }
        data = await self._json("POST", self._batches, json_body=payload)
        job_id = _get(data, "id")
        if not isinstance(job_id, str) or not job_id:
            raise BatchExecutorError("anthropic batch submit returned no id")
        return job_id

    async def poll(self, job_id: str) -> BatchStatus:
        """Map ``processing_status`` to :class:`BatchStatus` (unknown ⇒ ``FAILED``)."""
        data = await self._json("GET", f"{self._batches}/{job_id}")
        return self._STATUS.get(str(_get(data, "processing_status")), BatchStatus.FAILED)

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        """Download and parse the JSONL at ``results_url``; raises if there is none."""
        data = await self._json("GET", f"{self._batches}/{job_id}")
        url = _get(data, "results_url")
        if not isinstance(url, str) or not url:
            raise BatchExecutorError("anthropic batch has no results_url")
        return _parse_jsonl(await self._text(url))

    async def cancel(self, job_id: str) -> None:
        """Request cancellation of the Message Batch *job_id*."""
        await self._request("POST", f"{self._batches}/{job_id}/cancel")


class OpenRouterBatchAdapter(_HttpBatchAdapter):
    """OpenRouter ``:batch`` async batches over raw ``httpx`` (best-effort fields)."""

    family = "openrouter"
    _STATUS = {
        "validating": BatchStatus.RUNNING,
        "in_progress": BatchStatus.RUNNING,
        "finalizing": BatchStatus.RUNNING,
        "submitted": BatchStatus.RUNNING,
        "completed": BatchStatus.COMPLETED,
        "ended": BatchStatus.COMPLETED,
        "failed": BatchStatus.FAILED,
        "cancelled": BatchStatus.FAILED,
        "cancelling": BatchStatus.FAILED,
        "expired": BatchStatus.EXPIRED,
    }

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, client=client)

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {_require_key(self._api_key, self.family)}",
            "content-type": "application/json",
        }

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        """Create a 24h batch with the GW privacy defaults on every body.

        Raises :class:`BatchExecutorError` when no job id is returned.
        """
        payload = {
            "completion_window": "24h",
            "requests": [{"custom_id": r.custom_id, "body": _openrouter_body(r)} for r in requests],
        }
        data = await self._json("POST", f"{self._base_url}/batches", json_body=payload)
        job_id = _get(data, "id")
        if not isinstance(job_id, str) or not job_id:
            raise BatchExecutorError("openrouter batch submit returned no id")
        return job_id

    async def poll(self, job_id: str) -> BatchStatus:
        """Map the batch ``status`` to :class:`BatchStatus` (unknown ⇒ ``FAILED``)."""
        data = await self._json("GET", f"{self._base_url}/batches/{job_id}")
        return self._STATUS.get(str(_get(data, "status")), BatchStatus.FAILED)

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        """Results from inline ``results``, ``results_url`` or ``output_file_id`` (first found).

        Raises :class:`BatchExecutorError` when the batch exposes none.
        """
        data = await self._json("GET", f"{self._base_url}/batches/{job_id}")
        inline = _get(data, "results")
        if isinstance(inline, list):
            return _parse_items(inline)
        url = _get(data, "results_url")
        if isinstance(url, str) and url:
            return _parse_jsonl(await self._text(url))
        file_id = _get(data, "output_file_id")
        if isinstance(file_id, str) and file_id:
            return _parse_jsonl(await self._text(f"{self._base_url}/files/{file_id}/content"))
        raise BatchExecutorError("openrouter batch has no results")

    async def cancel(self, job_id: str) -> None:
        """Delete (cancel) the batch *job_id*."""
        await self._request("DELETE", f"{self._base_url}/batches/{job_id}")


def _chat_body(request: BatchRequest) -> dict[str, object]:
    body: dict[str, object] = {
        "model": provider_model(request.model_ref),
        "messages": [dict(m) for m in request.messages],
        "max_tokens": request.max_tokens,
    }
    if request.response_format is not None:
        body["response_format"] = dict(request.response_format)
    return body


def _openrouter_body(request: BatchRequest) -> dict[str, object]:
    """``_chat_body`` plus the GW privacy defaults (never weakened, only added)."""
    body = _chat_body(request)
    body["provider"] = dict(_OPENROUTER_PRIVACY)
    return body


# ── OpenAI (LiteLLM) ──────────────────────────────────────────────────────────


class OpenAIBatchAdapter:
    """OpenAI Batch via LiteLLM's files + batches API (module injectable for tests)."""

    family = "openai"
    _STATUS = {
        "validating": BatchStatus.RUNNING,
        "in_progress": BatchStatus.RUNNING,
        "finalizing": BatchStatus.RUNNING,
        "completed": BatchStatus.COMPLETED,
        "failed": BatchStatus.FAILED,
        "cancelling": BatchStatus.FAILED,
        "cancelled": BatchStatus.FAILED,
        "expired": BatchStatus.EXPIRED,
    }

    def __init__(self, api_key: SecretStr | None, *, litellm_module: Any | None = None) -> None:
        if litellm_module is None:
            import litellm

            litellm_module = litellm
        self._api_key = api_key
        self._litellm: Any = litellm_module

    def __repr__(self) -> str:
        return f"{type(self).__name__}(family={self.family!r})"

    async def _call(self, name: str, **kwargs: Any) -> Any:
        key = _require_key(self._api_key, self.family)
        try:
            return await getattr(self._litellm, name)(
                custom_llm_provider="openai", api_key=key, **kwargs
            )
        except Exception as exc:
            raise BatchExecutorError(f"openai batch {name} failed: {type(exc).__name__}") from None

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        """Upload the requests as a JSONL file and create a 24h chat-completions batch.

        Raises :class:`BatchExecutorError` when no job id is returned.
        """
        _require_key(self._api_key, self.family)
        lines = [
            json.dumps(
                {
                    "custom_id": r.custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": _chat_body(r),
                }
            )
            for r in requests
        ]
        raw = ("\n".join(lines) + "\n").encode()
        uploaded = await self._call("acreate_file", file=("batch.jsonl", raw), purpose="batch")
        batch = await self._call(
            "acreate_batch",
            completion_window="24h",
            endpoint="/v1/chat/completions",
            input_file_id=_get(uploaded, "id"),
        )
        job_id = _get(batch, "id")
        if not isinstance(job_id, str) or not job_id:
            raise BatchExecutorError("openai batch submit returned no id")
        return job_id

    async def poll(self, job_id: str) -> BatchStatus:
        """Map the batch ``status`` to :class:`BatchStatus` (unknown ⇒ ``FAILED``)."""
        batch = await self._call("aretrieve_batch", batch_id=job_id)
        return self._STATUS.get(str(_get(batch, "status")), BatchStatus.FAILED)

    async def results(self, job_id: str) -> Sequence[BatchResult]:
        """Download and parse the batch's output file; raises if it has none."""
        batch = await self._call("aretrieve_batch", batch_id=job_id)
        file_id = _get(batch, "output_file_id")
        if not isinstance(file_id, str) or not file_id:
            raise BatchExecutorError("openai batch has no output_file_id")
        content = await self._call("afile_content", file_id=file_id)
        raw = _get(content, "content")
        text = raw.decode() if isinstance(raw, bytes) else str(_get(content, "text") or "")
        return _parse_jsonl(text)

    async def cancel(self, job_id: str) -> None:
        """Cancel the batch *job_id*."""
        await self._call("acancel_batch", batch_id=job_id)


def default_adapters(
    settings: Settings, *, key_resolver: KeyResolver | None = None
) -> dict[str, BatchApiPort]:
    """Build one adapter per family that has an API key.

    By default keys come from *settings* (global keys). A host serving several
    tenants passes *key_resolver* (``family -> SecretStr | None``) to inject the
    tenant's keys instead; when given it is authoritative for every family
    (``None`` / empty ⇒ no adapter for that family). Keys stay ``SecretStr`` and
    never appear in ``repr`` or logs.
    """

    def key_for(family: str, fallback: SecretStr) -> SecretStr | None:
        """The family's key (resolver or settings fallback); ``None`` when unset or empty."""
        key = key_resolver(family) if key_resolver is not None else fallback
        return key if key is not None and key.get_secret_value() else None

    adapters: dict[str, BatchApiPort] = {}
    anthropic_key = key_for("anthropic", settings.anthropic_api_key)
    if anthropic_key is not None:
        adapters["anthropic"] = AnthropicBatchAdapter(anthropic_key)
    openai_key = key_for("openai", settings.openai_api_key)
    if openai_key is not None:
        adapters["openai"] = OpenAIBatchAdapter(openai_key)
    openrouter_key = key_for("openrouter", settings.openrouter_api_key)
    if openrouter_key is not None:
        adapters["openrouter"] = OpenRouterBatchAdapter(
            openrouter_key, base_url=settings.openrouter_base_url
        )
    return adapters
