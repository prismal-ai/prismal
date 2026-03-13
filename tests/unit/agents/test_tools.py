"""Unit tests for lightagent.agents.tools — stub tools and tool group constants."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.tools import BaseTool

from lightagent.agents.tools import (
    CODER_TOOLS,
    CRITIC_TOOLS,
    DATA_ANALYST_TOOLS,
    FILE_MANAGER_TOOLS,
    RAG_AGENT_TOOLS,
    RESEARCHER_TOOLS,
    code_executor,
    create_chart,
    cron_add,
    doc_index,
    duckdb_query,
    evaluate,
    polars_transform,
    rag_search,
    read_file,
    score,
    vector_search,
    web_search,
    write_file,
)


# ---------------------------------------------------------------------------
# Individual stub tool outputs
# ---------------------------------------------------------------------------


def test_web_search_stub_returns_string() -> None:
    """web_search returns a string containing the query when backend succeeds."""
    with patch("langchain_community.tools.DuckDuckGoSearchRun") as mock_cls:
        mock_cls.return_value.run.return_value = "AI news results for: latest AI news"
        result = web_search.invoke({"query": "latest AI news"})
    assert isinstance(result, str)
    assert "latest AI news" in result


def test_rag_search_stub_contains_query_and_collection() -> None:
    """rag_search returns results containing query and collection name."""
    chunk = MagicMock(
        relevance_score=0.9,
        source="docs/test.pdf",
        content="machine learning concepts in docs collection",
    )
    with patch("lightagent.rag.engine.RAGEngine") as mock_cls:
        mock_cls.return_value.search.return_value = [chunk]
        result = rag_search.invoke({"query": "machine learning", "collection": "docs"})
    assert isinstance(result, str)
    assert "machine learning" in result
    assert "docs" in result


def test_rag_search_default_collection() -> None:
    """rag_search uses 'default' collection when none specified — shows in no-results message."""
    with patch("lightagent.rag.engine.RAGEngine") as mock_cls:
        mock_cls.return_value.search.return_value = []
        result = rag_search.invoke({"query": "test"})
    assert isinstance(result, str)
    assert "default" in result


def test_read_file_stub_contains_path() -> None:
    """read_file returns a message containing the requested path."""
    result = read_file.invoke({"path": "/home/user/data.txt"})
    assert isinstance(result, str)
    assert "/home/user/data.txt" in result


def test_write_file_stub_contains_path_and_length() -> None:
    """write_file returns a confirmation with the resolved path and byte count."""
    content = "hello world"
    result = write_file.invoke({"path": "/tmp/out.txt", "content": content})
    assert isinstance(result, str)
    assert "/tmp/out.txt" in result
    assert str(len(content)) in result


def test_code_executor_stub_contains_language_and_length() -> None:
    """code_executor runs code and returns output when shell is enabled."""
    code = "print('hello')"
    with (
        patch("lightagent.core.config.get_settings") as mock_gs,
        patch("subprocess.run") as mock_run,
    ):
        mock_gs.return_value.shell_enabled = True
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
        result = code_executor.invoke({"code": code, "language": "python"})
    assert isinstance(result, str)
    assert "hello" in result


def test_code_executor_default_language() -> None:
    """code_executor defaults to python when language is omitted."""
    with (
        patch("lightagent.core.config.get_settings") as mock_gs,
        patch("subprocess.run") as mock_run,
    ):
        mock_gs.return_value.shell_enabled = True
        mock_run.return_value = MagicMock(stdout="1\n", stderr="", returncode=0)
        result = code_executor.invoke({"code": "x = 1"})
    assert isinstance(result, str)


def test_vector_search_stub_contains_all_params() -> None:
    """vector_search result includes query, collection, and k value."""
    chunk = MagicMock(
        relevance_score=0.95,
        source="kb/doc.txt",
        content="embeddings concept found in knowledge base",
    )
    with patch("lightagent.rag.engine.RAGEngine") as mock_cls:
        mock_cls.return_value.search.return_value = [chunk]
        result = vector_search.invoke({"query": "embeddings", "collection": "knowledge", "k": 10})
    assert isinstance(result, str)
    assert "embeddings" in result
    assert "knowledge" in result
    assert "10" in result


def test_doc_index_stub_contains_path_and_collection() -> None:
    """doc_index returns confirmation with chunk count and collection name."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp_path = f.name
    try:
        with patch("lightagent.rag.engine.RAGEngine") as mock_cls:
            mock_cls.return_value.index_file.return_value = 5
            result = doc_index.invoke({"path": tmp_path, "collection": "reports"})
        assert isinstance(result, str)
        assert "reports" in result
        assert "5" in result
    finally:
        os.unlink(tmp_path)


