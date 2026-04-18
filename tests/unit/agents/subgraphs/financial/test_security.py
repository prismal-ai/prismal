"""Security tests for Phase 27 financial analysis agent."""

from __future__ import annotations


def test_trade_execution_disabled_by_default() -> None:
    """AC-027-1: Trade execution is always disabled by default."""
    from lightagent.core.config import get_settings

    assert get_settings().financial_trade_execution_enabled is False


def test_no_openbb_import_at_module_level_in_graph() -> None:
    """openbb must NOT be imported at module level in graph.py."""
    import ast
    import pathlib

    source = pathlib.Path("lightagent/agents/graph.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("openbb"), (
                    f"openbb imported at module level in graph.py: {alias.name}"
                )
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("openbb"):
            raise AssertionError(f"openbb imported at module level in graph.py: {node.module}")


def test_no_ccxt_import_at_module_level_in_supervisor() -> None:
    """ccxt must NOT be imported at module level in supervisor.py."""
    import ast
    import pathlib

    source = pathlib.Path("lightagent/agents/supervisor.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("ccxt"), (
                    "ccxt imported at module level in supervisor.py"
                )


def test_financial_report_default_disclaimer_present() -> None:
    """FinancialReport default disclaimer is always the legal text."""
    from lightagent.agents.subgraphs.financial.artifacts import FinancialReport

    report = FinancialReport(symbol="TEST", report_mode="single_asset")
    assert "informational purposes only" in report.disclaimer


def test_no_api_keys_in_financial_yaml() -> None:
    """Exchange API keys must not appear in config/financial.yaml."""
    import pathlib

    config = pathlib.Path("config/financial.yaml").read_text()
    forbidden = ["api_key:", "secret:", "BINANCE_API_KEY", "apikey:", "token:"]
    for pattern in forbidden:
        assert pattern.lower() not in config.lower(), (
            f"Potential API key found in financial.yaml: {pattern}"
        )
