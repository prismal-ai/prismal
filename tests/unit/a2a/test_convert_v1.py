"""v1 ⇄ internal (v0.3) codecs (SPEC-PRT-A2A-001/004, DD-PRT-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismal.a2a import convert
from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, A2ATask, AgentCard
from prismal.a2a.v1.types import (
    AgentCardV1,
    ArtifactV1,
    MessageV1,
    PartV1,
    Role,
    SendMessageRequest,
    TaskState,
    TaskStatusV1,
    TaskV1,
)

pytestmark = pytest.mark.unit

_FIX = Path(__file__).parents[2] / "fixtures" / "protocols" / "a2a_v1"


class TestParts:
    def test_text(self) -> None:
        internal = convert.part_to_internal(PartV1(text="hi"))
        assert internal == A2APart(kind="text", text="hi")
        assert convert.part_from_internal(internal) == PartV1(text="hi")

    def test_raw_file(self) -> None:
        part = PartV1(raw="aGk=", media_type="text/plain", filename="a.txt")
        internal = convert.part_to_internal(part)
        assert internal.kind == "file"
        assert internal.file == {"bytes": "aGk=", "mimeType": "text/plain", "name": "a.txt"}
        assert convert.part_from_internal(internal) == part

    def test_url_file(self) -> None:
        part = PartV1(url="https://x/f.pdf", media_type="application/pdf")
        internal = convert.part_to_internal(part)
        assert internal.file == {"uri": "https://x/f.pdf", "mimeType": "application/pdf"}
        assert convert.part_from_internal(internal) == part

    def test_dict_data(self) -> None:
        part = PartV1(data={"a": 1})
        internal = convert.part_to_internal(part)
        assert internal == A2APart(kind="data", data={"a": 1})
        assert convert.part_from_internal(internal) == part

    def test_scalar_data_is_wrapped(self) -> None:
        internal = convert.part_to_internal(PartV1(data=[1, 2]))
        assert internal.data == {"value": [1, 2]}

    def test_empty_internal_parts_degrade_to_empty_text(self) -> None:
        assert convert.part_from_internal(A2APart(kind="text")) == PartV1(text="")
        assert convert.part_from_internal(A2APart(kind="file", file={})) == PartV1(text="")
        assert convert.part_from_internal(A2APart(kind="data")) == PartV1(data={})


class TestMessages:
    def test_fixture_message_to_internal(self) -> None:
        raw = json.loads((_FIX / "send_message_request.json").read_text())["params"]
        req = SendMessageRequest.model_validate(raw)
        internal = convert.message_to_internal(req.message)
        assert isinstance(internal, A2AMessage)
        assert internal.role == "user"
        assert internal.message_id == "msg-1"
        assert [p.kind for p in internal.parts] == ["text", "data", "file", "file"]
        assert convert.message_text(req.message) == "Bill ACME 100 EUR"

    def test_internal_to_v1(self) -> None:
        msg = A2AMessage(role="agent", parts=[A2APart(kind="text", text="ok")], message_id="m")
        v1 = convert.message_from_internal(msg, context_id="c", task_id="t")
        assert v1 == MessageV1(
            message_id="m", role=Role.AGENT, parts=[PartV1(text="ok")], context_id="c", task_id="t"
        )

    def test_message_text_joins_text_parts(self) -> None:
        msg = MessageV1(
            message_id="m",
            role=Role.USER,
            parts=[PartV1(text="a"), PartV1(data={"x": 1}), PartV1(text="b")],
        )
        assert convert.message_text(msg) == "a\nb"


class TestStates:
    @pytest.mark.parametrize(
        ("legacy", "v1"),
        [
            ("submitted", TaskState.SUBMITTED),
            ("working", TaskState.WORKING),
            ("completed", TaskState.COMPLETED),
            ("failed", TaskState.FAILED),
            ("canceled", TaskState.CANCELED),
            ("input-required", TaskState.INPUT_REQUIRED),
        ],
    )
    def test_shared_states_round_trip(self, legacy: str, v1: TaskState) -> None:
        assert convert.state_to_v1(legacy) is v1
        assert convert.state_to_internal(v1) == legacy

    @pytest.mark.parametrize(
        ("v1", "internal"),
        [
            (TaskState.REJECTED, "failed"),
            (TaskState.AUTH_REQUIRED, "input-required"),
            (TaskState.UNSPECIFIED, "submitted"),
        ],
    )
    def test_v1_only_states_are_lossy(self, v1: TaskState, internal: str) -> None:
        assert convert.state_to_internal(v1) == internal

    def test_unknown_legacy_state(self) -> None:
        assert convert.state_to_v1("weird") is TaskState.UNSPECIFIED


class TestTasks:
    def _task(self) -> A2ATask:
        return A2ATask(
            id="t1",
            status="completed",
            history=[
                A2AMessage(role="user", parts=[A2APart(kind="text", text="q")], message_id="m1"),
                A2AMessage(role="agent", parts=[A2APart(kind="text", text="a")], message_id="m2"),
            ],
            artifacts=[A2AArtifact(artifact_id="a1", parts=[A2APart(kind="text", text="r")])],
        )

    def test_internal_to_v1(self) -> None:
        v1 = convert.task_from_internal(
            self._task(), context_id="c1", timestamp="2026-10-06T00:00:00Z"
        )
        assert v1.id == "t1"
        assert v1.context_id == "c1"
        assert v1.status == TaskStatusV1(
            state=TaskState.COMPLETED, timestamp="2026-10-06T00:00:00Z"
        )
        assert v1.artifacts == [ArtifactV1(artifact_id="a1", parts=[PartV1(text="r")])]
        assert v1.history is not None
        assert [m.context_id for m in v1.history] == ["c1", "c1"]
        assert [m.task_id for m in v1.history] == ["t1", "t1"]

    def test_include_artifacts_false_omits_them(self) -> None:
        v1 = convert.task_from_internal(self._task(), context_id="c", include_artifacts=False)
        assert v1.artifacts is None
        assert "artifacts" not in v1.model_dump(by_alias=True, exclude_none=True)

    def test_history_length_truncates_to_most_recent(self) -> None:
        v1 = convert.task_from_internal(self._task(), context_id="c", history_length=1)
        assert v1.history is not None
        assert [m.message_id for m in v1.history] == ["m2"]
        v0 = convert.task_from_internal(self._task(), context_id="c", history_length=0)
        assert v0.history is None

    def test_v1_to_internal(self) -> None:
        v1 = TaskV1(
            id="t",
            context_id="c",
            status=TaskStatusV1(state=TaskState.REJECTED),
            artifacts=[ArtifactV1(artifact_id="a", parts=[PartV1(text="x")])],
            history=[MessageV1(message_id="m", role=Role.USER, parts=[PartV1(text="q")])],
        )
        internal = convert.task_to_internal(v1)
        assert internal.status == "failed"
        assert internal.artifacts[0].parts[0].text == "x"
        assert internal.history[0].role == "user"

    def test_v1_to_internal_without_optional_lists(self) -> None:
        v1 = TaskV1(id="t", context_id="c", status=TaskStatusV1(state=TaskState.WORKING))
        internal = convert.task_to_internal(v1)
        assert internal.artifacts == []
        assert internal.history == []


class TestCard:
    def test_v1_card_to_internal_uses_v1_jsonrpc_interface(self) -> None:
        raw = json.loads((_FIX / "agent_card.json").read_text())
        internal = convert.card_to_internal(AgentCardV1.model_validate(raw))
        assert isinstance(internal, AgentCard)
        assert internal.url == "https://billing.acme/a2a/v1"
        assert internal.protocol_version == "1.0"
        assert internal.skills[0].id == "create_invoice"
        assert internal.skills[0].tags == ["billing", "invoice"]
        assert internal.skills[0].output_modes == ["application/json"]
        assert internal.capabilities == {"streaming": True}
        assert "oauth" in internal.security_schemes
        assert internal.provider == {"organization": "Acme Corp", "url": "https://acme.example"}

    def test_card_without_jsonrpc_v1_interface_falls_back_to_first(self) -> None:
        card = AgentCardV1.model_validate(
            {
                "name": "n",
                "description": "d",
                "supportedInterfaces": [
                    {"url": "https://g", "protocolBinding": "GRPC", "protocolVersion": "1.0"}
                ],
                "version": "1",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [{"id": "s", "name": "S", "description": "d"}],
            }
        )
        internal = convert.card_to_internal(card)
        assert internal.url == "https://g"
        assert internal.capabilities == {"streaming": False}
        assert internal.skills[0].input_modes == ["text/plain"]
        assert internal.security_schemes == {}
        assert internal.provider is None
