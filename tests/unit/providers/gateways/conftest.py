"""Shared fixtures for gateway tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from prismal.core.config import Settings


class NoNetwork:
    """An ``httpx`` transport that fails the test on any request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError(f"unexpected network I/O: {request.method} {request.url}")


@pytest.fixture
def no_network() -> Iterator[tuple[NoNetwork, httpx.AsyncClient]]:
    guard = NoNetwork()
    client = httpx.AsyncClient(transport=httpx.MockTransport(guard.handler))
    yield guard, client


class FakeOTel:
    """Records counter increments (patched over ``OTelManager``)."""

    counts: dict[str, list[dict[str, Any]]] = {}

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        FakeOTel.counts.setdefault(metric, []).append(dict(attributes or {}))

    def record_histogram(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None:
        FakeOTel.counts.setdefault(metric, []).append({"value": value, **(attributes or {})})

    def set_gauge(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None:
        FakeOTel.counts.setdefault(metric, []).append({"value": value, **(attributes or {})})


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> type[FakeOTel]:
    FakeOTel.counts = {}
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager", FakeOTel)
    return FakeOTel


def gw_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"llm_gateways_enabled": True}
    base.update(kw)
    return Settings(**base)
