"""LangGraph ReAct agent — built from a declarative AgentConfig."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from agri_agent.agent.tools import get_tools_for_config
from agri_agent.config.loader import AgentConfig
from agri_agent.config.settings import settings


# ── Model factory ─────────────────────────────────────────────────────────────

def _build_model(model_cfg):
    """Instantiate an LLM from a ModelConfig."""
    params = {
        "temperature": model_cfg.temperature,
        "max_tokens": model_cfg.max_tokens,
    }
    if model_cfg.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model_cfg.name,
            anthropic_api_key=settings.anthropic_api_key,
            **params,
        )
    if model_cfg.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_cfg.name,
            openai_api_key=settings.openai_api_key,
            **params,
        )
    raise ValueError(f"Unknown provider: {model_cfg.provider!r}")


# ── Guardrail helpers ─────────────────────────────────────────────────────────

def _check_blocked_patterns(text: str, patterns: list[str]) -> str | None:
    """Return the first matched blocked pattern, or None."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


# ── Agent builder ─────────────────────────────────────────────────────────────

def build_agent(config: AgentConfig):
    """Build a compiled LangGraph ReAct agent from an AgentConfig.

    Returns the compiled graph. Call `.invoke()` or `.stream()` on it.
    The agent is stateless between calls unless you pass a thread_id in
    the RunnableConfig (requires a checkpointer — add one here when ready).
    """
    model = _build_model(config.model)
    tools = get_tools_for_config(config.tools)

    # LangSmith tracing config
    if settings.langchain_tracing_v2 and config.observability.langsmith_tracing:
        import os
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langchain_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)

    compiled = create_react_agent(
        model=model,
        tools=tools,
        prompt=config.system_prompt.strip(),
    )
    return compiled


# ── High-level runner ─────────────────────────────────────────────────────────

def run_agent(
    config: AgentConfig,
    user_message: str,
    thread_id: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the agent synchronously and return a structured result dict.

    Args:
        config:       Loaded AgentConfig.
        user_message: The user's input text.
        thread_id:    Optional conversation thread ID for continuity.
        extra_context: Optional additional key-value context injected into the prompt.

    Returns:
        {
          "output": str,
          "messages": [...],
          "thread_id": str,
          "tool_calls": [...],
          "input_tokens": int,
          "output_tokens": int,
          "elapsed_seconds": float,
        }
    """
    # ── Guardrail: blocked patterns ───────────────────────────────────────────
    matched = _check_blocked_patterns(
        user_message, config.guardrails.blocked_patterns
    )
    if matched:
        return {
            "output": f"Request blocked by guardrail (matched pattern: '{matched}').",
            "messages": [],
            "thread_id": thread_id or str(uuid.uuid4()),
            "tool_calls": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_seconds": 0.0,
            "blocked": True,
        }

    agent = build_agent(config)

    # Build message list
    messages: list = [HumanMessage(content=user_message)]
    if extra_context:
        ctx_text = "\n".join(f"{k}: {v}" for k, v in extra_context.items())
        messages = [HumanMessage(content=f"Context:\n{ctx_text}\n\n{user_message}")]

    runnable_config = RunnableConfig(
        recursion_limit=config.guardrails.max_iterations,
        configurable={"thread_id": thread_id or str(uuid.uuid4())},
    )

    start = time.perf_counter()
    result = agent.invoke({"messages": messages}, config=runnable_config)
    elapsed = round(time.perf_counter() - start, 3)

    # Extract final AI message
    ai_messages = [m for m in result["messages"] if hasattr(m, "content") and m.__class__.__name__ == "AIMessage"]
    output_text = ai_messages[-1].content if ai_messages else ""

    # Collect tool calls from all messages
    tool_calls = []
    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)

    # Token accounting (available via usage_metadata on response messages)
    input_tokens = output_tokens = 0
    for msg in result["messages"]:
        meta = getattr(msg, "usage_metadata", None)
        if meta:
            input_tokens += meta.get("input_tokens", 0)
            output_tokens += meta.get("output_tokens", 0)

    return {
        "output": output_text,
        "messages": [
            {"role": m.__class__.__name__.replace("Message", "").lower(), "content": str(m.content)}
            for m in result["messages"]
        ],
        "thread_id": runnable_config["configurable"]["thread_id"],
        "tool_calls": [
            {"name": tc.get("name", ""), "args": tc.get("args", {})}
            for tc in tool_calls
        ],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "elapsed_seconds": elapsed,
        "blocked": False,
    }
