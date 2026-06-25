"""Tool registry — maps YAML config names to LangChain tool objects."""

from langchain_core.tools import BaseTool

from agri_agent.agent.tools.calculator import calculator
from agri_agent.agent.tools.dispatch import (
    dispatch_order,
    get_dispatch_rules,
    get_order_details,
    get_pending_orders,
    recommend_dispatch,
)
from agri_agent.agent.tools.outreach import filter_prospects, list_retailers, send_email
from agri_agent.agent.tools.search import web_search

_TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": calculator,
    "web_search": web_search,
    "list_retailers": list_retailers,
    "filter_prospects": filter_prospects,
    "send_email": send_email,
    "get_pending_orders": get_pending_orders,
    "get_order_details": get_order_details,
    "get_dispatch_rules": get_dispatch_rules,
    "dispatch_order": dispatch_order,
    "recommend_dispatch": recommend_dispatch,
}


def get_tools_for_config(tool_configs: list) -> list[BaseTool]:
    """Return enabled tool instances for a list of ToolConfig objects."""
    tools = []
    for tc in tool_configs:
        if tc.enabled and tc.name in _TOOL_REGISTRY:
            tools.append(_TOOL_REGISTRY[tc.name])
    return tools


def list_available_tools() -> list[str]:
    return list(_TOOL_REGISTRY.keys())


def list_tools_with_descriptions() -> list[dict]:
    """Return name + description for every tool in the registry."""
    return [
        {"name": name, "description": tool.description or ""}
        for name, tool in _TOOL_REGISTRY.items()
    ]
