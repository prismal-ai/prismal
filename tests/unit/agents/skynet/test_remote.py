"""Unit tests for the Skynet S+ remote worker delegation (SPEC-SP-RMT-001).

``make_remote_send_fn`` delegates one order to a role's remote A2A agent through
the Phase-I ``A2AConnectionManager``; remote output is L1-sanitized before it
returns and the call is audited hash-first. All A2A collaborators are faked — no
network.
"""

from __future__ import annotations

from typing import Any

import pytest

from prismal.agents.skynet.remote import make_remote_send_fn
from prismal.agents.skynet.roles import SpecialistRole
from prismal.agents.skynet.types import SwarmOrder
from prismal.core.exceptions import A2AAgentUnavailable


class _FakePart:
    def __init__(self, text: str) -> None:
        self.kind = "text"
        self.text = text


class _FakeArtifact:
    def __init__(self, texts: list[str]) -> None:
        self.parts = [_FakePart(t) for t in texts]


class FakeClient:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.sent: list[Any] = []

    async def send_task(self, message: Any, *, skill_id: str | None = None) -> Any:
        self.sent.append(message)
        yield _FakeArtifact(self._chunks)


class FakeManager:
    """A2AConnectionManager stand-in — returns a client or denies by allowlist."""

    def __init__(self, client: FakeClient | None = None, *, deny: bool = False) -> None:
        self._client = client
        self._deny = deny
        self.requested: list[str] = []

    async def get_client(self, card_url: str) -> FakeClient:
        self.requested.append(card_url)
        if self._deny:
            raise A2AAgentUnavailable(card_url, "host not in outbound allowlist")
        assert self._client is not None
        return self._client


class SpySanitizer:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def sanitize(self, text: str) -> str:
        self.seen.append(text)
        return f"CLEAN:{text}"


class SpyAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


_ROLE = SpecialistRole(
    name="legal_review",
    remote_agent="https://legal.example.com/.well-known/agent-card.json",
)
_ORDER = SwarmOrder(order_id="ord-1", instruction="review this contract", role="legal_review")


async def test_send_fn_delegates_sanitizes_audits() -> None:
    """The send_fn delegates the order, sanitizes the concatenated text, and audits."""
    client = FakeClient(["part one", "part two"])
    manager = FakeManager(client)
    sanitizer = SpySanitizer()
    audit = SpyAudit()

    send_fn = make_remote_send_fn(manager=manager, sanitizer=sanitizer, audit=audit)
    result = await send_fn(_ROLE, _ORDER)

    # Delegated to the role's remote agent.
    assert manager.requested == [_ROLE.remote_agent]
    # Concatenated then sanitized (spy saw the raw joined text).
    assert sanitizer.seen == ["part one\npart two"]
    assert result == "CLEAN:part one\npart two"
    # The order instruction crossed to the remote as the message text.
    sent_text = client.sent[0].parts[0].text
    assert sent_text == "review this contract"
    # Audited hash-first (no raw content in the payload).
    events = [e for e, _ in audit.events]
    assert "a2a.outbound" in events
    payload = dict(audit.events[-1][1])
    assert payload.get("agent") == _ROLE.remote_agent
    assert "review this contract" not in str(payload)


async def test_denied_by_allowlist_contained() -> None:
    """A manager that denies by allowlist raises A2AAgentUnavailable, audited failed."""
    manager = FakeManager(deny=True)
    audit = SpyAudit()

    send_fn = make_remote_send_fn(manager=manager, sanitizer=SpySanitizer(), audit=audit)

    with pytest.raises(A2AAgentUnavailable):
        await send_fn(_ROLE, _ORDER)

    # A failed outbound is still audited.
    assert any(e == "a2a.outbound" for e, _ in audit.events)


class RaisingClient:
    async def send_task(self, message: Any, *, skill_id: str | None = None) -> Any:
        raise RuntimeError("connection reset")
        yield  # pragma: no cover - makes this an async generator


async def test_send_fn_wraps_generic_error_as_unavailable() -> None:
    """A non-A2A error from send_task is wrapped as A2AAgentUnavailable, audited."""
    manager = FakeManager(RaisingClient())  # type: ignore[arg-type]
    audit = SpyAudit()

    send_fn = make_remote_send_fn(manager=manager, sanitizer=SpySanitizer(), audit=audit)

    with pytest.raises(A2AAgentUnavailable):
        await send_fn(_ROLE, _ORDER)
    assert any(
        e == "a2a.outbound" and p.get("status") == "failed" for e, p in audit.events
    )


async def test_empty_artifacts_return_empty_string() -> None:
    """A remote answer with no text parts sanitizes to an empty string."""
    client = FakeClient([])
    manager = FakeManager(client)
    sanitizer = SpySanitizer()

    send_fn = make_remote_send_fn(manager=manager, sanitizer=sanitizer, audit=SpyAudit())
    result = await send_fn(_ROLE, _ORDER)

    assert result == ""
    assert sanitizer.seen == []  # no sanitize call for empty output


def test_make_remote_send_fn_builds_defaults() -> None:
    """With no collaborators injected, the factory wires the strict-deny defaults."""
    from prismal.core.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    send_fn = make_remote_send_fn(settings=settings)
    assert callable(send_fn)
