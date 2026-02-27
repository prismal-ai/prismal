"""Stub LangChain tools for LightAgent sub-agents.

All implementations are placeholders that return ``[stub] ...`` messages.
Real implementations will be wired in later phases when the respective
backend services (RAG, code executor, DuckDB, etc.) are available.

Example::

    from lightagent.agents.tools import web_search, RESEARCHER_TOOLS

    result = web_search.invoke({"query": "latest AI news"})
    # "[stub] web_search called with query='latest AI news'"
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool


@tool
def web_search(query: str) -> str:
    """Search the web for up-to-date information.

    Args:
        query: The search query string.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] web_search called with query={query!r}"


@tool
def rag_search(query: str, collection: str = "default") -> str:
    """Search a RAG vector collection for relevant document chunks.

    Args:
        query: The semantic search query.
        collection: Name of the ChromaDB collection to search.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] rag_search called with query={query!r}, collection={collection!r}"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file from the filesystem.

    Args:
        path: Absolute or relative path to the file to read.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] read_file called with path={path!r}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file on the filesystem.

    Args:
        path: Absolute or relative path to the destination file.
        content: Text content to write to the file.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] write_file called with path={path!r}, content_length={len(content)}"


@tool
def code_executor(code: str, language: str = "python") -> str:
    """Execute a code snippet in the specified programming language.

    Args:
        code: Source code to execute.
        language: Programming language of the code (e.g. ``"python"``, ``"bash"``).

    Returns:
        A stub response indicating the tool was called.
    """
    return (
        f"[stub] code_executor called with language={language!r}, "
        f"code_length={len(code)}"
    )


@tool
def vector_search(query: str, collection: str = "default", k: int = 5) -> str:
    """Perform a nearest-neighbour vector search in a ChromaDB collection.

    Args:
        query: The embedding query string.
        collection: Name of the ChromaDB collection to search.
        k: Number of top results to return.

    Returns:
        A stub response indicating the tool was called.
    """
    return (
        f"[stub] vector_search called with query={query!r}, "
        f"collection={collection!r}, k={k}"
    )


@tool
def doc_index(path: str, collection: str = "default") -> str:
    """Index a document (or directory of documents) into a ChromaDB collection.

    Args:
        path: Path to the document file or directory to index.
        collection: Name of the ChromaDB collection to insert into.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] doc_index called with path={path!r}, collection={collection!r}"


@tool
def evaluate(text: str, criteria: str) -> str:
    """Evaluate a piece of text against a set of qualitative criteria.

    Args:
        text: The text to evaluate.
        criteria: Natural-language description of the evaluation criteria.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] evaluate called with text_length={len(text)}, criteria={criteria!r}"


@tool
def score(text: str, rubric: str) -> float:
    """Score a piece of text against a scoring rubric.

    Args:
        text: The text to score.
        rubric: Natural-language rubric describing how to assign a score.

    Returns:
        A stub float score (always 0.0 in this placeholder implementation).
    """
    _ = text, rubric  # unused in stub
    return 0.0


@tool
def duckdb_query(sql: str, source: str = "") -> str:
    """Execute a SQL query against a DuckDB data source.

    Args:
        sql: The SQL query string to execute.
        source: Optional path to a Parquet/CSV file or DuckDB database file.

    Returns:
        A stub response indicating the tool was called.
    """
    return f"[stub] duckdb_query called with sql_length={len(sql)}, source={source!r}"


@tool
def polars_transform(operation: str, data_source: str) -> str:
    """Apply a Polars data transformation operation to a data source.

    Args:
        operation: Name or description of the Polars transformation to apply.
        data_source: Path or URI of the input data source.

    Returns:
        A stub response indicating the tool was called.
    """
    return (
        f"[stub] polars_transform called with operation={operation!r}, "
        f"data_source={data_source!r}"
    )


@tool
def create_chart(data: str, chart_type: str = "bar", title: str = "") -> str:
    """Create a chart from serialised data.

    Args:
        data: JSON-serialised data to visualise.
        chart_type: Type of chart (e.g. ``"bar"``, ``"line"``, ``"scatter"``).
        title: Optional chart title.

    Returns:
        A stub response indicating the tool was called.
    """
    return (
        f"[stub] create_chart called with chart_type={chart_type!r}, "
        f"title={title!r}, data_length={len(data)}"
    )


RESEARCHER_TOOLS: list[BaseTool] = [web_search, rag_search, read_file]
CODER_TOOLS: list[BaseTool] = [code_executor, read_file, write_file]
RAG_AGENT_TOOLS: list[BaseTool] = [vector_search, doc_index, web_search]
CRITIC_TOOLS: list[BaseTool] = [evaluate, score]
DATA_ANALYST_TOOLS: list[BaseTool] = [duckdb_query, polars_transform, create_chart]
FILE_MANAGER_TOOLS: list[BaseTool] = [read_file, write_file]

__all__ = [
    "CODER_TOOLS",
    "CRITIC_TOOLS",
    "DATA_ANALYST_TOOLS",
    "FILE_MANAGER_TOOLS",
    "RAG_AGENT_TOOLS",
    "RESEARCHER_TOOLS",
    "code_executor",
    "create_chart",
    "doc_index",
    "duckdb_query",
    "evaluate",
    "polars_transform",
    "rag_search",
    "read_file",
    "score",
    "vector_search",
    "web_search",
    "write_file",
]
