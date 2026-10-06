"""Laya local, Jev SDK and LLM fallback adapters + factory (Phase DEC — DEC2-02..05)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.decisions.adapters.factory import build_backend
from prismal.decisions.adapters.jev_sdk import JevSdkDecisionAdapter
from prismal.decisions.adapters.laya_local import LayaLocalDecisionAdapter
from prismal.decisions.adapters.llm_fallback import LlmFallbackDecisionAdapter
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.types import GatePolicy
from tests.unit.decisions.contract import CHOICE, NOUL, QUESTIONS, SCORE, STATE, wide_choice

POLICY = GatePolicy(timeout_ms=500)


def _laya_answers() -> dict[str, Any]:
    return {
        CHOICE.id: {
            "choice": "researcher",
            "confidence": 0.4,
            "answer_confidence": 0.7,
            "probabilities": {"coder": 0.3, "researcher": 0.7},
        },
        NOUL.id: {"noul": 0.9, "confidence": 0.6, "answer_confidence": 0.9},
        SCORE.id: {"score": 1, "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1}, "confidence": 0.5},
    }


class FakeRouter:
    instances = 0

    def __init__(self) -> None:
        FakeRouter.instances += 1
        self.calls: list[dict[str, Any]] = []
        self.device = "cpu"

    def predict(self, state: Any, questions: Any, **kw: Any) -> dict[str, Any]:
        self.calls.append({"state": state, "questions": questions, **kw})
        return {"answers": _laya_answers(), "routing": {"model": kw.get("model")}}


@pytest.fixture
def fake_laya(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("laya")
    mod.Router = FakeRouter  # type: ignore[attr-defined]
    FakeRouter.instances = 0
    monkeypatch.setitem(sys.modules, "laya", mod)
    return mod


class TestLayaLocal:
    async def test_predict_in_thread_and_parse(self, fake_laya: types.ModuleType) -> None:
        adapter = LayaLocalDecisionAdapter()
        out = await adapter.decide(STATE, QUESTIONS, policy=POLICY)
        assert out[CHOICE.id].value == "researcher"
        assert out[CHOICE.id].answer_confidence == pytest.approx(0.7)
        assert out[CHOICE.id].spread_confidence == pytest.approx(0.4)
        assert out[NOUL.id].value is True
        assert out[SCORE.id].value == "partial"
        assert adapter.name == "laya"
        assert adapter.model == "laya-multilingual"

    async def test_passes_checkpoint_and_max_len(self, fake_laya: types.ModuleType) -> None:
        adapter = LayaLocalDecisionAdapter(checkpoint="laya-multilingual", max_len=1024)
        await adapter.decide(STATE, QUESTIONS, policy=POLICY)
        router = adapter._loaded["laya-multilingual"]  # noqa: SLF001
        assert router.calls[0]["model"] == "laya-multilingual"
        assert router.calls[0]["max_len"] == 1024
        assert router.calls[0]["questions"][CHOICE.id]["criteria"] == {
            "coder": "Writes code",
            "researcher": "Searches the web",
        }

    async def test_import_deferred_until_first_use(self, fake_laya: types.ModuleType) -> None:
        adapter = LayaLocalDecisionAdapter()
        assert FakeRouter.instances == 0
        await adapter.warmup()
        assert FakeRouter.instances == 1
        await adapter.decide(STATE, QUESTIONS, policy=POLICY)
        assert FakeRouter.instances == 1

    async def test_missing_extra_abstains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "laya", None)
        out = await LayaLocalDecisionAdapter().decide(STATE, QUESTIONS, policy=POLICY)
        assert {d.reason for d in out.values()} == {"backend_error"}

    def test_english_checkpoint_rejects_spanish(self) -> None:
        en = LayaLocalDecisionAdapter(checkpoint="laya")
        assert en.supports(CHOICE, language="en") is True
        assert en.supports(CHOICE, language="es") is False
        multi = LayaLocalDecisionAdapter()
        assert multi.supports(CHOICE, language="es") is True

    def test_over_20_options_unsupported(self) -> None:
        adapter = LayaLocalDecisionAdapter()
        assert adapter.supports(wide_choice(20), language="en") is True
        assert adapter.supports(wide_choice(21), language="en") is False

    async def test_gpu_without_fp16_warns(
        self, fake_laya: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import prismal.decisions.adapters.laya_local as mod

        warnings: list[str] = []
        monkeypatch.setattr(mod.logger, "warning", lambda event, **_: warnings.append(event))

        class GpuRouter(FakeRouter):
            def __init__(self) -> None:
                super().__init__()
                self.device = "cuda:0"

        fake_laya.Router = GpuRouter  # type: ignore[attr-defined]
        await LayaLocalDecisionAdapter(cuda_amp="bf16").warmup()
        assert "laya_cuda_amp_not_fp16" in warnings
        warnings.clear()
        await LayaLocalDecisionAdapter(cuda_amp="fp16").warmup()
        assert warnings == []

    async def test_max_loaded_evicts(self, fake_laya: types.ModuleType) -> None:
        adapter = LayaLocalDecisionAdapter(max_loaded=1)
        adapter._router("a")  # noqa: SLF001
        adapter._router("b")  # noqa: SLF001
        assert list(adapter._loaded) == ["b"]  # noqa: SLF001
        await adapter.aclose()
        assert adapter._loaded == {}  # noqa: SLF001


class FakeSdkResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return self._data


class FakeAsyncClient:
    created: list[dict[str, Any]] = []
    response: dict[str, Any] = {}

    def __init__(self, **kw: Any) -> None:
        FakeAsyncClient.created.append(kw)
        self.closed = False

    async def system_one(self, state: Any, questions: Any, **kw: Any) -> FakeSdkResponse:
        return FakeSdkResponse(FakeAsyncClient.response)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeAsyncClient]:
    mod = types.ModuleType("typesafe_sdk")
    mod.AsyncTypeSafeClient = FakeAsyncClient  # type: ignore[attr-defined]
    FakeAsyncClient.created = []
    FakeAsyncClient.response = {
        "model": "jev-1.13.0",
        "answers": {NOUL.id: {"type": "noul", "noul": 0.8}},
    }
    monkeypatch.setitem(sys.modules, "typesafe_sdk", mod)
    return FakeAsyncClient


class TestJevSdk:
    async def test_call_through_shim(self, fake_sdk: type[FakeAsyncClient]) -> None:
        adapter = JevSdkDecisionAdapter(
            api_key=SecretStr("sk"), model="jev-1.13.0", base_url="https://api.typesafe.ai/v1"
        )
        out = await adapter.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
        assert out[NOUL.id].value is True
        assert fake_sdk.created[0]["base_url"] == "https://api.typesafe.ai"  # SDK appends /v1
        assert fake_sdk.created[0]["api_key"] == "sk"

    async def test_mismatch_abstains(self, fake_sdk: type[FakeAsyncClient]) -> None:
        fake_sdk.response = {"model": "jev-2.0.0", "answers": {}}
        adapter = JevSdkDecisionAdapter(api_key=SecretStr("sk"), model="jev-1.13.0")
        out = await adapter.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
        assert out[NOUL.id].reason == "model_mismatch"

    async def test_missing_sdk_abstains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
        adapter = JevSdkDecisionAdapter(api_key=SecretStr("sk"), model="jev-1.13.0")
        out = await adapter.decide(STATE, {NOUL.id: NOUL}, policy=POLICY)
        assert out[NOUL.id].reason == "backend_error"

    def test_repr_hides_key(self) -> None:
        adapter = JevSdkDecisionAdapter(api_key=SecretStr("sk-HIDDEN"), model="jev-1.13.0")
        assert "HIDDEN" not in repr(adapter)


class FakeStructured:
    def __init__(self, answer: Any, calls: list[Any]) -> None:
        self._answer = answer
        self._calls = calls

    async def ainvoke(self, messages: Any) -> dict[str, Any]:
        self._calls.append(messages)
        from langchain_core.messages import AIMessage

        return {"raw": AIMessage(content=""), "parsed": {"answer": self._answer}}


class FakeLlm:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[Any] = []
        self.schemas: list[Any] = []

    def with_structured_output(self, schema: Any, **kw: Any) -> FakeStructured:
        self.schemas.append(schema)
        return FakeStructured(self.answer, self.calls)


class TestLlmFallback:
    async def test_one_hot_answer_via_secure_prompt(self) -> None:
        llm = FakeLlm("coder")
        adapter = LlmFallbackDecisionAdapter(model="m", llm_factory=lambda: llm)
        out = await adapter.decide(STATE, {CHOICE.id: CHOICE}, policy=POLICY)
        d = out[CHOICE.id]
        assert d.value == "coder"
        assert d.probabilities == {"coder": 1.0, "researcher": 0.0}
        assert d.calibration_version is None
        assert d.backend == "llm"
        system, user = llm.calls[0][0]["content"], llm.calls[0][1]["content"]
        assert CHOICE.instructions in system
        assert "write a sort function" in user
        assert "<user_input>" in user
        assert llm.schemas[0]["properties"]["answer"]["enum"] == ["coder", "researcher"]

    async def test_noul_bool_answer(self) -> None:
        llm = FakeLlm("false")
        out = await LlmFallbackDecisionAdapter(llm_factory=lambda: llm).decide(
            STATE, {NOUL.id: NOUL}, policy=POLICY
        )
        assert out[NOUL.id].value is False

    async def test_invalid_answer_abstains(self) -> None:
        llm = FakeLlm("hacker")
        out = await LlmFallbackDecisionAdapter(llm_factory=lambda: llm).decide(
            STATE, {CHOICE.id: CHOICE}, policy=POLICY
        )
        assert out[CHOICE.id].abstained

    async def test_budget_guard_stop_abstains(self) -> None:
        from prismal.budget.guard import BudgetGuard
        from prismal.budget.meter import CostMeter
        from prismal.budget.types import Budget, Usage

        meter = CostMeter()
        meter.record(Usage(prompt_tokens=10_000, calls=1))
        guard = BudgetGuard(Budget(max_tokens=10), meter, hard_cap=False)
        llm = FakeLlm("coder")
        out = await LlmFallbackDecisionAdapter(llm_factory=lambda: llm, budget_guard=guard).decide(
            STATE, {CHOICE.id: CHOICE}, policy=POLICY
        )
        assert out[CHOICE.id].abstained
        assert llm.calls == []

    async def test_supports_everything(self) -> None:
        adapter = LlmFallbackDecisionAdapter(llm_factory=lambda: FakeLlm("x"))
        assert adapter.supports(wide_choice(300), language="other") is True


class TestFactory:
    def test_jev_http_by_default(self) -> None:
        s = Settings(jev_api_key="k")
        b = build_backend("jev", s)
        assert isinstance(b, SystemOneHttpDecisionAdapter)
        assert b.model == "jev-1.13.0"

    def test_jev_sdk_transport(self) -> None:
        b = build_backend("jev", Settings(jev_api_key="k", jev_transport="sdk"))
        assert isinstance(b, JevSdkDecisionAdapter)

    def test_jev_without_key_skipped(self) -> None:
        assert build_backend("jev", Settings()) is None

    def test_laya_local_and_http(self) -> None:
        assert isinstance(build_backend("laya", Settings()), LayaLocalDecisionAdapter)
        b = build_backend("laya", Settings(laya_mode="http", laya_base_url="http://laya:8000/v1"))
        assert isinstance(b, SystemOneHttpDecisionAdapter)
        assert b.name == "laya"
        assert b.model == "laya-multilingual"

    def test_llm(self) -> None:
        assert isinstance(
            build_backend("llm", Settings(decision_llm_fallback_model="m")),
            LlmFallbackDecisionAdapter,
        )

    def test_rule_and_unknown(self) -> None:
        assert build_backend("rule", Settings()) is None
        assert build_backend("nope", Settings()) is None

    def test_vault_key_per_tenant(self) -> None:
        from prismal.identity.vault import FakeVault

        vault = FakeVault()
        vault.store("jev_api_key@acme", SecretStr("tenant-key"))
        b = build_backend("jev", Settings(), vault=vault, org_id="acme")
        assert isinstance(b, SystemOneHttpDecisionAdapter)
        assert b._api_key is not None  # noqa: SLF001
        assert b._api_key.get_secret_value() == "tenant-key"  # noqa: SLF001
