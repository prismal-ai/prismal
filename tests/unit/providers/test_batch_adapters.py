"""Provider batch adapters (AMR5-01..03 — SPEC-AMR-BEX-001).

All HTTP is faked with ``httpx.MockTransport``; the OpenAI adapter receives a
fake ``litellm``-shaped module. No network, no real keys.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.core.exceptions import BatchExecutorError
from prismal.providers.batch_adapters import (
    AnthropicBatchAdapter,
    BatchApiPort,
    BatchRequest,
    BatchResult,
    BatchStatus,
    OpenAIBatchAdapter,
    OpenRouterBatchAdapter,
    default_adapters,
    family_of,
    provider_model,
)

KEY = "sk-test-SECRET-123"


def _req(cid: str = "r1", model: str = "anthropic/claude-x") -> BatchRequest:
    return BatchRequest(
        custom_id=cid,
        model_ref=model,
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        max_tokens=64,
    )


class _Recorder:
    """A MockTransport handler that records requests and replies from a route table."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url))
        if key not in self.routes:
            return httpx.Response(404, json={"error": "no route"})
        return self.routes[key]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


# ── shared types ──────────────────────────────────────────────────────────────


def test_batch_status_values() -> None:
    assert [s.value for s in BatchStatus] == [
        "submitted",
        "running",
        "completed",
        "failed",
        "expired",
        "fallback",
    ]


def test_batch_result_defaults() -> None:
    r = BatchResult(custom_id="a", content="x")
    assert r.error is None
    assert r.usage.calls == 0


@pytest.mark.parametrize(
    ("ref", "family"),
    [
        ("anthropic/claude-sonnet-4-5", "anthropic"),
        ("claude-haiku-4-5", "anthropic"),
        ("openai/gpt-4o-mini", "openai"),
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("gw:openrouter/meta-llama/llama-3.3-70b:batch", "openrouter"),
        ("openrouter/anthropic/claude-x", "openrouter"),
        ("gw:openrouter/meta-llama/llama-3.3-70b", None),
        ("gemini/gemini-2.0", None),
        ("ollama/llama3", None),
        ("", None),
    ],
)
def test_family_of(ref: str, family: str | None) -> None:
    assert family_of(ref) == family


@pytest.mark.parametrize(
    ("ref", "model"),
    [
        ("anthropic/claude-x", "claude-x"),
        ("claude-x", "claude-x"),
        ("openai/gpt-4o", "gpt-4o"),
        ("gw:openrouter/meta/llama:batch", "meta/llama:batch"),
        ("openrouter/meta/llama", "meta/llama"),
    ],
)
def test_provider_model(ref: str, model: str) -> None:
    assert provider_model(ref) == model


def test_adapters_conform_to_port_and_never_repr_key() -> None:
    adapters: list[Any] = [
        AnthropicBatchAdapter(SecretStr(KEY)),
        OpenAIBatchAdapter(SecretStr(KEY), litellm_module=SimpleNamespace()),
        OpenRouterBatchAdapter(SecretStr(KEY)),
    ]
    for a in adapters:
        assert isinstance(a, BatchApiPort)
        assert KEY not in repr(a)
        assert KEY not in str(a)
    assert [a.family for a in adapters] == ["anthropic", "openai", "openrouter"]


def test_default_adapters_only_for_configured_keys() -> None:
    s = Settings(
        anthropic_api_key=SecretStr(KEY),
        openai_api_key=SecretStr(""),
        openrouter_api_key=SecretStr(KEY),
    )
    assert sorted(default_adapters(s)) == ["anthropic", "openrouter"]
    s2 = Settings(
        anthropic_api_key=SecretStr(""),
        openai_api_key=SecretStr(KEY),
        openrouter_api_key=SecretStr(""),
    )
    assert sorted(default_adapters(s2)) == ["openai"]


# ── Anthropic (raw httpx) ─────────────────────────────────────────────────────

A_BASE = "https://api.anthropic.com/v1/messages/batches"


