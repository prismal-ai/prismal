"""A2A (Agent2Agent) interoperability — Phase I.

Bidirectional A2A interop reusing the extension surface (Phase X), the
``ToolProviderPort`` (Phase Y), the composition root (Phase R) and the agent
identity DID (Phase IDN). Opt-in via ``settings.a2a_enabled`` (default off);
HTTP/SSE deps ship under the ``[a2a]`` extra.

- **Inbound** — :class:`A2AServerHandler` exposes the prismal graph as an A2A
  agent (JSON-RPC + SSE); :func:`build_agent_card` publishes the Agent Card.
- **Outbound** — :class:`A2AClient` / :class:`A2AAgentNode` delegate to remote
  agents; :class:`A2AToolProvider` surfaces remote skills as tools.

Everything that crosses the A2A trust boundary is treated as untrusted and is
sanitized + audited before it touches ``AgentState``.
"""

from __future__ import annotations

from prismal.a2a.card import build_agent_card, clear_agent_card_cache
from prismal.a2a.client import A2AAgentNode, A2AClient, A2AConnectionManager
from prismal.a2a.provider import A2AToolProvider
from prismal.a2a.server import A2AServerHandler, AuthContext
from prismal.a2a.types import (
    PROTOCOL_VERSION,
    A2AArtifact,
    A2AAuth,
    A2AMessage,
    A2APart,
    A2ATask,
    AgentCard,
    AgentSkill,
)

__all__ = [
    "PROTOCOL_VERSION",
    "A2AAgentNode",
    "A2AArtifact",
    "A2AAuth",
    "A2AClient",
    "A2AConnectionManager",
    "A2AMessage",
    "A2APart",
    "A2AServerHandler",
    "A2ATask",
    "A2AToolProvider",
    "AgentCard",
    "AgentSkill",
    "AuthContext",
    "build_agent_card",
    "clear_agent_card_cache",
]
