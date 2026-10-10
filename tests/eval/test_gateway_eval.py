"""Eval smoke: one agent through a fake gateway (Phase GW — SPEC-GW-SNAP-001, GW6-09).

Runs a real ``react_loop`` (tool call → final answer) against a
``GatewayChatLiteLLM`` whose litellm client is faked, and checks the task is
still solved and that tokens and gateway cost are reported per call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from litellm import ModelResponse
from pydantic import SecretStr

from prismal.agents.tool_registry import react_loop
from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.providers.gateways.base import GatewayCapabilities, GatewayProfile
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def _resp(message: dict[str, Any], cost: float) -> ModelResponse:
    return ModelResponse(
        id=f"gen-{cost}",
        model="fake/served",
        choices=[{"message": message, "finish_reason": "stop", "index": 0}],
        usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60, "cost": cost},
    )


class _Client:
    def __init__(self) -> None:
        self.responses = [
            _resp(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                        }
                    ],
                },
                0.002,
            ),
            _resp({"role": "assistant", "content": "The answer is 5."}, 0.001),
        ]
        self.calls = 0

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls += 1
        return self.responses.pop(0)

    def completion(self, **kwargs: Any) -> Any:  # pragma: no cover - async path only
        raise AssertionError("sync path not expected")


async def test_agent_solves_task_through_fake_gateway() -> None:
    s = Settings(llm_gateways_enabled=True)
    gw = OpenAICompatibleGateway(
        GatewayProfile(
            name="fake",
            base_url="https://fake.local/v1",
            auth_ref="k",
            capabilities=GatewayCapabilities(reports_cost=True),
        ),
        secret_resolver=lambda ref: SecretStr("sk-fake"),
    )
    llm: Any = ProviderRegistry(
        settings=s, gateway_router=GatewayRouter({"fake": gw}, settings=s)
    ).get_llm("gw:fake/served", agent="coder", session_id="eval")
    client = _Client()
    llm.client = client
    meter = CostMeter(settings=s)

    response = await react_loop(
        llm.bind_tools([add]),
        [add],
        [HumanMessage(content="What is 2 + 3? Use the add tool.")],
        agent_name="coder",
    )
    assert "5" in str(response.content)
    assert client.calls == 2
    usage = meter.record_response(response, "openai/served")
    assert usage.total_tokens == 60
    assert usage.cost_usd == 0.001
    assert usage.cost_source == "gateway"
    assert usage.gateway == "fake"