def test_evaluate_stub_contains_criteria() -> None:
    """evaluate passes criteria to the LLM and returns its response."""
    mock_response = MagicMock(content="Evaluation: clarity score is high")
    with patch("lightagent.providers.registry.ProviderRegistry") as mock_cls:
        mock_cls.return_value.get_llm_with_fallback.return_value.invoke.return_value = mock_response
        result = evaluate.invoke({"text": "some text", "criteria": "clarity"})
    assert isinstance(result, str)
    assert "clarity" in result


def test_score_stub_returns_float() -> None:
    """score returns a float parsed from the LLM response."""
    mock_response = MagicMock(content="0.0")
    with patch("lightagent.providers.registry.ProviderRegistry") as mock_cls:
        mock_cls.return_value.get_llm_with_fallback.return_value.invoke.return_value = mock_response
        result = score.invoke({"text": "some text", "rubric": "accuracy"})
    assert isinstance(result, float)
    assert result == 0.0


def test_duckdb_query_stub_contains_sql_length() -> None:
    """duckdb_query formats query results as a table and returns it."""
    sql = "SELECT avg(sales) FROM data.csv"
    rows = [{"avg_sales": "150.0", "source": "data.csv"}]
    mock_engine = MagicMock()
    mock_engine.query.return_value = rows
    mock_engine._conn = MagicMock()
    with patch("lightagent.data.duckdb_engine.DuckDBEngine") as mock_cls:
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_engine)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = duckdb_query.invoke({"sql": sql, "source": "data.csv"})
    assert isinstance(result, str)
    assert "data.csv" in result


