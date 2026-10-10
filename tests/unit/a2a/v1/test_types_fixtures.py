"""A2A v1 wire types — official-shaped fixtures round-trip (SPEC-PRT-A2A-001)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from prismal.a2a.v1 import constants as c
from prismal.a2a.v1.types import (
    AgentCardV1,
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResult,
    MessageV1,
    PartV1,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    TaskState,
)

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parents[3] / "fixtures" / "protocols" / "a2a_v1"


def _load(name: str) -> Any:
    return json.loads((_FIX / name).read_text())


def _round_trip(model: type[BaseModel], raw: dict[str, Any]) -> dict[str, Any]:
    return model.model_validate(raw).model_dump(by_alias=True, exclude_none=True, mode="json")


@pytest.mark.parametrize(
    ("fixture", "model", "path"),
    [
        ("agent_card.json", AgentCardV1, None),
        ("send_message_request.json", SendMessageRequest, "params"),
        ("send_message_response.json", SendMessageResponse, "result"),
        ("list_tasks_request.json", ListTasksRequest, "params"),
        ("list_tasks_response.json", ListTasksResult, "result"),
        ("get_task_request.json", GetTaskRequest, "params"),
        ("cancel_task_request.json", CancelTaskRequest, "params"),
    ],
)
def test_fixture_round_trips_without_field_loss(
    fixture: str, model: type[BaseModel], path: str | None
) -> None:
    raw = _load(fixture)
    body = raw[path] if path else raw
    assert _round_trip(model, body) == body


def test_stream_events_round_trip() -> None:
    for event in _load("stream_events.json"):
        assert _round_trip(StreamResponse, event["result"]) == event["result"]


def test_unknown_fields_are_preserved() -> None:
    raw = {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "x"}], "futureField": 1}
    assert _round_trip(MessageV1, raw) == raw


def test_python_names_are_snake_case() -> None:
    req = SendMessageRequest.model_validate(_load("send_message_request.json")["params"])
    assert req.tenant == "acme"
    assert req.message.message_id == "msg-1"
    assert req.message.role == c.ROLE_USER
    assert req.message.parts[0].media_type == "text/plain"
    assert req.message.reference_task_ids == ["task-0"]


def test_card_security_scheme_oneof() -> None:
    card = AgentCardV1.model_validate(_load("agent_card.json"))
    oauth = card.security_schemes["oauth"].oauth2_security_scheme  # type: ignore[index]
    assert oauth is not None
    ac = oauth.flows.authorization_code
    assert ac is not None and ac.pkce_required is True
    device = card.security_schemes["device"].oauth2_security_scheme  # type: ignore[index]
    assert device is not None and device.flows.device_code is not None
    assert device.flows.device_code.device_authorization_url == "https://idp.acme/device"
    assert card.supported_interfaces[0].tenant == "acme"
    assert card.supported_interfaces[1].protocol_binding == c.BINDING_HTTP_JSON


class TestPartOneof:
    @pytest.mark.parametrize(
        "raw",
        [{"text": "a"}, {"raw": "aGk="}, {"url": "https://x/y"}, {"data": {"a": 1}}],
    )
    def test_exactly_one_content_field_is_valid(self, raw: dict[str, Any]) -> None:
        assert PartV1.model_validate(raw).model_dump(by_alias=True, exclude_none=True) == raw

    @pytest.mark.parametrize("raw", [{}, {"text": "a", "data": {}}, {"mediaType": "x"}])
    def test_zero_or_many_content_fields_rejected(self, raw: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            PartV1.model_validate(raw)

    def test_kind_field_is_not_emitted(self) -> None:
        dumped = PartV1(text="x").model_dump(by_alias=True, exclude_none=True)
        assert "kind" not in dumped


class TestEnumsAndConstants:
    def test_task_state_values(self) -> None:
        assert {s.value for s in TaskState} == {
            "TASK_STATE_UNSPECIFIED",
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_INPUT_REQUIRED",
            "TASK_STATE_REJECTED",
            "TASK_STATE_AUTH_REQUIRED",
        }

    def test_state_maps_round_trip_for_shared_states(self) -> None:
        for legacy, v1 in c.LEGACY_TO_V1_STATE.items():
            assert c.V1_TO_LEGACY_STATE[v1] == legacy

    def test_v1_only_states_have_lossy_legacy_mapping(self) -> None:
        assert c.V1_TO_LEGACY_STATE["TASK_STATE_REJECTED"] == "rejected"
        assert c.V1_TO_LEGACY_STATE["TASK_STATE_AUTH_REQUIRED"] == "auth-required"
        assert c.V1_TO_LEGACY_STATE["TASK_STATE_UNSPECIFIED"] == "unknown"

    def test_method_families_do_not_overlap(self) -> None:
        assert not (c.V1_METHODS & c.LEGACY_METHODS)
        assert c.METHOD_LIST_TASKS in c.V1_METHODS
        assert c.LEGACY_METHOD_SEND in c.LEGACY_METHODS

    def test_versions_and_codes(self) -> None:
        assert c.A2A_V1_PROTOCOL_VERSION == "1.0"
        assert c.A2A_LEGACY_PROTOCOL_VERSION == "0.3"
        assert c.VERSION_HEADER == "A2A-Version"
        assert c.ERR_TASK_NOT_FOUND == -32001
        assert c.ERR_VERSION_NOT_SUPPORTED == -32009
        assert c.CONTENT_TYPE == "application/a2a+json"
        assert c.LIST_TASKS_MAX_PAGE_SIZE == 100
        assert c.LIST_TASKS_DEFAULT_PAGE_SIZE == 50


def test_list_tasks_result_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        ListTasksResult.model_validate({"tasks": []})


def test_list_tasks_request_defaults() -> None:
    req = ListTasksRequest()
    assert req.page_size is None
    assert req.include_artifacts is None
