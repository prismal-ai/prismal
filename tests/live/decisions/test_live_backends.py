"""Live decision backends (Phase DEC — DEC0-08 / DEC6-04). Opt-in: ``-m live_api`` + keys.

They spend real requests (Jev) or need the ``[laya]`` extra plus model weights,
so they are skipped unless configured. Never run by default.
"""

from __future__ import annotations

import importlib.util

import pytest

from prismal.core.config import get_settings
from prismal.decisions.adapters.factory import build_backend
from prismal.decisions.cli import run_bench

pytestmark = pytest.mark.live_api


@pytest.mark.skipif(get_settings().jev_api_key is None, reason="PRISMAL_JEV_API_KEY not set")
async def test_jev_smoke_latency() -> None:
    backend = build_backend("jev", get_settings())
    assert backend is not None
    try:
        report = await run_bench(backend, n=1)
    finally:
        await backend.aclose()
    assert report["abstain_ratio"] < 1.0
    assert float(report["p95_ms"]) < 5_000  # type: ignore[arg-type]


@pytest.mark.skipif(importlib.util.find_spec("laya") is None, reason="laya extra not installed")
async def test_laya_local_smoke_latency() -> None:
    backend = build_backend("laya", get_settings().model_copy(update={"laya_mode": "local"}))
    assert backend is not None
    try:
        report = await run_bench(backend, n=1)
    finally:
        await backend.aclose()
    assert report["abstain_ratio"] < 1.0
