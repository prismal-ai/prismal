"""Unit tests for lightagent.agents.tools — stub tools and tool group constants."""

from __future__ import annotations

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
    """web_search returns a stub string containing the query."""
    result = web_search.invoke({"query": "latest AI news"})
    assert isinstance(result, str)
    assert "web_search" in result
    assert "latest AI news" in result


def test_rag_search_stub_contains_query_and_collection() -> None:
    """rag_search stub includes query and collection name."""
    result = rag_search.invoke({"query": "machine learning", "collection": "docs"})
    assert "rag_search" in result
    assert "machine learning" in result
    assert "docs" in result


def test_rag_search_default_collection() -> None:
    """rag_search uses 'default' collection when none is specified."""
    result = rag_search.invoke({"query": "test"})
    assert "default" in result


def test_read_file_stub_contains_path() -> None:
    """read_file stub includes the path."""
    result = read_file.invoke({"path": "/home/user/data.txt"})
    assert "read_file" in result
    assert "/home/user/data.txt" in result


def test_write_file_stub_contains_path_and_length() -> None:
    """write_file stub includes path and content length."""
    content = "hello world"
    result = write_file.invoke({"path": "/tmp/out.txt", "content": content})
    assert "write_file" in result
    assert "/tmp/out.txt" in result
    assert str(len(content)) in result


def test_code_executor_stub_contains_language_and_length() -> None:
    """code_executor stub includes language and code length."""
    code = "print('hello')"
    result = code_executor.invoke({"code": code, "language": "python"})
    assert "code_executor" in result
    assert "python" in result
    assert str(len(code)) in result


def test_code_executor_default_language() -> None:
    """code_executor defaults to 'python' language."""
    result = code_executor.invoke({"code": "x = 1"})
    assert "python" in result


def test_vector_search_stub_contains_all_params() -> None:
    """vector_search stub includes query, collection, and k."""
    result = vector_search.invoke({"query": "embeddings", "collection": "knowledge", "k": 10})
    assert "vector_search" in result
    assert "embeddings" in result
    assert "knowledge" in result
    assert "10" in result


def test_doc_index_stub_contains_path_and_collection() -> None:
    """doc_index stub includes path and collection."""
    result = doc_index.invoke({"path": "/data/report.pdf", "collection": "reports"})
    assert "doc_index" in result
    assert "/data/report.pdf" in result
    assert "reports" in result


def test_evaluate_stub_contains_criteria() -> None:
    """evaluate stub includes criteria."""
    result = evaluate.invoke({"text": "some text", "criteria": "clarity"})
    assert "evaluate" in result
    assert "clarity" in result


def test_score_stub_returns_float() -> None:
    """score stub returns a float (0.0)."""
    result = score.invoke({"text": "some text", "rubric": "accuracy"})
    assert isinstance(result, float)
    assert result == 0.0


def test_duckdb_query_stub_contains_sql_length() -> None:
    """duckdb_query stub includes sql_length and source."""
    sql = "SELECT avg(sales) FROM data.csv"
    result = duckdb_query.invoke({"sql": sql, "source": "data.csv"})
    assert "duckdb_query" in result
    assert str(len(sql)) in result
    assert "data.csv" in result


def test_polars_transform_stub_contains_operation_and_source() -> None:
    """polars_transform stub includes operation and data_source."""
    result = polars_transform.invoke({"operation": "filter", "data_source": "sales.parquet"})
    assert "polars_transform" in result
    assert "filter" in result
    assert "sales.parquet" in result


def test_create_chart_stub_contains_chart_type_and_title() -> None:
    """create_chart stub includes chart_type and title."""
    result = create_chart.invoke({"data": "[1,2,3]", "chart_type": "line", "title": "Sales"})
    assert "create_chart" in result
    assert "line" in result
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
