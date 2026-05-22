"""NetworkSupervisorAgent — distributed multi-agent routing (T-211 / SPEC-021).

Routes tasks to remote LightAgent nodes by capability tag.  Falls back
gracefully to local execution when no suitable node is available or when
the remote call fails.

Authentication: A2A requests use HS256 JWTs signed with
``settings.jwt_secret_key``.  Remote nodes verify the token via
their own ``/api/v1/auth`` middleware.

Architecture:
    1. ``_load_nodes()`` reads ``config/network_nodes.yaml`` on startup.
    2. ``NetworkSupervisorAgent.delegate()`` picks the first enabled node
       whose ``capabilities`` list contains the requested capability.
    3. If no match or the HTTP call fails, falls back to local graph execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from prismal.agents.graph import get_compiled_graph
from prismal.agents.state import create_initial_state
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = get_logger("lightagent.agents.network_supervisor")

_DEFAULT_CONFIG = Path("config/network_nodes.yaml")

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class NetworkNode(BaseModel):
    """A remote LightAgent node in the distributed network.

    Attributes:
        name: Human-readable node label.
        url: Base URL (no trailing slash).
        capabilities: Task categories this node handles.
        enabled: Whether to include in routing.
        timeout_seconds: HTTP request timeout.
    """

    name: str
    url: str
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_seconds: int = 30


def _load_nodes(config_path: Path = _DEFAULT_CONFIG) -> list[NetworkNode]:
    """Load and parse the network node registry from YAML.

    Only returns nodes where ``enabled=True``.

    Args:
        config_path: Path to ``network_nodes.yaml``.

    Returns:
        List of enabled :class:`NetworkNode` instances.
    """
    if not config_path.exists():
        logger.info("network_nodes_config_missing", path=str(config_path))
        return []

    with config_path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    raw: list[dict[str, Any]] = data.get("nodes") or []
    nodes = [NetworkNode(**entry) for entry in raw]
    enabled = [n for n in nodes if n.enabled]
    logger.info("network_nodes_loaded", total=len(nodes), enabled=len(enabled))
    return enabled


def _make_a2a_jwt(node_url: str) -> str:
    """Create a short-lived HS256 JWT for A2A authentication.

    Args:
        node_url: URL of the target node (embedded in JWT audience).

    Returns:
        Signed JWT string.
    """
    from datetime import UTC, datetime, timedelta

    import prismal.core.config as _cfg

    settings = _cfg.get_settings()
    secret = settings.jwt_secret_key.get_secret_value()

    try:
        from jose import jwt

        payload = {
            "sub": "lightagent-node",
            "aud": node_url,
            "type": "a2a",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        }
        return cast("str", jwt.encode(payload, secret, algorithm="HS256"))
    except ImportError:
        logger.warning("jose_not_installed_a2a_jwt_empty")
        return ""


class NetworkSupervisorAgent:
    """Routes tasks to remote LightAgent nodes by capability.

    Args:
        nodes: List of remote nodes.  If None, loaded from YAML on init.
        config_path: Override for the YAML config path.
    """

    def __init__(
        self,
        nodes: list[NetworkNode] | None = None,
        config_path: Path | None = None,
    ) -> None:
        """Initialise with a list of nodes (or load from YAML)."""
        if nodes is not None:
            self._nodes = nodes
        else:
            self._nodes = _load_nodes(config_path or _DEFAULT_CONFIG)

    def _find_node(self, capability: str) -> NetworkNode | None:
        """Return the first node that advertises the given capability.

        Args:
            capability: Task category string (e.g. ``"research"``).

        Returns:
            Matching node or None.
        """
        for node in self._nodes:
            if capability in node.capabilities:
                return node
        return None

    async def delegate(
        self,
        task: str,
        capability: str,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Delegate a task to a remote node or fall back to local.

        Sends ``POST /api/v1/agent/chat`` to the target node with a
        bearer JWT.  On any failure (connection, timeout, status != 200)
        falls back to the local LangGraph graph.

        Args:
            task: The task description / user message.
            capability: Capability tag used for node selection.
            session_id: Session thread ID forwarded to remote node.

        Returns:
            Agent result dict (``{"messages": [...], ...}``).
        """
        otel = OTelManager()
        with otel.start_span("network_supervisor.delegate") as span:
            span.set_attribute("lightagent.capability", capability)

            node = self._find_node(capability)
            if node is not None and httpx is not None:
                try:
                    result = await self._call_remote(node, task, session_id)
                    span.set_attribute("lightagent.routed_to", node.name)
                    logger.info(
                        "network_delegate_remote",
                        node=node.name,
                        capability=capability,
                    )
                    return result
                except Exception as exc:
                    logger.warning(
                        "network_delegate_remote_failed",
                        node=node.name,
                        error=str(exc),
                    )

            span.set_attribute("lightagent.routed_to", "local")
            logger.info("network_delegate_local", capability=capability)
            return await self._run_local(task, session_id)

    async def _call_remote(self, node: NetworkNode, task: str, session_id: str) -> dict[str, Any]:
        """POST to remote node's /api/v1/agent/chat endpoint.

        Args:
            node: Target node.
            task: User message / task text.
            session_id: Session ID forwarded to remote.

        Returns:
            Parsed JSON response dict.

        Raises:
            Exception: On any HTTP or network error.
        """
        token = _make_a2a_jwt(node.url)
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async with httpx.AsyncClient(timeout=node.timeout_seconds) as client:
            resp = await client.post(
                f"{node.url}/api/v1/agent/chat",
                json={"message": task, "session_id": session_id},
                headers=headers,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return {
                "messages": [AIMessage(content=data.get("reply", ""))],
                "current_agent": "network_supervisor",
            }

    async def _run_local(self, task: str, session_id: str) -> dict[str, Any]:
        """Run the task on the local LangGraph graph.

        Args:
            task: User message / task text.
            session_id: Session thread ID.

        Returns:
            Agent result dict.
        """
        graph = get_compiled_graph()
        state = create_initial_state(session_id=session_id or "network-local")
        state["messages"] = [HumanMessage(content=task)]
        config: RunnableConfig = {"configurable": {"thread_id": session_id or "network-local"}}
        result = await graph.ainvoke(state, config)
        return cast("dict[str, Any]", result)


__all__ = ["NetworkNode", "NetworkSupervisorAgent", "_load_nodes"]
