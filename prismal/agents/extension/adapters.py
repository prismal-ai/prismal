"""Adapt LangChain ``Runnable`` / ``AgentExecutor`` objects to prismal nodes.

``LangChainRunnableAdapter`` maps ``state["messages"]`` to the Runnable's
input and the Runnable's output back to a state_update, then wraps the result
with ``@prismal_node`` via :meth:`as_node`.

Example::

    adapter = LangChainRunnableAdapter(my_agent_executor)
    node = adapter.as_node(name="legacy_research", capabilities=["research"])
    builder.add_node("legacy_research", node)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import AIMessage

from prismal.agents.extension.decorators import NodeFn, SecurityLevel, prismal_node
from prismal.core.exceptions import LangChainAdapterError

if TYPE_CHECKING:
    from langchain_core.runnables import Runnable

    from prismal.agents.state import AgentState
    from prismal.core.config import Settings

InputMapping = Literal["auto", "messages", "input_dict"]
"""How to map ``AgentState`` to the Runnable input.

``'auto'``       — detect from the Runnable (``input_keys`` → ``input_dict``).
``'messages'``   — pass ``state["messages"]`` as a list of ``BaseMessage``.
``'input_dict'`` — pass ``{"input": last_user_msg, "chat_history": prior_msgs}``.
"""

_VALID_MAPPINGS: tuple[str, ...] = ("auto", "messages", "input_dict")


class LangChainRunnableAdapter:
    """Convert a LangChain ``Runnable`` / ``AgentExecutor`` into a prismal node."""

    def __init__(
        self,
        runnable: Runnable[Any, Any],
        *,
        input_mapping: InputMapping = "auto",
        output_key: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wrap a Runnable.

        Raises:
            LangChainAdapterError: If ``runnable`` lacks an ``ainvoke`` method
                or ``input_mapping`` is not recognised.
        """
        if not hasattr(runnable, "ainvoke"):
            raise LangChainAdapterError(
                type(runnable).__name__, "object is not a LangChain Runnable (no ainvoke)"
            )
        if input_mapping not in _VALID_MAPPINGS:
            raise LangChainAdapterError(
                type(runnable).__name__, f"invalid input_mapping '{input_mapping}'"
            )
        self._runnable = runnable
        self._input_mapping: InputMapping = input_mapping
        self._output_key = output_key
        self._settings = settings

    def _resolve_mapping(self) -> InputMapping:
        if self._input_mapping != "auto":
            return self._input_mapping
        # AgentExecutor-style runnables expose ``input_keys``; feed them a dict.
        if hasattr(self._runnable, "input_keys"):
            return "input_dict"
        return "messages"

    def _map_input(self, state: AgentState) -> Any:
        messages = list(state.get("messages") or [])
        mapping = self._resolve_mapping()
        if mapping == "messages":
            return messages
        last_user = messages[-1].content if messages else ""
        return {"input": last_user, "chat_history": messages[:-1]}

    def _map_output(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, AIMessage):
            return {"messages": [raw]}
        if isinstance(raw, str):
            return {"messages": [AIMessage(content=raw)]}
        if isinstance(raw, dict):
            return {"messages": [AIMessage(content=self._extract_dict_text(raw))]}
        return {"messages": [AIMessage(content=str(raw))]}

    def _extract_dict_text(self, raw: dict[str, Any]) -> str:
        if self._output_key is not None:
            return str(raw.get(self._output_key, ""))
        if "output" in raw:
            return str(raw["output"])
        return str(raw)

    async def ainvoke(self, state: AgentState) -> dict[str, Any]:
        """Map state → Runnable → state_update (used directly in tests)."""
        try:
            mapped_in = self._map_input(state)
            raw = await self._runnable.ainvoke(mapped_in)
        except LangChainAdapterError:
            raise
        except Exception as exc:
            raise LangChainAdapterError(
                type(self._runnable).__name__, f"runnable invocation failed: {exc!r}"
            ) from exc
        return self._map_output(raw)

    def as_node(
        self,
        *,
        name: str,
        capabilities: list[str] | None = None,
        security: SecurityLevel = "standard",
        timeout_s: float | None = None,
    ) -> NodeFn:
        """Return a ``@prismal_node``-wrapped callable ready for ``add_node``."""

        async def _node(state: AgentState) -> dict[str, Any]:
            return await self.ainvoke(state)

        return prismal_node(
            name=name,
            capabilities=capabilities,
            security=security,
            timeout_s=timeout_s,
        )(_node)


__all__ = ["InputMapping", "LangChainRunnableAdapter"]