async def test_anthropic_submit_payload_and_headers() -> None:
    rec = _Recorder({("POST", A_BASE): httpx.Response(200, json={"id": "msgbatch_1"})})
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    job = await adapter.submit(
        [
            _req("r1"),
            BatchRequest(
                custom_id="r2",
                model_ref="claude-y",
                messages=[{"role": "user", "content": "yo"}],
                max_tokens=8,
            ),
        ]
    )
    assert job == "msgbatch_1"
    sent = rec.requests[0]
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    first = body["requests"][0]
    assert first["custom_id"] == "r1"
    assert first["params"] == {
        "model": "claude-x",
        "max_tokens": 64,
        "system": "be brief",
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert "system" not in body["requests"][1]["params"]


async def test_anthropic_submit_without_key_sends_nothing() -> None:
    rec = _Recorder({})
    adapter = AnthropicBatchAdapter(None, client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.submit([_req()])
    assert rec.requests == []


async def test_anthropic_submit_http_error_raises_without_leaking_key() -> None:
    rec = _Recorder({("POST", A_BASE): httpx.Response(400, json={"error": "bad"})})
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError) as exc:
        await adapter.submit([_req()])
    assert KEY not in str(exc.value)


async def test_anthropic_submit_missing_id_raises() -> None:
    rec = _Recorder({("POST", A_BASE): httpx.Response(200, json={})})
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.submit([_req()])


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("in_progress", BatchStatus.RUNNING),
        ("canceling", BatchStatus.RUNNING),
        ("ended", BatchStatus.COMPLETED),
        ("weird", BatchStatus.FAILED),
    ],
)
async def test_anthropic_poll_mapping(raw: str, status: BatchStatus) -> None:
    rec = _Recorder({("GET", f"{A_BASE}/b1"): httpx.Response(200, json={"processing_status": raw})})
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    assert await adapter.poll("b1") is status


async def test_anthropic_results_parses_jsonl() -> None:
    url = "https://api.anthropic.com/v1/messages/batches/b1/results"
    lines = [
        {
            "custom_id": "r1",
            "result": {
                "type": "succeeded",
                "message": {
                    "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "!"}],
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                },
            },
        },
        {"custom_id": "r2", "result": {"type": "errored", "error": {"type": "x"}}},
        {"custom_id": "r3", "result": {"type": "expired"}},
    ]
    jsonl = "\n".join(json.dumps(x) for x in lines) + "\nnot-json\n\n"
    rec = _Recorder(
        {
            ("GET", f"{A_BASE}/b1"): httpx.Response(
                200, json={"processing_status": "ended", "results_url": url}
            ),
            ("GET", url): httpx.Response(200, text=jsonl),
        }
    )
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    out = {r.custom_id: r for r in await adapter.results("b1")}
    assert out["r1"].content == "hello!"
    assert out["r1"].usage.prompt_tokens == 10
    assert out["r1"].usage.completion_tokens == 3
    assert out["r1"].usage.calls == 1
    assert out["r1"].error is None
    assert out["r2"].error == "errored"
    assert out["r2"].content is None
    assert out["r3"].error == "expired"
    assert rec.requests[-1].headers["x-api-key"] == KEY


async def test_anthropic_results_without_url_raises() -> None:
    rec = _Recorder(
        {("GET", f"{A_BASE}/b1"): httpx.Response(200, json={"processing_status": "ended"})}
    )
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.results("b1")


async def test_anthropic_cancel() -> None:
    rec = _Recorder({("POST", f"{A_BASE}/b1/cancel"): httpx.Response(200, json={})})
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    await adapter.cancel("b1")
    assert rec.requests[0].method == "POST"