def test_polars_transform_stub_contains_operation_and_source() -> None:
    """polars_transform reads CSV and applies the operation, returning tabular output."""
    with tempfile.NamedTemporaryFile(
        suffix=".csv", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("region,sales\nnorth,100\nsouth,200\n")
        tmp_csv = f.name
    try:
        result = polars_transform.invoke({"operation": "head 2", "data_source": tmp_csv})
    finally:
        os.unlink(tmp_csv)
    assert isinstance(result, str)
    assert "region" in result or "sales" in result


def test_create_chart_stub_contains_chart_type_and_title() -> None:
    """create_chart saves a chart file and returns its path."""
    mock_saved = Path("/tmp/chart_Sales.png")
    with patch("lightagent.data.polars_utils.save_chart", return_value=mock_saved):
        result = create_chart.invoke(
            {"data": '[{"month": "Jan", "sales": 120}]', "chart_type": "line", "title": "Sales"}
        )
    assert isinstance(result, str)
    assert "Sales" in result


# ---------------------------------------------------------------------------
# Tool instances are LangChain BaseTool
# ---------------------------------------------------------------------------


def test_all_tools_are_base_tool_instances() -> None:
    """Every exported tool must be a BaseTool instance."""
    all_tools = [
        web_search, rag_search, read_file, write_file, code_executor,
        vector_search, doc_index, evaluate, score, duckdb_query,
        polars_transform, create_chart,
    ]
    for t in all_tools:
        assert isinstance(t, BaseTool), f"{t} is not a BaseTool"


def test_all_tools_have_non_empty_name() -> None:
    """Every tool must have a non-empty name attribute."""
    all_tools = [
        web_search, rag_search, read_file, write_file, code_executor,
        vector_search, doc_index, evaluate, score, duckdb_query,
        polars_transform, create_chart,
    ]
    for t in all_tools:
        assert t.name, f"{t} has empty name"


def test_all_tools_have_description() -> None:
    """Every tool must have a non-empty description attribute."""
    all_tools = [
        web_search, rag_search, read_file, write_file, code_executor,
        vector_search, doc_index, evaluate, score, duckdb_query,
        polars_transform, create_chart,
    ]
    for t in all_tools:
        assert t.description, f"{t.name} has empty description"


# ---------------------------------------------------------------------------
# Tool group constants
# ---------------------------------------------------------------------------


def test_researcher_tools_contains_expected_tools() -> None:
    """RESEARCHER_TOOLS must include web_search, rag_search, read_file."""
    names = {t.name for t in RESEARCHER_TOOLS}
    assert "web_search" in names
    assert "rag_search" in names
    assert "read_file" in names


def test_coder_tools_contains_expected_tools() -> None:
    """CODER_TOOLS must include code_executor, read_file, write_file."""
    names = {t.name for t in CODER_TOOLS}
    assert "code_executor" in names
    assert "read_file" in names
    assert "write_file" in names


def test_rag_agent_tools_contains_expected_tools() -> None:
    """RAG_AGENT_TOOLS must include vector_search, doc_index, web_search."""
    names = {t.name for t in RAG_AGENT_TOOLS}
    assert "vector_search" in names
    assert "doc_index" in names
    assert "web_search" in names


def test_critic_tools_contains_evaluate_and_score() -> None:
    """CRITIC_TOOLS must include evaluate and score."""
    names = {t.name for t in CRITIC_TOOLS}
    assert "evaluate" in names
    assert "score" in names


def test_data_analyst_tools_contains_duckdb_polars_chart() -> None:
    """DATA_ANALYST_TOOLS must include duckdb_query, polars_transform, create_chart."""
    names = {t.name for t in DATA_ANALYST_TOOLS}
    assert "duckdb_query" in names
    assert "polars_transform" in names
    assert "create_chart" in names


def test_file_manager_tools_contains_read_and_write() -> None:
    """FILE_MANAGER_TOOLS must include read_file and write_file."""
    names = {t.name for t in FILE_MANAGER_TOOLS}
    assert "read_file" in names
    assert "write_file" in names


def test_tool_groups_are_lists_of_base_tool() -> None:
    """All tool group constants must be lists of BaseTool."""
    groups = [RESEARCHER_TOOLS, CODER_TOOLS, RAG_AGENT_TOOLS, CRITIC_TOOLS, DATA_ANALYST_TOOLS, FILE_MANAGER_TOOLS]
    for group in groups:
        assert isinstance(group, list)
        for t in group:
            assert isinstance(t, BaseTool)


def test_read_file_blocks_system_path() -> None:
    """read_file must return an error string for blocked paths like /etc/passwd."""
    from lightagent.agents.tools import read_file

    result = read_file.invoke({"path": "/etc/passwd"})
    assert "error" in result.lower() or "blocked" in result.lower()


def test_write_file_blocks_system_path() -> None:
    """write_file must return an error string for blocked paths like /etc/evil.txt."""
    from lightagent.agents.tools import write_file

    result = write_file.invoke({"path": "/etc/evil.txt", "content": "x"})
    assert "error" in result.lower() or "blocked" in result.lower()


def test_cron_add_tool_accepts_output_channel() -> None:
    """cron_add tool passes output_channel and output_target to CronManager."""
    with patch("lightagent.agents.tools.CronManager") as mock_cls:
        mock_manager = MagicMock()
        mock_manager.add.return_value = MagicMock(
            name="hb",
            schedule="0 8 * * *",
            task="Morning check",
            output_channel="telegram",
            output_target="12345",
            next_run=None,
        )
        mock_cls.return_value = mock_manager

        result = cron_add.invoke({
            "name": "hb",
            "schedule": "0 8 * * *",
            "task": "Morning check",
            "output_channel": "telegram",
            "output_target": "12345",
        })

    mock_manager.add.assert_called_once_with(
        "hb",
        "0 8 * * *",
        "Morning check",
        output_channel="telegram",
        output_target="12345",
    )
    assert "telegram" in result


def test_list_dir_returns_entries(tmp_path: Path) -> None:
    """list_dir returns [DIR] and [FILE] tagged entries for a directory."""
    from lightagent.agents.tools import list_dir
    (tmp_path / "file1.txt").write_text("a")
    (tmp_path / "subdir").mkdir()
    result = list_dir.invoke({"path": str(tmp_path)})
    assert "file1.txt" in result
    assert "subdir" in result


def test_list_dir_blocks_system_path() -> None:
    """list_dir returns an error for blocked system paths."""
    from lightagent.agents.tools import list_dir
    result = list_dir.invoke({"path": "/etc"})
    assert "error" in result.lower() or "blocked" in result.lower()


def test_find_files_finds_by_pattern(tmp_path: Path) -> None:
    """find_files returns files matching the glob pattern."""
    from lightagent.agents.tools import find_files
    (tmp_path / "main.py").write_text("x")
    (tmp_path / "test_main.py").write_text("y")
    (tmp_path / "readme.md").write_text("z")
    result = find_files.invoke({"root": str(tmp_path), "pattern": "*.py"})
    assert "main.py" in result
    assert "test_main.py" in result
    assert "readme.md" not in result


def test_find_files_respects_max_results(tmp_path: Path) -> None:
    """find_files returns at most max_results entries."""
    from lightagent.agents.tools import find_files
    for i in range(20):
        (tmp_path / f"file{i}.txt").write_text("x")
    result = find_files.invoke({"root": str(tmp_path), "pattern": "*.txt", "max_results": 5})
    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 5


# ---------------------------------------------------------------------------
# create_dir tests
# ---------------------------------------------------------------------------


def test_create_dir_creates_nested(tmp_path: Path) -> None:
    """create_dir creates a nested directory structure."""
    from lightagent.agents.tools import create_dir
    new_dir = tmp_path / "a" / "b" / "c"
    result = create_dir.invoke({"path": str(new_dir)})
    assert new_dir.exists()
    assert "created" in result.lower()


def test_create_dir_is_idempotent(tmp_path: Path) -> None:
    """create_dir on an existing directory does not raise."""
    from lightagent.agents.tools import create_dir
    d = tmp_path / "existing"
    d.mkdir()
    result = create_dir.invoke({"path": str(d)})
    assert isinstance(result, str)  # returns something, doesn't raise


def test_create_dir_blocks_system_path() -> None:
    """create_dir returns an error for blocked paths."""
    from lightagent.agents.tools import create_dir
    result = create_dir.invoke({"path": "/etc/newdir"})
    assert "error" in result.lower() or "blocked" in result.lower()


# ---------------------------------------------------------------------------
# move_path tests
# ---------------------------------------------------------------------------


def test_move_path_renames_file(tmp_path: Path) -> None:
    """move_path moves a file from src to dst."""
    from lightagent.agents.tools import move_path
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")
    result = move_path.invoke({"src": str(src), "dst": str(dst)})
    assert dst.exists()
    assert not src.exists()
    assert "moved" in result.lower()


def test_move_path_returns_error_for_missing_src(tmp_path: Path) -> None:
    """move_path returns an error if source does not exist."""
    from lightagent.agents.tools import move_path
    result = move_path.invoke({"src": str(tmp_path / "missing.txt"), "dst": str(tmp_path / "dst.txt")})
    assert "error" in result.lower()


# ---------------------------------------------------------------------------
# delete_path tests
# ---------------------------------------------------------------------------


def test_delete_path_removes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_path removes a file when fs_delete_enabled is True."""
    from lightagent.agents.tools import delete_path
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(fs_delete_enabled=True))
    f = tmp_path / "todelete.txt"
    f.write_text("bye")
    result = delete_path.invoke({"path": str(f)})
    assert not f.exists()
    assert "deleted" in result.lower()


def test_delete_path_blocked_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_path returns an error when fs_delete_enabled is False."""
    from lightagent.agents.tools import delete_path
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(fs_delete_enabled=False))
    f = tmp_path / "safe.txt"
    f.write_text("keep")
    result = delete_path.invoke({"path": str(f)})
    assert f.exists()
    assert "not enabled" in result.lower() or "disabled" in result.lower()


