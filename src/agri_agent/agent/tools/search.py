"""Web search tool — uses Tavily if API key is set, otherwise returns a mock."""

from langchain_core.tools import tool

from agri_agent.config.settings import settings


def _tavily_search(query: str, max_results: int) -> str:
    from tavily import TavilyClient  # type: ignore[import]

    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query, max_results=max_results)
    results = response.get("results", [])
    if not results:
        return "No results found."
    lines = []
    for r in results:
        lines.append(f"[{r.get('title', 'Untitled')}]({r.get('url', '')})\n{r.get('content', '')}")
    return "\n\n---\n\n".join(lines)


def _mock_search(query: str) -> str:
    return (
        f"[Mock search result for: '{query}']\n"
        "No TAVILY_API_KEY configured. Set it in .env to enable live search.\n"
        "For the POC this placeholder confirms the tool is wired correctly."
    )


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for up-to-date information.

    Args:
        query: The search query.
        max_results: Maximum number of results to return (default 5).
    """
    if settings.tavily_api_key:
        try:
            return _tavily_search(query, max_results)
        except Exception as exc:
            return f"Search error: {exc}"
    return _mock_search(query)
