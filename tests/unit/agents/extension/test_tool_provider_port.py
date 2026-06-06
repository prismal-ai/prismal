"""Tests for ``ToolProviderPort`` (Fase Y1, SPEC-TPI-001)."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from prismal.agents.extension.ports import ToolProviderPort, conforms_to


class _DemoTool(BaseTool):
    name: str = "demo_provider_tool"
    description: str = "demo"

    def _run(self, *args: object, **kwargs: object) -> str:
        return "ok"


class _ConformingProvider:
    """Structural implementation — no inheritance, no registration."""

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[BaseTool]:
        return [_DemoTool()]


class TestToolProviderPortConformance:
    def test_object_with_get_tools_conforms(self) -> None:
        assert conforms_to(_ConformingProvider(), ToolProviderPort)

    def test_plain_object_does_not_conform(self) -> None:
        assert conforms_to(object(), ToolProviderPort) is False

    def test_object_without_get_tools_does_not_conform(self) -> None:
        class _Other:
            def list_tools(self) -> list[BaseTool]:
                return []

        assert conforms_to(_Other(), ToolProviderPort) is False

    def test_get_tools_returns_tool_ports(self) -> None:
        from prismal.agents.extension.ports import ToolPort

        provider: ToolProviderPort = _ConformingProvider()
        tools = provider.get_tools(agent_name="researcher", capabilities=None)
        assert tools
        assert all(conforms_to(tool, ToolPort) for tool in tools)


class TestReExports:
    def test_port_reexported_from_extension(self) -> None:
        import prismal.agents.extension as ext

        assert ext.ToolProviderPort is ToolProviderPort

    def test_port_in_ports_all(self) -> None:
        from prismal.agents.extension import ports

        assert "ToolProviderPort" in ports.__all__

    def test_port_in_extension_all(self) -> None:
        import prismal.agents.extension as ext

        assert "ToolProviderPort" in ext.__all__
