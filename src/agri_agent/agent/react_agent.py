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
from langchain.agents import create_agent

from agri_agent.agent.tools import get_tools_for_config
from agri_agent.config.loader import AgentConfig
from agri_agent.config.settings import settings
from agri_agent.telemetry import current_trace_id, get_tracer, jaeger_url

_log = logging.getLogger(__name__)
_tracer = get_tracer("agri_agent.agent")

# ── LangSmith helpers ─────────────────────────────────────────────────────────

# Shared lazy Client (avoids re-authenticating on every run).
_ls_client: Any = None
# Cached URL template: "…/o/{tenant}/projects/p/{proj}/r/{run_id}"
_ls_url_template: str | None = None


def _get_ls_client() -> Any:
    global _ls_client
    if _ls_client is None and settings.langchain_api_key:
        try:
            from langsmith import Client
            _ls_client = Client(api_key=settings.langchain_api_key)
        except Exception as exc:
            _log.error("LangSmith Client init failed: %s", exc)
    return _ls_client


class _RunRef:
    """Duck-type shim — Client.get_run_url() only accesses .id."""
    def __init__(self, run_id: str) -> None:
        self.id = uuid.UUID(run_id)


def _wait_traces() -> None:
    """Flush LangSmith's background upload queue before reading run data."""
    try:
        from langsmith import wait_for_all_tracers
        wait_for_all_tracers()
        return
    except (ImportError, AttributeError):
        pass
    try:
        from langchain.callbacks.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
        return
    except (ImportError, AttributeError):
        pass
    time.sleep(1.5)


def _langsmith_url(run_id: str) -> str | None:
    """Return a LangSmith trace URL. Builds a template on first call (one API
    round-trip) then just substitutes the run ID on subsequent calls."""
    global _ls_url_template

    client = _get_ls_client()
    if not client or not settings.langchain_project:
        return None

    if _ls_url_template is None:
        _DUMMY = "00000000-0000-0000-0000-000000000000"
        try:
            url = client.get_run_url(run=_RunRef(_DUMMY),
                                     project_name=settings.langchain_project)
            _ls_url_template = url.replace(_DUMMY, "{run_id}")
        except Exception as exc:
            _log.error("LangSmith URL setup failed: %s", exc)
            return None

    return _ls_url_template.format(run_id=run_id)


def _cost_from_langsmith(run_id: str) -> float:
    """Read the cost LangSmith computed from the run's token usage.

    Flushes the background trace uploader first so the run is guaranteed to
    exist in LangSmith when we read it back. Returns 0.0 on any failure.
    """
    client = _get_ls_client()
    if not client:
        return 0.0
    _wait_traces()
    try:
        run = client.read_run(run_id)
        return float(run.total_cost or 0.0)
    except Exception as exc:
        _log.warning("Could not read LangSmith cost for run %s: %s", run_id, exc)
        return 0.0


