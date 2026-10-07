"""Outbound A2A — client, connection manager, agent node (Phase I — SPEC-A2A-004).

Consumes remote A2A agents:

- :class:`A2AClient` discovers an Agent Card, sends a task over JSON-RPC
  (``message/send``) and streams the result artifacts over SSE, handling auth.
- :class:`A2AConnectionManager` enforces the outbound allowlist (fnmatch
  wildcards), pools clients per endpoint, and is the mirror of
  ``mcp/connection.py``.
- :class:`A2AAgentNode` wraps a remote agent as a prismal graph node
  (``@prismal_node``) — the A2A analogue of ``LangChainRunnableAdapter``.

**Everything remote is untrusted**: artifact text is passed through
:class:`~prismal.security.InputSanitizer` before it reaches ``AgentState``, and
every delegation is audited (without content).

HTTP/SSE imports are deferred so the base install need not carry the ``[a2a]``
extra unless A2A is used.

Phase PRT (SPEC-PRT-A2A-006): ``A2AClient(protocol="auto"|"0.3"|"1.0",
tenant=, prompter=)``. ``auto`` runs :func:`~prismal.a2a.negotiation.select_outbound`
once on the discovered card (cached with it); a v1 peer gets v1 envelopes
(``SendStreamingMessage`` / ``CancelTask`` / ``ListTasks`` with the
``A2A-Version`` header and ``tenant``) while :meth:`A2AClient.discover` still
returns the internal :class:`AgentCard` projection, so
:class:`~prismal.a2a.provider.A2AToolProvider` and :class:`A2AAgentNode` work
unchanged. Interactive OAuth is selected from the v1 card's security schemes
(:func:`~prismal.a2a.oauth.select_oauth_flow`).
"""

from __future__ import annotations

import fnmatch
import json
import uuid
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from prismal.a2a import convert
from prismal.a2a.negotiation import count_a2a_request, select_interface_url, select_outbound
from prismal.a2a.types import A2AArtifact, A2AAuth, A2AMessage, A2APart, AgentCard
from prismal.a2a.v1 import constants as v1c
from prismal.a2a.v1.types import AgentCardV1, ListTasksResult, StreamResponse
from prismal.core.exceptions import A2AAgentUnavailable, A2AVersionError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langchain_core.messages import BaseMessage

    from prismal.a2a.oauth import AuthorizationPrompter, DeviceCodeFlow, PkceAuthorizationCodeFlow
    from prismal.a2a.v1.types import ListTasksRequest
    from prismal.agents.extension import NodeFn, SecurityLevel
    from prismal.agents.extension.ports import CredentialVaultPort
    from prismal.agents.state import AgentState

ClientProtocol = Literal["auto", "0.3", "1.0"]

logger = get_logger(__name__)

_WELL_KNOWN_SUFFIX = "/.well-known/agent-card.json"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})


def _host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _allowed(host: str, allowlist: list[str], *, strict: bool) -> bool:
    """Match *host* against fnmatch *allowlist* patterns (Phase I security)."""
    if not allowlist:
        return not strict  # empty + strict ⇒ deny-all
    return any(fnmatch.fnmatch(host, pat) for pat in allowlist)


