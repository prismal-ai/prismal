"""Decision keys never leak (Phase DEC — SPEC-DEC-CFG-001, DEC2-06).

The Jev / laya-serve key must not appear in logs, audit records, URLs, reprs
or ``os.environ`` — on success *and* on every failure path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from prismal.core.config import Settings
from prismal.decisions.adapters.factory import build_backend
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import GatePolicy
from tests.unit.decisions.contract import QUESTIONS

SECRET = "sk-jev-DO-NOT-LEAK-7f3a"


class Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


@pytest.mark.parametrize("status", [200, 401, 500])
async def test_key_absent_everywhere(status: int, tmp_path: Path) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(status, json={"model": "other", "answers": {}})

    adapter = SystemOneHttpDecisionAdapter(
        base_url="https://api.typesafe.ai/v1",
        api_key=SecretStr(SECRET),
        model="jev-1.13.0",
        transport=httpx.MockTransport(handler),
    )
    audit = Audit()
    cascade = DecisionCascade([adapter], audit=audit)
    state = DecisionStateBuilder().add_untrusted("user", "hello there", source="user").build()
    before = dict(os.environ)
    with capture_logs() as logs:
        await cascade.decide(state, QUESTIONS, policy=GatePolicy(), integration="route")
    assert dict(os.environ) == before
    for blob in (str(logs), str(audit.events), str(urls), repr(adapter), repr(cascade)):
        assert SECRET not in blob


def test_settings_and_factory_never_expose_key() -> None:
    s = Settings(jev_api_key=SECRET, laya_api_key=SECRET)
    before = dict(os.environ)
    backend = build_backend("jev", s)
    assert dict(os.environ) == before
    assert SECRET not in repr(s)
    assert SECRET not in repr(backend)
    assert SECRET not in str(s.model_dump())