def _metrics_from_langsmith(run_id: str) -> dict[str, Any]:
    """Read cost + token counts from a LangSmith run in a single API call.

    Used by supervisor runs where token counts can't be read from message
    usage_metadata (supervisor and worker nodes both append synthetic AIMessages).
    Returns zeroed dict on any failure.
    """
    client = _get_ls_client()
    if not client:
        return {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
    _wait_traces()
    try:
        run = client.read_run(run_id)
        return {
            "cost_usd": float(run.total_cost or 0.0),
            "input_tokens": int(run.prompt_tokens or 0),
            "output_tokens": int(run.completion_tokens or 0),
        }
    except Exception as exc:
        _log.warning("Could not read LangSmith metrics for run %s: %s", run_id, exc)
        return {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}


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

    compiled = create_agent(
        model=model,
        tools=tools,
        system_prompt=config.system_prompt.strip(),
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
    with _tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("agent.name", config.name)
        span.set_attribute("agent.model.name", config.model.name)
        span.set_attribute("agent.model.provider", config.model.provider)
        span.set_attribute("message.length", len(user_message))

        # ── Guardrail: blocked patterns ───────────────────────────────────────
        matched = _check_blocked_patterns(
            user_message, config.guardrails.blocked_patterns
        )
        if matched:
            span.set_attribute("agent.blocked", True)
            span.set_attribute("agent.blocked_pattern", matched)
            _tid = current_trace_id()
            return {
                "output": f"Request blocked by guardrail (matched pattern: '{matched}').",
                "messages": [],
                "thread_id": thread_id or str(uuid.uuid4()),
                "tool_calls": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "elapsed_seconds": 0.0,
                "blocked": True,
                "otel_trace_id": _tid,
                "otel_trace_url": jaeger_url(_tid),
            }

        # ── Supervisor agents: dispatch inside this span so OTel trace is live ─
        if config.type == "supervisor":
            from agri_agent.agent.supervisor_agent import run_supervisor
            result = run_supervisor(config, user_message, extra_context)
            span.set_attribute("tokens.input", result.get("input_tokens", 0))
            span.set_attribute("tokens.output", result.get("output_tokens", 0))
            span.set_attribute("cost.usd", result.get("cost_usd", 0.0))
            span.set_attribute("tool.count", 0)
            span.set_attribute("elapsed.seconds", result.get("elapsed_seconds", 0.0))
            span.set_attribute("agent.blocked", False)
            return result

        agent = build_agent(config)

        # Resolve declared inputs: validate required params, apply defaults, cast types.
        # Falls back to the raw dict when the agent declares no inputs schema.
        resolved_context: dict[str, Any] = {}
        if extra_context:
            if config.inputs:
                resolved_context = config.resolve_context(extra_context)
            else:
                resolved_context = extra_context

        # Build message — inject resolved context and feature flags as clearly
        # labelled blocks so the LLM can reliably read each named parameter.
        parts: list[str] = []
        if resolved_context:
            ctx_lines = "\n".join(f"  {k}: {v}" for k, v in resolved_context.items())
            parts.append(f"[Runtime context]\n{ctx_lines}")
        if config.feature_flags:
            flag_lines = "\n".join(f"  {k}: {v}" for k, v in config.feature_flags.items())
            parts.append(f"[Feature flags]\n{flag_lines}")
        parts.append(f"[Task]\n{user_message}")
        messages: list = [HumanMessage(content="\n\n".join(parts))]

        # Use a fixed run_id so the LangSmith root trace ID is stable and storable.
        ls_run_id = uuid.uuid4()
        span.set_attribute("langsmith.run_id", str(ls_run_id))

        runnable_config = RunnableConfig(
            recursion_limit=config.guardrails.max_iterations,
            configurable={"thread_id": thread_id or str(uuid.uuid4())},
            run_id=ls_run_id,
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

        # Cost + trace URL — both sourced from LangSmith.
        # _cost_from_langsmith() flushes the trace uploader first; _langsmith_url()
        # then reuses the already-uploaded run (no extra wait needed).
        cost_usd: float = 0.0
        langsmith_trace_url: str | None = None
        if config.observability.langsmith_tracing:
            cost_usd = _cost_from_langsmith(str(ls_run_id))
            langsmith_trace_url = _langsmith_url(str(ls_run_id))

        # OTel span attributes — set after we have all the numbers.
        span.set_attribute("tokens.input", input_tokens)
        span.set_attribute("tokens.output", output_tokens)
        span.set_attribute("cost.usd", cost_usd)
        span.set_attribute("tool.count", len(tool_calls))
        span.set_attribute("elapsed.seconds", elapsed)
        span.set_attribute("agent.blocked", False)

        otel_trace_id = current_trace_id()
        otel_trace_url = jaeger_url(otel_trace_id)

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
            "otel_trace_id": otel_trace_id,
            "otel_trace_url": otel_trace_url,
            "blocked": False,
        }
