"""LangGraph ReAct agent — built from a declarative AgentConfig."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from agri_agent.agent.tools import get_tools_for_config
from agri_agent.config.loader import AgentConfig
from agri_agent.config.settings import settings

_log = logging.getLogger(__name__)

# Cached URL template built on first successful call: "…/o/{tenant}/projects/p/{proj}/r/{run_id}"
_langsmith_url_template: str | None = None


class _FakeRun:
    """Duck-type shim — langsmith.Client.get_run_url() only accesses .id."""
    def __init__(self, run_id: str) -> None:
        self.id = uuid.UUID(run_id)


def _langsmith_url(run_id: str) -> str | None:
    """Return a LangSmith trace URL by delegating URL construction to the SDK.

    Uses a dummy run ID on first call to prime a URL template, then substitutes
    the real run ID on every subsequent call — one API round-trip total.
    """
    global _langsmith_url_template

    api_key = settings.langchain_api_key
    project = settings.langchain_project
    if not api_key or not project:
        return None

    if _langsmith_url_template is None:
        _DUMMY = "00000000-0000-0000-0000-000000000000"
        try:
            from langsmith import Client
            client = Client(api_key=api_key)
            url = client.get_run_url(run=_FakeRun(_DUMMY), project_name=project)
            _langsmith_url_template = url.replace(_DUMMY, "{run_id}")
        except Exception as exc:
            _log.error("LangSmith URL setup failed: %s", exc)
            return None

    return _langsmith_url_template.format(run_id=run_id)

# Cost per 1M tokens (USD). Add new models here as needed.
_PRICING: dict[str, tuple[float, float]] = {
    # model-name: (input_per_1m, output_per_1m)
    "claude-sonnet-4-6":         (3.00,  15.00),
    "claude-opus-4-8":           (15.00, 75.00),
    "claude-haiku-4-5-20251001": (0.80,  4.00),
    "gpt-4o":                    (2.50,  10.00),
    "gpt-4o-mini":               (0.15,  0.60),
    "gpt-4-turbo":               (10.00, 30.00),
}


def _calc_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model_name)
    if not pricing:
        return 0.0
    in_cost, out_cost = pricing
    return round((input_tokens * in_cost + output_tokens * out_cost) / 1_000_000, 6)


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

    # Use a fixed run_id so the LangSmith root trace ID is stable and storable.
    ls_run_id = uuid.uuid4()
    runnable_config = RunnableConfig(
        recursion_limit=config.guardrails.max_iterations,
        configurable={"thread_id": thread_id or str(uuid.uuid4())},
        run_id=ls_run_id,
    )

    start = time.perf_counter()
    result = agent.invoke({"messages": messages}, config=runnable_config)
    elapsed = round(time.perf_counter() - start, 3)

    # Resolve LangSmith trace URL (SDK-independent, cached after first call).
    langsmith_trace_url: str | None = None
    if config.observability.langsmith_tracing:
        langsmith_trace_url = _langsmith_url(str(ls_run_id))

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

    cost_usd = _calc_cost(config.model.name, input_tokens, output_tokens)

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
        "cost_usd": cost_usd,
        "elapsed_seconds": elapsed,
        "langsmith_run_id": str(ls_run_id),
        "langsmith_trace_url": langsmith_trace_url,
        "blocked": False,
    }