class A2AClient:
    """Talks A2A JSON-RPC/SSE to one remote agent (SPEC-A2A-004)."""

    def __init__(
        self,
        card_or_url: str | AgentCard,
        *,
        auth: A2AAuth | None = None,
        max_retries: int = 2,
        timeout_s: float = 30.0,
        protocol: ClientProtocol = "auto",
        tenant: str | None = None,
        prompter: AuthorizationPrompter | None = None,
        vault: CredentialVaultPort | None = None,
        redirect_uri: str | None = None,
        allowlist: Sequence[str] | None = None,
        strict: bool = False,
        local_org_id: str | None = None,
        principal: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        """``tenant`` / the card's interface tenant only fill the outbound A2A
        ``tenant`` param; the OAuth vault partition is ``local_org_id`` (+
        ``principal``) from the local authenticated context — never remote data."""
        if protocol not in ("auto", "0.3", "1.0"):
            raise ValueError(f"unsupported A2A protocol: {protocol!r}")
        self._protocol: ClientProtocol = protocol
        self._tenant = tenant
        self._prompter = prompter
        self._vault = vault
        self._redirect_uri = redirect_uri
        self._allowlist = list(allowlist) if allowlist else None
        self._strict = strict
        self._local_org_id = local_org_id
        self._principal = principal
        self._version: v1c.A2AProtocolVersion | None = None
        self._card_v1: AgentCardV1 | None = None
        self._card_tenant: str | None = None
        self._oauth_flow: PkceAuthorizationCodeFlow | DeviceCodeFlow | None = None
        self._oauth_resolved = False
        if isinstance(card_or_url, AgentCard):
            self._card: AgentCard | None = card_or_url
            self._card_url = card_or_url.url
        else:
            self._card = None
            self._card_url = card_or_url
        self._auth = auth or A2AAuth()
        self._max_retries = max(1, max_retries)
        self._timeout_s = timeout_s
        self._client: Any = http_client  # httpx.AsyncClient (deferred unless injected)
        self._oauth_token: str | None = None

    def _http(self) -> Any:
        """Lazily build the shared httpx client (deferred import)."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def protocol_version(self) -> v1c.A2AProtocolVersion | None:
        """The negotiated outbound version (``None`` until the card is known)."""
        return self._version

    def _pick_version(self, raw: dict[str, Any]) -> v1c.A2AProtocolVersion:
        if self._protocol == "auto":
            return select_outbound(raw)
        chosen = select_outbound(raw, preferred=self._protocol)
        if chosen != self._protocol:
            raise A2AVersionError(
                f"remote agent does not advertise A2A {self._protocol} over JSON-RPC"
            )
        return chosen

    def _adopt_card(self, raw: dict[str, Any]) -> AgentCard:
        """Negotiate on a raw card and project it onto the internal model (cached)."""
        version = self._pick_version(raw)
        if "supportedInterfaces" in raw and "url" not in raw:
            card_v1 = AgentCardV1.model_validate(raw)
            url, tenant = select_interface_url(raw, version)
            card = convert.card_to_internal(card_v1).model_copy(
                update={"url": url or "", "protocol_version": version}
            )
            self._card_v1 = card_v1
            self._card_tenant = tenant
        else:
            card = AgentCard.model_validate(raw)
        self._version = version
        self._card = card
        return card

    async def _ensure_card(self) -> AgentCard:
        card = await self.discover()
        if self._version is None:
            self._version = self._pick_version(card.model_dump(by_alias=True))
        return card

    async def discover(self) -> AgentCard:
        """Fetch and cache the remote Agent Card (GET well-known)."""
        if self._card is not None:
            return self._card
        import httpx

        url = self._card_url
        if not url.endswith(_WELL_KNOWN_SUFFIX) and "/.well-known/" not in url:
            url = url.rstrip("/") + _WELL_KNOWN_SUFFIX
        last_exc: Exception | None = None
        for _ in range(self._max_retries):
            try:
                resp = await self._http().get(url)
                resp.raise_for_status()
                return self._adopt_card(resp.json())
            except httpx.HTTPError as exc:
                last_exc = exc
        raise A2AAgentUnavailable(self._card_url, f"discovery failed: {last_exc!r}")

    async def _auth_headers(self) -> dict[str, str]:
        if self._auth.scheme == "bearer" and self._auth.bearer_token is not None:
            return {"Authorization": f"Bearer {self._auth.bearer_token.get_secret_value()}"}
        if self._auth.scheme == "oauth2_client_credentials":
            token = await self._fetch_oauth_token()
            return {"Authorization": f"Bearer {token}"}
        flow = self._card_oauth_flow()
        if flow is not None:
            secret = await flow.access_token()
            return {"Authorization": f"Bearer {secret.get_secret_value()}"}
        return {}

    def _card_oauth_flow(self) -> PkceAuthorizationCodeFlow | DeviceCodeFlow | None:
        """Interactive OAuth required by a v1 card (``securityRequirements``), cached."""
        if self._oauth_resolved:
            return self._oauth_flow
        card = self._card_v1
        if card is None or self._auth.scheme != "none" or not card.security_requirements:
            return None
        from prismal.a2a.oauth import select_oauth_flow

        self._oauth_flow = select_oauth_flow(
            card,
            auth=self._auth,
            prompter=self._prompter,
            redirect_uri=self._redirect_uri,
            vault=self._vault,
            org_id=self._local_org_id,
            principal=self._principal,
            http_client=self._http(),
            agent_url=self._card_url,
            allowlist=self._allowlist,
            strict=self._strict,
        )
        self._oauth_resolved = True
        return self._oauth_flow

    def _effective_tenant(self) -> str | None:
        return self._tenant if self._tenant is not None else self._card_tenant

    def _v1_params(self, params: dict[str, Any]) -> dict[str, Any]:
        tenant = self._effective_tenant()
        return {**params, "tenant": tenant} if tenant is not None else params

    @staticmethod
    def _count(method: str, outcome: str, version: str = v1c.A2A_V1_PROTOCOL_VERSION) -> None:
        count_a2a_request(
            protocol_version=version, direction="outbound", method=method, outcome=outcome
        )

    async def _fetch_oauth_token(self) -> str:
        if self._oauth_token is not None:
            return self._oauth_token
        import httpx

        if not self._auth.token_url:
            raise A2AAgentUnavailable(self._card_url, "oauth2 auth requires token_url")
        data = {
            "grant_type": "client_credentials",
            "client_id": self._auth.client_id or "",
            "client_secret": (
                self._auth.client_secret.get_secret_value() if self._auth.client_secret else ""
            ),
        }
        try:
            resp = await self._http().post(self._auth.token_url, data=data)
            resp.raise_for_status()
            self._oauth_token = str(resp.json()["access_token"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise A2AAgentUnavailable(self._card_url, f"oauth token failed: {exc!r}") from exc
        return self._oauth_token

    async def send_task(
        self, message: A2AMessage, *, skill_id: str | None = None
    ) -> AsyncIterator[A2AArtifact]:
        """Send a task and stream A2A artifacts over SSE (v1 or ``message/send``)."""
        import httpx
        from httpx_sse import aconnect_sse

        card = await self._ensure_card()
        if self._version == "1.0":
            async for v1_artifact in self._send_task_v1(card, message, skill_id=skill_id):
                yield v1_artifact
            return
        task_id = uuid.uuid4().hex
        params: dict[str, Any] = {"message": message.model_dump(by_alias=True), "taskId": task_id}
        if skill_id is not None:
            params["skillId"] = skill_id
        body = {"jsonrpc": "2.0", "id": task_id, "method": "message/send", "params": params}
        headers = await self._auth_headers()
        headers["Accept"] = "text/event-stream"

        try:
            async with aconnect_sse(
                self._http(), "POST", card.url, json=body, headers=headers
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    artifact, done = self._parse_event(sse.data)
                    if artifact is not None:
                        yield artifact
                    if done:
                        break
        except httpx.HTTPError as exc:
            raise A2AAgentUnavailable(self._card_url, f"send_task failed: {exc!r}") from exc

    async def _send_task_v1(
        self, card: AgentCard, message: A2AMessage, *, skill_id: str | None
    ) -> AsyncIterator[A2AArtifact]:
        import httpx
        from httpx_sse import aconnect_sse

        method = v1c.METHOD_SEND_STREAMING_MESSAGE
        params: dict[str, Any] = {
            "message": convert.message_from_internal(message).model_dump(
                by_alias=True, exclude_none=True, mode="json"
            )
        }
        if skill_id is not None:
            params["metadata"] = {"skillId": skill_id}
        rid = uuid.uuid4().hex
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": self._v1_params(params)}
        headers = await self._auth_headers()
        headers["Accept"] = "text/event-stream"
        headers[v1c.VERSION_HEADER] = v1c.A2A_V1_PROTOCOL_VERSION
        try:
            async with aconnect_sse(
                self._http(), "POST", card.url, json=body, headers=headers
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    artifacts, done = self._parse_v1_event(sse.data)
                    for artifact in artifacts:
                        yield artifact
                    if done:
                        break
        except httpx.HTTPError as exc:
            self._count(method, "error")
            raise A2AAgentUnavailable(self._card_url, f"send_task failed: {exc!r}") from exc
        except A2AAgentUnavailable:
            self._count(method, "error")
            raise
        self._count(method, "ok")

    @staticmethod
    def _parse_v1_event(data: str) -> tuple[list[A2AArtifact], bool]:
        """One v1 ``StreamResponse`` → (artifacts, terminal?). Tolerant of bad events."""
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return [], False
        if isinstance(obj, dict) and "error" in obj:
            raise A2AAgentUnavailable("remote", f"jsonrpc error: {obj['error']}")
        result = obj.get("result") if isinstance(obj, dict) else None
        if not isinstance(result, dict):
            return [], False
        try:
            event = StreamResponse.model_validate(result)
        except Exception:
            return [], False
        if event.artifact_update is not None:
            return [convert.artifact_to_internal(event.artifact_update.artifact)], False
        if event.status_update is not None:
            return [], event.status_update.status.state.value in v1c.TERMINAL_STATES
        if event.task is not None:
            arts = [convert.artifact_to_internal(a) for a in event.task.artifacts or []]
            return arts, event.task.status.state.value in v1c.TERMINAL_STATES
        if event.message is not None:
            msg = convert.message_to_internal(event.message)
            return [A2AArtifact(artifact_id=event.message.message_id, parts=msg.parts)], True
        return [], False

    async def list_tasks(self, params: ListTasksRequest) -> ListTasksResult:
        """v1 ``ListTasks`` (tenant injected). A v0.3 peer ⇒ :class:`A2AVersionError`, no I/O."""
        import httpx

        if self._protocol == "0.3":
            raise A2AVersionError("ListTasks requires an A2A 1.0 peer")
        if self._card is not None:  # known peer: decide without any I/O
            if self._version is None:
                self._version = self._pick_version(self._card.model_dump(by_alias=True))
            if self._version != "1.0":
                raise A2AVersionError("ListTasks requires an A2A 1.0 peer")
        card = await self._ensure_card()
        if self._version != "1.0":
            raise A2AVersionError("ListTasks requires an A2A 1.0 peer")
        method = v1c.METHOD_LIST_TASKS
        payload = params.model_dump(by_alias=True, exclude_none=True, mode="json")
        body = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": self._v1_params(payload),
        }
        headers = await self._auth_headers()
        headers[v1c.VERSION_HEADER] = v1c.A2A_V1_PROTOCOL_VERSION
        try:
            resp = await self._http().post(card.url, json=body, headers=headers)
            resp.raise_for_status()
            obj = resp.json()
            if "error" in obj:
                raise A2AAgentUnavailable(self._card_url, f"jsonrpc error: {obj['error']}")
            result = ListTasksResult.model_validate(obj["result"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self._count(method, "error")
            raise A2AAgentUnavailable(self._card_url, f"list_tasks failed: {exc!r}") from exc
        except A2AAgentUnavailable:
            self._count(method, "error")
            raise
        self._count(method, "ok")
        return result

    @staticmethod
    def _parse_event(data: str) -> tuple[A2AArtifact | None, bool]:
        """Parse one SSE payload → (artifact?, terminal?). Tolerant of shapes."""
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None, False
        if isinstance(obj, dict) and "error" in obj:
            raise A2AAgentUnavailable("remote", f"jsonrpc error: {obj['error']}")
        result = obj.get("result", obj) if isinstance(obj, dict) else {}
        if not isinstance(result, dict):
            return None, False
        if result.get("status") in _TERMINAL_STATUSES:
            return None, True
        raw_artifact = result.get("artifact") if "artifact" in result else result
        if isinstance(raw_artifact, dict) and (
            "artifactId" in raw_artifact or "parts" in raw_artifact
        ):
            try:
                return A2AArtifact.model_validate(raw_artifact), False
            except Exception:  # pragma: no cover - malformed remote artifact
                return None, False
        return None, False

    async def cancel(self, task_id: str) -> None:
        """Best-effort ``tasks/cancel`` for a running task."""
        import httpx

        card = await self._ensure_card()
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        try:
            headers = await self._auth_headers()
            if self._version == "1.0":
                body["method"] = v1c.METHOD_CANCEL_TASK
                body["params"] = self._v1_params({"id": task_id})
                headers[v1c.VERSION_HEADER] = v1c.A2A_V1_PROTOCOL_VERSION
            resp = await self._http().post(card.url, json=body, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - best effort
            logger.warning("a2a.cancel_failed", task_id=task_id, error=str(exc))


class A2AConnectionManager:
    """Allowlist + client pool for outbound A2A (mirror of ``mcp/connection.py``)."""

    def __init__(
        self,
        *,
        allowlist: list[str],
        strict: bool = True,
        auth: A2AAuth | None = None,
        local_org_id: str | None = None,
        principal: str | None = None,
        prompter: AuthorizationPrompter | None = None,
        vault: CredentialVaultPort | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        self._allowlist = list(allowlist)
        self._strict = strict
        self._auth = auth
        self._local_org_id = local_org_id
        self._principal = principal
        self._prompter = prompter
        self._vault = vault
        self._redirect_uri = redirect_uri
        self._pool: dict[str, A2AClient] = {}

    async def get_client(self, card_url: str) -> A2AClient:
        """Return a pooled client for *card_url*, enforcing the allowlist."""
        host = _host_of(card_url)
        if not _allowed(host, self._allowlist, strict=self._strict):
            logger.warning("a2a.outbound_denied", host=host)
            raise A2AAgentUnavailable(card_url, "host not in outbound allowlist")
        client = self._pool.get(card_url)
        if client is None:
            client = A2AClient(
                card_url,
                auth=self._auth,
                allowlist=self._allowlist,
                strict=self._strict,
                local_org_id=self._local_org_id,
                principal=self._principal,
                prompter=self._prompter,
                vault=self._vault,
                redirect_uri=self._redirect_uri,
            )
            self._pool[card_url] = client
        return client

    async def aclose(self) -> None:
        for client in self._pool.values():
            await client.aclose()
        self._pool.clear()


class A2AAgentNode:
    """Wrap a remote A2A agent as a prismal graph node (SPEC-A2A-004).

    The A2A analogue of ``LangChainRunnableAdapter``: ``as_node`` returns an
    ``async (state) -> state_update`` decorated with ``@prismal_node`` (otel,
    audit, security middleware for free). Remote artifacts are sanitized before
    they touch the state; a remote failure yields a graceful error update
    (``metadata.a2a.error = True``) rather than aborting the graph.
    """

    def __init__(
        self,
        card_or_url: str | AgentCard,
        *,
        client: Any | None = None,
        manager: A2AConnectionManager | None = None,
        skill_id: str | None = None,
        auth: A2AAuth | None = None,
        sanitizer: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self._card_or_url = card_or_url
        self._card_url = card_or_url.url if isinstance(card_or_url, AgentCard) else card_or_url
        self._client = client
        self._manager = manager
        self._skill_id = skill_id
        self._auth = auth
        self._sanitizer = sanitizer
        self._audit = audit

    async def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._manager is not None:
            return await self._manager.get_client(self._card_url)
        self._client = A2AClient(self._card_url, auth=self._auth)
        return self._client

    def _get_sanitizer(self) -> Any:
        if self._sanitizer is None:
            from prismal.security import InputSanitizer

            self._sanitizer = InputSanitizer()
        return self._sanitizer

    def _get_audit(self) -> Any:
        if self._audit is None:
            from prismal.security import AuditLogger

            self._audit = AuditLogger()
        return self._audit

    @staticmethod
    def _last_user_text(messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content:
                return content
        return ""

    async def ainvoke(self, state: AgentState) -> dict[str, Any]:
        """Delegate the last turn to the remote agent and merge the answer."""
        from langchain_core.messages import AIMessage

        messages = list(state.get("messages") or [])
        prompt = self._last_user_text(messages)
        msg = A2AMessage(
            role="user",
            parts=[A2APart(kind="text", text=prompt)],
            message_id=uuid.uuid4().hex,
        )
        audit = self._get_audit()
        try:
            client = await self._resolve_client()
            texts: list[str] = []
            count = 0
            async for artifact in client.send_task(msg, skill_id=self._skill_id):
                count += 1
                for part in artifact.parts:
                    if part.kind == "text" and part.text:
                        texts.append(part.text)
        except Exception as exc:
            audit.log_event(
                "a2a.outbound",
                {"agent": self._card_url, "skill": self._skill_id, "status": "failed"},
            )
            logger.warning("a2a.outbound_error", agent=self._card_url, error=str(exc))
            return {
                "messages": [AIMessage(content=f"[a2a] remote agent unavailable: {exc}")],
                "metadata": {"a2a": {"error": True, "agent": self._card_url}},
            }

        sanitizer = self._get_sanitizer()
        answer = sanitizer.sanitize("\n".join(texts)) if texts else ""
        audit.log_event(
            "a2a.outbound",
            {
                "agent": self._card_url,
                "skill": self._skill_id,
                "status": "completed",
                "artifacts": count,
            },
        )
        return {
            "messages": [AIMessage(content=answer)],
            "metadata": {"a2a": {"error": False, "agent": self._card_url, "artifacts": count}},
        }

    def as_node(
        self,
        *,
        name: str,
        capabilities: list[str] | None = None,
        security: SecurityLevel = "standard",
        timeout_s: float | None = None,
    ) -> NodeFn:
        """Return a ``@prismal_node``-wrapped callable ready for ``add_node``."""
        from prismal.agents.extension import prismal_node

        async def _node(state: AgentState) -> dict[str, Any]:
            return await self.ainvoke(state)

        return prismal_node(
            name=name,
            capabilities=capabilities,
            security=security,
            timeout_s=timeout_s,
        )(_node)


__all__ = ["A2AAgentNode", "A2AClient", "A2AConnectionManager"]
