"""Tool registry — maps YAML config names to LangChain tool objects."""

from langchain_core.tools import BaseTool

from agri_agent.agent.tools.agri import (
    calculate_fertilizer,
    get_crop_recommendation,
    get_pest_alert,
    get_weather_data,
)
from agri_agent.agent.tools.calculator import calculator
from agri_agent.agent.tools.search import web_search

_TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": calculator,
    "web_search": web_search,
    "get_crop_recommendation": get_crop_recommendation,
    "get_pest_alert": get_pest_alert,
    "calculate_fertilizer": calculate_fertilizer,
    "get_weather_data": get_weather_data,
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
