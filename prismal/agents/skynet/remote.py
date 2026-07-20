"""Remote worker delegation over A2A for the Skynet swarm (SPEC-SP-RMT-001, S+3).

A role whose ``remote_agent`` is set runs its whole sub-order on a remote A2A
agent instead of in-process (DD-SP-005).  :func:`make_remote_send_fn` returns
the injected ``send_fn`` the :class:`~prismal.agents.skynet.worker.SwarmWorker`
calls for such a role.  It reuses Phase I's :class:`A2AConnectionManager`
(allowlist + pool + strict deny-all) — no new client code — and mirrors
``A2AAgentNode.ainvoke``:

    order.instruction ──► A2AMessage ──► client.send_task (SSE artifacts)
        │ concat text parts
        ▼
    InputSanitizer.sanitize(text)  ──►  audit a2a.outbound (hash-first)

**Remote output is untrusted** (DD-SP-008): it is L1-sanitized before it returns
so it never reaches ``AgentState`` unsanitized.  A denied / unreachable / timed-
out agent raises :class:`A2AAgentUnavailable`, which the worker catches and turns
into a contained ``WorkerResult(success=False, remote=True)``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

from prismal.core.exceptions import A2AAgentUnavailable
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.a2a.client import A2AConnectionManager
    from prismal.agents.skynet.roles import SpecialistRole
    from prismal.agents.skynet.types import SwarmOrder
    from prismal.agents.skynet.worker import RemoteSendFn
    from prismal.core.config import Settings
    from prismal.security.audit import AuditLogger
    from prismal.security.sanitizer import InputSanitizer

logger = get_logger("prismal.agents.skynet.remote")


def make_remote_send_fn(
    *,
    manager: A2AConnectionManager | None = None,
    sanitizer: InputSanitizer | None = None,
    audit: AuditLogger | None = None,
    settings: Settings | None = None,
) -> RemoteSendFn:
    """Return a ``send_fn`` delegating one :class:`SwarmOrder` over A2A (S+3).

    Args:
        manager: Phase-I :class:`A2AConnectionManager` (allowlist + pool).
            ``None`` builds one from ``settings.skynet_remote_allowlist``
            (strict deny-all when the allowlist is empty).
        sanitizer: :class:`InputSanitizer` applied to the remote output before
            it returns.  ``None`` builds the default.
        audit: Audit logger for the hash-first ``a2a.outbound`` record.
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.

    Returns:
        An async ``send_fn(role, order) -> str`` (raises
        :class:`A2AAgentUnavailable` on denial / failure).
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()

    resolved_manager = manager if manager is not None else _default_manager(settings)
    resolved_sanitizer = sanitizer if sanitizer is not None else _default_sanitizer()
    resolved_audit = audit if audit is not None else _default_audit()

    async def send_fn(role: SpecialistRole, order: SwarmOrder) -> str:
        """Delegate *order* to *role*'s remote A2A agent; return sanitized text."""
        from prismal.a2a.types import A2AMessage, A2APart

        agent = role.remote_agent or ""
        message = A2AMessage(
            role="user",
            parts=[A2APart(kind="text", text=order.instruction)],
            message_id=uuid.uuid4().hex,
        )
        try:
            client = await resolved_manager.get_client(agent)
            texts: list[str] = []
            async for artifact in client.send_task(message):
                for part in artifact.parts:
                    if part.kind == "text" and part.text:
                        texts.append(part.text)
        except A2AAgentUnavailable:
            resolved_audit.log_event(
                "a2a.outbound",
                {"agent": agent, "order_hash": _sha256(order.instruction), "status": "failed"},
            )
            raise
        except Exception as exc:  # unreachable / timeout / malformed → contained upstream
            resolved_audit.log_event(
                "a2a.outbound",
                {"agent": agent, "order_hash": _sha256(order.instruction), "status": "failed"},
            )
            raise A2AAgentUnavailable(agent, f"remote worker failed: {exc}") from exc

        answer = resolved_sanitizer.sanitize("\n".join(texts)) if texts else ""
        resolved_audit.log_event(
            "a2a.outbound",
            {
                "agent": agent,
                "order_hash": _sha256(order.instruction),
                "status": "completed",
                "artifacts": len(texts),
            },
        )
        return answer

    return send_fn


def _default_manager(settings: Settings) -> A2AConnectionManager:
    """Build the default allowlist-gated connection manager (strict deny-all)."""
    from prismal.a2a.client import A2AConnectionManager

    return A2AConnectionManager(allowlist=settings.skynet_remote_allowlist, strict=True)


def _default_sanitizer() -> InputSanitizer:
    from prismal.security.sanitizer import InputSanitizer

    return InputSanitizer()


def _default_audit() -> AuditLogger:
    from prismal.security.audit import AuditLogger

    return AuditLogger()


def _sha256(text: str) -> str:
    """Return the SHA-256 hex digest of *text* (audit hash-first records)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["make_remote_send_fn"]
