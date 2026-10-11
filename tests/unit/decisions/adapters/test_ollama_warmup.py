"""Ollama warm-up at runtime start (SPEC-DEC-OLL-006 — DEC2-07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.composition import build_runtime
from prismal.core.config import Settings
from prismal.decisions.adapters.ollama import OllamaDecisionAdapter
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.runtime import DecisionRuntime, set_decision_runtime
from tests.unit.composition.test_composition import (  # noqa: F401
    _reset_global_provider,
    patched_builders,
)

pytestmark = pytest.mark.unit


class _Backend:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.model = "m"
        self.fail = fail
        self.warmed = 0

    async def warmup(self) -> bool:
        self.warmed += 1
        if self.fail:
            raise RuntimeError("boom")
        return True

    async def aclose(self) -> None:
        return None


def _runtime(backends: list[Any], **kw: Any) -> DecisionRuntime:
    settings = Settings(**kw)
    return DecisionRuntime(cascade=DecisionCascade(backends, settings=settings), settings=settings)


async def test_runtime_warmup_only_when_enabled() -> None:
    fast = _Backend("ollama:fast")
    await _runtime([fast]).warmup()
    assert fast.warmed == 0
    await _runtime([fast], decision_ollama_warmup=True).warmup()
    assert fast.warmed == 1


async def test_runtime_warmup_only_touches_ollama_tiers_and_never_raises() -> None:
    fast = _Backend("ollama:fast", fail=True)
    accurate = _Backend("ollama:accurate")
    laya = _Backend("laya")
    await _runtime([laya, fast, accurate], decision_ollama_warmup=True).warmup()
    assert (laya.warmed, fast.warmed, accurate.warmed) == (0, 1, 1)


@pytest.fixture
def warmed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def fake_warmup(self: OllamaDecisionAdapter) -> bool:
        seen.append(self.name)
        return True

    monkeypatch.setattr(OllamaDecisionAdapter, "warmup", fake_warmup)
    return seen


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    set_decision_runtime(None)
    yield
    set_decision_runtime(None)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "decisions_enabled": True,
        "decision_backend_order": ["rule", "ollama"],
        "decision_calibration_dir": str(tmp_path),
    }
    base.update(kw)
    return Settings(**base)


@pytest.mark.usefixtures("patched_builders")
async def test_build_runtime_warms_ollama_when_enabled(tmp_path: Path, warmed: list[str]) -> None:
    ctx = await build_runtime(_settings(tmp_path, decision_ollama_warmup=True), mode="context")
    assert warmed == ["ollama:fast", "ollama:accurate"]
    await ctx.aclose()


@pytest.mark.usefixtures("patched_builders")
async def test_build_runtime_skips_ollama_warmup_by_default(
    tmp_path: Path, warmed: list[str]
) -> None:
    ctx = await build_runtime(_settings(tmp_path, laya_preload=True), mode="context")
    assert warmed == []  # laya_preload never warms Ollama; only decision_ollama_warmup does
    await ctx.aclose()
