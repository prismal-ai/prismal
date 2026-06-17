"""Unit tests for the A2A domain types (Phase I — SPEC-A2A-001).

The serialized JSON MUST be A2A v0.3.x conformant: keys are camelCase
(``protocolVersion``, ``inputModes``, ``messageId``, …) while the Python
attributes stay snake_case. ``model_validate`` accepts either form.
"""

from __future__ import annotations

import pytest

from prismal.a2a.types import (
    A2AArtifact,
    A2AAuth,
    A2AMessage,
    A2APart,
    A2ATask,
    AgentCard,
    AgentSkill,
)

pytestmark = pytest.mark.unit


class TestA2APart:
    def test_text_part_round_trips(self) -> None:
        part = A2APart(kind="text", text="hello")
        dumped = part.model_dump(by_alias=True, exclude_none=True)
        assert dumped == {"kind": "text", "text": "hello"}
        assert A2APart.model_validate(dumped) == part

    def test_data_part_carries_dict(self) -> None:
        part = A2APart(kind="data", data={"k": 1})
        assert part.data == {"k": 1}

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            A2APart(kind="bogus")  # type: ignore[arg-type]


class TestAgentSkill:
    def test_serializes_io_modes_in_camel_case(self) -> None:
        skill = AgentSkill(
            id="research",
            name="Research",
            description="Deep research",
            tags=["search"],
        )
        dumped = skill.model_dump(by_alias=True)
        assert dumped["inputModes"] == ["text/plain"]
        assert dumped["outputModes"] == ["text/plain"]
        assert "input_modes" not in dumped

    def test_validate_accepts_camel_case_input(self) -> None:
        skill = AgentSkill.model_validate(
            {
                "id": "code",
                "name": "Code",
                "description": "Writes code",
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain", "text/markdown"],
            }
        )
        assert skill.output_modes == ["text/plain", "text/markdown"]


class TestAgentCard:
    def test_serializes_camel_case_and_defaults(self) -> None:
        card = AgentCard(
            name="prismal",
            description="Prismal agent",
            url="https://prismal.example.com/a2a",
            version="3.5.0",
            skills=[AgentSkill(id="research", name="Research", description="d")],
        )
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert dumped["protocolVersion"] == "0.3.0"
        assert dumped["capabilities"] == {"streaming": True}
        assert dumped["skills"][0]["id"] == "research"
        # optional did/provider omitted when None
        assert "did" not in dumped

    def test_round_trip_with_did_and_security(self) -> None:
        card = AgentCard(
            name="prismal",
            description="d",
            url="https://x/a2a",
            version="3.5.0",
            skills=[],
            security_schemes={"oauth2": {"type": "oauth2"}},
            did="did:web:prismal.example.com",
        )
        dumped = card.model_dump(by_alias=True, exclude_none=True)
        assert dumped["securitySchemes"] == {"oauth2": {"type": "oauth2"}}
        assert dumped["did"] == "did:web:prismal.example.com"
        assert AgentCard.model_validate(dumped) == card


class TestA2AMessage:
    def test_message_id_camel_case(self) -> None:
        msg = A2AMessage(role="user", parts=[A2APart(kind="text", text="hi")], message_id="m1")
        dumped = msg.model_dump(by_alias=True)
        assert dumped["messageId"] == "m1"
        assert dumped["role"] == "user"

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValueError):
            A2AMessage(role="system", parts=[], message_id="m")  # type: ignore[arg-type]


class TestA2AArtifact:
    def test_artifact_id_camel_case(self) -> None:
        art = A2AArtifact(artifact_id="a1", parts=[A2APart(kind="text", text="out")])
        dumped = art.model_dump(by_alias=True)
        assert dumped["artifactId"] == "a1"


class TestA2ATask:
    def test_default_status_and_collections(self) -> None:
        task = A2ATask(id="t1", status="submitted")
        assert task.history == []
        assert task.artifacts == []

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError):
            A2ATask(id="t", status="done")  # type: ignore[arg-type]

    def test_round_trip_with_artifacts(self) -> None:
        task = A2ATask(
            id="t1",
            status="completed",
            artifacts=[A2AArtifact(artifact_id="a", parts=[A2APart(kind="text", text="r")])],
        )
        assert A2ATask.model_validate(task.model_dump(by_alias=True)) == task


class TestA2AAuth:
    def test_secret_not_serialized_by_default_repr(self) -> None:
        auth = A2AAuth(
            scheme="oauth2_client_credentials",
            token_url="https://auth/token",
            client_id="cid",
            client_secret="shhh",
        )
        # secret must never appear in repr
        assert "shhh" not in repr(auth)

    def test_default_scheme_none(self) -> None:
        auth = A2AAuth()
        assert auth.scheme == "none"
