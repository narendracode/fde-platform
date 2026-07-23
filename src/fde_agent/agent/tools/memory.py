"""Platform-level semantic memory tools for RAG-enabled agents."""
from __future__ import annotations

import json
import os

import httpx
from langchain_core.tools import tool

_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("API_KEY", "dev-secret-key-change-in-prod")
_HEADERS = {"X-API-Key": _API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=_BASE_URL, headers=_HEADERS, timeout=60.0)


@tool
def query_semantic_store(store_slug: str, query: str, top_k: int = 5) -> str:
    """Retrieve relevant knowledge chunks from a semantic memory store using vector similarity.

    Embeds the query and returns the top_k most relevant approved chunks from the
    named store, ranked by cosine similarity.  Returns an empty list if the store
    does not exist, is inactive, or has no approved documents yet — the agent
    should continue normally using its own knowledge in that case.

    Args:
        store_slug: Identifier of the knowledge base (e.g. 'vastu-shastra')
        query: Natural language query describing what knowledge to retrieve
        top_k: Number of chunks to return (default 5, max 20)
    """
    with _client() as c:
        resp = c.post(
            f"/api/v1/stores/{store_slug}/query",
            json={"query": query, "top_k": min(top_k, 20)},
        )
        if resp.status_code == 404:
            return json.dumps({"chunks": [], "note": f"Store '{store_slug}' not found or inactive. Use your own knowledge."})
        if resp.status_code != 200:
            return json.dumps({"chunks": [], "note": f"Store query failed ({resp.status_code}). Use your own knowledge."})
        return resp.text