# ---------------------------------------------------------------------------
# shell_exec tests
# ---------------------------------------------------------------------------


def test_shell_exec_blocked_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell_exec returns an error when shell_enabled is False."""
    from lightagent.agents.tools import shell_exec
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(shell_enabled=False))
    result = shell_exec.invoke({"command": "echo hello"})
    assert "not enabled" in result.lower() or "disabled" in result.lower()


def test_shell_exec_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell_exec returns the stdout of the command."""
    from lightagent.agents.tools import shell_exec
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(shell_enabled=True))
    result = shell_exec.invoke({"command": "echo hello_world"})
    assert "hello_world" in result


def test_shell_exec_captures_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell_exec returns stderr content instead of raising."""
    from lightagent.agents.tools import shell_exec
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(shell_enabled=True))
    result = shell_exec.invoke({"command": "ls /nonexistent_path_xyz_abc_123"})
    assert isinstance(result, str)
    assert len(result) > 0


def test_shell_exec_timeout_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell_exec returns a timeout error when the command exceeds the limit."""
    from lightagent.agents.tools import shell_exec
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(shell_enabled=True))
    result = shell_exec.invoke({"command": "sleep 60", "timeout": 1})
    assert "timeout" in result.lower() or "timed out" in result.lower()


def test_shell_exec_rejects_empty_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell_exec returns an error for blank commands."""
    from lightagent.agents.tools import shell_exec
    from lightagent.core.config import Settings
    monkeypatch.setattr("lightagent.agents.tools.get_settings", lambda: Settings(shell_enabled=True))
    result = shell_exec.invoke({"command": "   "})
    assert "empty" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# Tool list wire tests (Task 6)
# ---------------------------------------------------------------------------


def test_file_manager_tools_contains_new_tools() -> None:
    """FILE_MANAGER_TOOLS must include all filesystem tools."""
    from lightagent.agents.tools import FILE_MANAGER_TOOLS
    tool_names = {t.name for t in FILE_MANAGER_TOOLS}
    assert "list_dir" in tool_names
    assert "find_files" in tool_names
    assert "create_dir" in tool_names
    assert "move_path" in tool_names
    assert "delete_path" in tool_names


def test_coder_tools_contains_shell_exec() -> None:
    """CODER_TOOLS must include shell_exec."""
    from lightagent.agents.tools import CODER_TOOLS
    tool_names = {t.name for t in CODER_TOOLS}
    assert "shell_exec" in tool_names


def test_all_exports_new_tools() -> None:
    """All new tools must appear in __all__."""
    import lightagent.agents.tools as m
    for name in ["list_dir", "find_files", "create_dir", "move_path", "delete_path", "shell_exec"]:
        assert name in m.__all__, f"{name} missing from __all__"