async def test_anthropic_owns_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an injected client the adapter builds (and closes) its own."""
    rec = _Recorder(
        {("GET", f"{A_BASE}/b1"): httpx.Response(200, json={"processing_status": "ended"})}
    )
    real = httpx.AsyncClient

    def _factory(**kw: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(rec), **kw)

    monkeypatch.setattr("prismal.providers.batch_adapters.httpx.AsyncClient", _factory)
    adapter = AnthropicBatchAdapter(SecretStr(KEY))
    assert await adapter.poll("b1") is BatchStatus.COMPLETED


# ── OpenAI (LiteLLM) ─────────────────────────────────────────────────────────


class _FakeLiteLLM:
    def __init__(self, *, status: str = "completed", content: bytes = b"") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.status = status
        self.content = content

    async def acreate_file(self, **kw: Any) -> Any:
        self.calls.append(("acreate_file", kw))
        return SimpleNamespace(id="file-in")

    async def acreate_batch(self, **kw: Any) -> Any:
        self.calls.append(("acreate_batch", kw))
        return SimpleNamespace(id="batch_1")

    async def aretrieve_batch(self, **kw: Any) -> Any:
        self.calls.append(("aretrieve_batch", kw))
        return SimpleNamespace(status=self.status, output_file_id="file-out", error_file_id=None)

    async def afile_content(self, **kw: Any) -> Any:
        self.calls.append(("afile_content", kw))
        return SimpleNamespace(content=self.content)

    async def acancel_batch(self, **kw: Any) -> Any:
        self.calls.append(("acancel_batch", kw))
        return SimpleNamespace(status="cancelling")


async def test_openai_submit_uploads_jsonl_and_creates_batch() -> None:
    fake = _FakeLiteLLM()
    adapter = OpenAIBatchAdapter(SecretStr(KEY), litellm_module=fake)
    req = BatchRequest(
        custom_id="r1",
        model_ref="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        response_format={"type": "json_object"},
    )
    assert await adapter.submit([req]) == "batch_1"
    name, upload = fake.calls[0]
    assert name == "acreate_file"
    assert upload["purpose"] == "batch"
    assert upload["custom_llm_provider"] == "openai"
    assert upload["api_key"] == KEY
    fname, raw = upload["file"]
    assert fname.endswith(".jsonl")
    line = json.loads(raw.decode().splitlines()[0])
    assert line == {
        "custom_id": "r1",
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 16,
            "response_format": {"type": "json_object"},
        },
    }
    name, create = fake.calls[1]
    assert name == "acreate_batch"
    assert create["input_file_id"] == "file-in"
    assert create["completion_window"] == "24h"
    assert create["endpoint"] == "/v1/chat/completions"


async def test_openai_submit_without_key_raises() -> None:
    fake = _FakeLiteLLM()
    with pytest.raises(BatchExecutorError):
        await OpenAIBatchAdapter(SecretStr(""), litellm_module=fake).submit([_req()])
    assert fake.calls == []


async def test_openai_submit_sdk_error_is_wrapped() -> None:
    class _Boom(_FakeLiteLLM):
        async def acreate_file(self, **kw: Any) -> Any:
            raise RuntimeError(f"denied {kw['api_key']}")

    with pytest.raises(BatchExecutorError) as exc:
        await OpenAIBatchAdapter(SecretStr(KEY), litellm_module=_Boom()).submit([_req()])
    assert KEY not in str(exc.value)


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("validating", BatchStatus.RUNNING),
        ("in_progress", BatchStatus.RUNNING),
        ("finalizing", BatchStatus.RUNNING),
        ("completed", BatchStatus.COMPLETED),
        ("failed", BatchStatus.FAILED),
        ("cancelled", BatchStatus.FAILED),
        ("expired", BatchStatus.EXPIRED),
    ],
)
async def test_openai_poll_mapping(raw: str, status: BatchStatus) -> None:
    adapter = OpenAIBatchAdapter(SecretStr(KEY), litellm_module=_FakeLiteLLM(status=raw))
    assert await adapter.poll("batch_1") is status


async def test_openai_results_parse_output_file() -> None:
    lines = [
        {
            "custom_id": "r1",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                },
            },
            "error": None,
        },
        {"custom_id": "r2", "response": None, "error": {"code": "rate_limit", "message": "x"}},
        {"custom_id": "r3", "response": {"status_code": 500, "body": {}}},
    ]
    content = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    fake = _FakeLiteLLM(content=content)
    adapter = OpenAIBatchAdapter(SecretStr(KEY), litellm_module=fake)
    out = {r.custom_id: r for r in await adapter.results("batch_1")}
    assert out["r1"].content == "ok"
    assert (out["r1"].usage.prompt_tokens, out["r1"].usage.completion_tokens) == (7, 2)
    assert out["r2"].error == "rate_limit"
    assert out["r3"].error == "http_500"
    content_call = [kw for n, kw in fake.calls if n == "afile_content"][0]
    assert content_call["file_id"] == "file-out"


async def test_openai_results_without_output_file_raises() -> None:
    class _NoOut(_FakeLiteLLM):
        async def aretrieve_batch(self, **kw: Any) -> Any:
            return SimpleNamespace(status="completed", output_file_id=None)

    with pytest.raises(BatchExecutorError):
        await OpenAIBatchAdapter(SecretStr(KEY), litellm_module=_NoOut()).results("b")


async def test_openai_cancel() -> None:
    fake = _FakeLiteLLM()
    await OpenAIBatchAdapter(SecretStr(KEY), litellm_module=fake).cancel("batch_1")
    assert fake.calls[-1][0] == "acancel_batch"
    assert fake.calls[-1][1]["batch_id"] == "batch_1"


async def test_openai_default_module_is_litellm() -> None:
    import litellm

    assert OpenAIBatchAdapter(SecretStr(KEY))._litellm is litellm


# ── OpenRouter (raw httpx) ────────────────────────────────────────────────────

O_BASE = "https://openrouter.ai/api/v1/batches"


async def test_openrouter_submit_payload() -> None:
    rec = _Recorder(
        {("POST", O_BASE): httpx.Response(202, json={"id": "or_1", "status": "validating"})}
    )
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    req = _req("r1", "gw:openrouter/meta/llama:batch")
    assert await adapter.submit([req]) == "or_1"
    sent = rec.requests[0]
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body["completion_window"] == "24h"
    assert body["requests"][0]["custom_id"] == "r1"
    assert body["requests"][0]["body"]["model"] == "meta/llama:batch"
    assert body["requests"][0]["body"]["max_tokens"] == 64


async def test_openrouter_model_without_batch_endpoint_400_raises() -> None:
    rec = _Recorder({("POST", O_BASE): httpx.Response(400, json={"error": "no :batch"})})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.submit([_req("r1", "openrouter/x/y")])


async def test_openrouter_submit_without_key_raises() -> None:
    rec = _Recorder({})
    with pytest.raises(BatchExecutorError):
        await OpenRouterBatchAdapter(SecretStr(""), client=rec.client()).submit([_req()])
    assert rec.requests == []


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("validating", BatchStatus.RUNNING),
        ("in_progress", BatchStatus.RUNNING),
        ("completed", BatchStatus.COMPLETED),
        ("failed", BatchStatus.FAILED),
        ("expired", BatchStatus.EXPIRED),
        ("cancelled", BatchStatus.FAILED),
    ],
)
async def test_openrouter_poll_mapping(raw: str, status: BatchStatus) -> None:
    rec = _Recorder({("GET", f"{O_BASE}/or_1"): httpx.Response(200, json={"status": raw})})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    assert await adapter.poll("or_1") is status


async def test_openrouter_results_inline() -> None:
    payload = {
        "status": "completed",
        "results": [
            {
                "custom_id": "r1",
                "response": {
                    "body": {
                        "choices": [{"message": {"content": "hey"}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 1},
                    }
                },
            },
            {"custom_id": "r2", "error": {"message": "nope"}},
            "garbage",
        ],
    }
    rec = _Recorder({("GET", f"{O_BASE}/or_1"): httpx.Response(200, json=payload)})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    out = {r.custom_id: r for r in await adapter.results("or_1")}
    assert out["r1"].content == "hey"
    assert out["r1"].usage.prompt_tokens == 4
    assert out["r2"].error == "nope"


async def test_openrouter_results_via_output_file() -> None:
    line = {
        "custom_id": "r1",
        "response": {"body": {"choices": [{"message": {"content": "f"}}], "usage": {}}},
    }
    rec = _Recorder(
        {
            ("GET", f"{O_BASE}/or_1"): httpx.Response(
                200, json={"status": "completed", "output_file_id": "fo"}
            ),
            ("GET", "https://openrouter.ai/api/v1/files/fo/content"): httpx.Response(
                200, text=json.dumps(line)
            ),
        }
    )
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    out = await adapter.results("or_1")
    assert out[0].content == "f"
    assert out[0].usage.prompt_tokens == 0


async def test_openrouter_results_via_results_url() -> None:
    url = "https://openrouter.ai/x/results.jsonl"
    line = {"custom_id": "r1", "result": {"type": "succeeded", "message": {"content": "a"}}}
    rec = _Recorder(
        {
            ("GET", f"{O_BASE}/or_1"): httpx.Response(
                200, json={"status": "completed", "results_url": url}
            ),
            ("GET", url): httpx.Response(200, text=json.dumps(line)),
        }
    )
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    out = await adapter.results("or_1")
    assert out[0].content == "a"


async def test_openrouter_results_absent_raises() -> None:
    rec = _Recorder({("GET", f"{O_BASE}/or_1"): httpx.Response(200, json={"status": "completed"})})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.results("or_1")


async def test_openrouter_cancel_uses_delete() -> None:
    rec = _Recorder({("DELETE", f"{O_BASE}/or_1"): httpx.Response(200, json={})})
    await OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client()).cancel("or_1")
    assert rec.requests[0].method == "DELETE"


async def test_non_json_body_raises() -> None:
    rec = _Recorder({("GET", f"{O_BASE}/or_1"): httpx.Response(200, text="<html>")})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    with pytest.raises(BatchExecutorError):
        await adapter.poll("or_1")


# ── review fixes: credential scoping, privacy defaults, tenant keys ───────────


async def test_anthropic_results_url_on_foreign_host_gets_no_credentials() -> None:
    url = "https://storage.example.net/presigned/results.jsonl?sig=abc"
    line = {"custom_id": "r1", "result": {"type": "succeeded", "message": {"content": "ok"}}}
    rec = _Recorder(
        {
            ("GET", f"{A_BASE}/b1"): httpx.Response(
                200, json={"processing_status": "ended", "results_url": url}
            ),
            ("GET", url): httpx.Response(200, text=json.dumps(line)),
        }
    )
    adapter = AnthropicBatchAdapter(SecretStr(KEY), client=rec.client())
    out = await adapter.results("b1")
    assert out[0].content == "ok"
    foreign = rec.requests[-1]
    assert foreign.url.host == "storage.example.net"
    assert "x-api-key" not in foreign.headers
    assert "authorization" not in foreign.headers
    assert KEY not in str(foreign.headers)
    # The same-origin metadata call still carries the key.
    assert rec.requests[0].headers["x-api-key"] == KEY


@pytest.mark.parametrize(
    "url",
    [
        "http://openrouter.ai/x/results.jsonl",  # scheme downgrade
        "https://openrouter.ai:8443/x/results.jsonl",  # other port
        "https://evil.openrouter.ai.example.com/x.jsonl",  # look-alike host
    ],
)
async def test_openrouter_results_url_other_origin_gets_no_credentials(url: str) -> None:
    line = {"custom_id": "r1", "result": {"type": "succeeded", "message": {"content": "a"}}}
    rec = _Recorder(
        {
            ("GET", f"{O_BASE}/or_1"): httpx.Response(
                200, json={"status": "completed", "results_url": url}
            ),
            ("GET", url): httpx.Response(200, text=json.dumps(line)),
        }
    )
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    out = await adapter.results("or_1")
    assert out[0].content == "a"
    assert "authorization" not in rec.requests[-1].headers


async def test_openrouter_results_url_same_origin_keeps_credentials() -> None:
    url = "https://openrouter.ai/x/results.jsonl"
    line = {"custom_id": "r1", "result": {"type": "succeeded", "message": {"content": "a"}}}
    rec = _Recorder(
        {
            ("GET", f"{O_BASE}/or_1"): httpx.Response(
                200, json={"status": "completed", "results_url": url}
            ),
            ("GET", url): httpx.Response(200, text=json.dumps(line)),
        }
    )
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    await adapter.results("or_1")
    assert rec.requests[-1].headers["authorization"] == f"Bearer {KEY}"


async def test_openrouter_submit_sends_privacy_defaults_per_request() -> None:
    rec = _Recorder({("POST", O_BASE): httpx.Response(202, json={"id": "or_1"})})
    adapter = OpenRouterBatchAdapter(SecretStr(KEY), client=rec.client())
    await adapter.submit([_req("r1", "openrouter/a/b"), _req("r2", "openrouter/c/d")])
    body = json.loads(rec.requests[0].content)
    for item in body["requests"]:
        assert item["body"]["provider"] == {"data_collection": "deny", "zdr": True}


async def test_openai_body_has_no_openrouter_provider_block() -> None:
    calls: dict[str, Any] = {}

    async def acreate_file(**kw: Any) -> Any:
        calls["file"] = kw["file"][1]
        return {"id": "f1"}

    async def acreate_batch(**kw: Any) -> Any:
        return {"id": "b1"}

    mod = SimpleNamespace(acreate_file=acreate_file, acreate_batch=acreate_batch)
    await OpenAIBatchAdapter(SecretStr(KEY), litellm_module=mod).submit([_req("r1", "gpt-4o")])
    line = json.loads(calls["file"].decode().splitlines()[0])
    assert "provider" not in line["body"]


def test_default_adapters_key_resolver_injects_tenant_keys() -> None:
    s = Settings(
        anthropic_api_key=SecretStr(""),
        openai_api_key=SecretStr("global-openai"),
        openrouter_api_key=SecretStr(""),
    )
    tenant = {"anthropic": SecretStr("tenant-anthropic-KEY"), "openrouter": None}
    seen: list[str] = []

    def resolver(family: str) -> SecretStr | None:
        seen.append(family)
        return tenant.get(family)

    adapters = default_adapters(s, key_resolver=resolver)
    # Resolver wins for every family; None ⇒ that family has no adapter.
    assert sorted(adapters) == ["anthropic"]
    assert sorted(seen) == ["anthropic", "openai", "openrouter"]
    assert "tenant-anthropic-KEY" not in repr(adapters["anthropic"])
    assert adapters["anthropic"]._api_key.get_secret_value() == "tenant-anthropic-KEY"  # type: ignore[attr-defined]


def test_default_adapters_without_resolver_uses_settings_keys() -> None:
    s = Settings(
        anthropic_api_key=SecretStr(""),
        openai_api_key=SecretStr(""),
        openrouter_api_key=SecretStr(KEY),
    )
    assert sorted(default_adapters(s, key_resolver=None)) == ["openrouter"]
