"""Web search skill — searches the web via DuckDuckGo (no API key required).

Uses the free DuckDuckGo Instant Answer API by default.
Set ``LIGHTAGENT_SEARCH_PROVIDER=google`` with ``LIGHTAGENT_GOOGLE_API_KEY`` and
``LIGHTAGENT_GOOGLE_CSE_ID`` to switch to Google Custom Search.

Example::

    skill = WebSearchSkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"query": "Python 3.13 new features"})
"""

from __future__ import annotations

import os
import urllib.parse

import httpx
from langchain_core.tools import BaseTool, tool

from lightagent.core.logging import get_logger
from lightagent.skills.base import BaseSkill, SkillMetadata

logger = get_logger("lightagent.skills.web_search")

_DDG_URL = "https://api.duckduckgo.com/"
_GOOGLE_URL = "https://www.googleapis.com/customsearch/v1"


def _search_ddg(query: str, max_results: int) -> str:
    """Search using DuckDuckGo Instant Answer API.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.

    Returns:
        Formatted search results string.
    """
    try:
        resp = httpx.get(
            _DDG_URL,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[str] = []

        # Abstract (best single answer)
        if data.get("AbstractText"):
            results.append(f"Summary: {data['AbstractText']}")
            if data.get("AbstractURL"):
                results.append(f"Source: {data['AbstractURL']}")

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text']}")
                if topic.get("FirstURL"):
                    results.append(f"  URL: {topic['FirstURL']}")

        if not results:
            encoded = urllib.parse.quote_plus(query)
            return (
                f"No instant results found for '{query}'. "
                f"Try: https://duckduckgo.com/?q={encoded}"
            )

        return "\n".join(results)
    except httpx.HTTPError as exc:
        return f"[web_search] HTTP error searching for '{query}': {exc}"
    except (KeyError, ValueError) as exc:
        return f"[web_search] Unexpected response format: {exc}"


def _search_google(query: str, max_results: int, api_key: str, cse_id: str) -> str:
    """Search using Google Custom Search API.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.
        api_key: Google API key.
        cse_id: Google Custom Search Engine ID.

    Returns:
        Formatted search results string.
    """
    try:
        resp = httpx.get(
            _GOOGLE_URL,
            params={
                "q": query,
                "key": api_key,
                "cx": cse_id,
                "num": min(max_results, 10),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return f"No results found for '{query}'."

        results: list[str] = []
        for item in items[:max_results]:
            title = item.get("title", "No title")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            results.append(f"**{title}**\n{snippet}\n{link}")

        return "\n\n".join(results)
    except httpx.HTTPError as exc:
        return f"[web_search] HTTP error searching for '{query}': {exc}"
    except (KeyError, ValueError) as exc:
        return f"[web_search] Unexpected response format: {exc}"


class WebSearchSkill(BaseSkill):
    """Provides web search tools using DuckDuckGo or Google Custom Search."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return web search skill metadata."""
        return SkillMetadata(
            name="web_search",
            description="Search the web for current information via DuckDuckGo or Google",  # noqa: E501
            version="1.0.0",
            author="lightagent",
            safe_to_auto_activate=True,
            tags=["utility", "search", "web", "api"],
        )

    def get_tools(self) -> list[BaseTool]:
        """Return web search tools.

        Returns:
            List containing the web_search tool.
        """

        @tool
        def web_search(query: str, max_results: int = 5) -> str:
            """Search the web for information on a topic.

            Args:
                query: The search query (e.g. 'Python 3.13 features', 'news today').
                max_results: Maximum number of results to return (1-10).

            Returns:
                Search results as a formatted string with titles, snippets, and URLs.
            """
            provider = os.getenv("LIGHTAGENT_SEARCH_PROVIDER", "ddg").lower()
            api_key = os.getenv("LIGHTAGENT_GOOGLE_API_KEY", "")
            cse_id = os.getenv("LIGHTAGENT_GOOGLE_CSE_ID", "")

            if provider == "google" and api_key and cse_id:
                return _search_google(query, max_results, api_key, cse_id)
            return _search_ddg(query, max_results)

        return [web_search]
