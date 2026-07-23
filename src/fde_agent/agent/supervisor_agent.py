"""Supervisor / Orchestrator-Worker agent pattern for LangGraph.

Architecture
────────────
The supervisor is a plain LLM call (with structured output) that decides
which worker to run next or when the overall task is done.

Workers are standard agents built with create_agent (langchain.agents) — each one is a
compiled LangGraph sub-graph.  They receive a plain instruction string,
do their work using their own tools, and return a result.

Graph topology
──────────────
    START
      │
      ▼
  supervisor  ──[conditional]──► order-analyst ──►┐
      ▲                                            │
      │         ──[conditional]──► outreach-analyst►┤
      │                                            │
      └────────────────────────────────────────────┘
                                                   │
      ──[conditional: FINISH or max_rounds]──► END

State
─────
  messages          full conversation history (human task + worker results + final answer)
  next_worker       set by supervisor; "" means FINISH
  next_instruction  the specific instruction the supervisor sends to the next worker
  rounds            safety counter compared against routing.max_rounds
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal, TypedDict, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain.agents import create_agent
from pydantic import BaseModel

from fde_agent.agent.react_agent import _build_model
from fde_agent.agent.tools import get_tools_for_config
from fde_agent.config.loader import AgentConfig, load_agent_config

_log = logging.getLogger(__name__)


# ── Shared graph state ────────────────────────────────────────────────────────

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    next_worker: str        # "" = FINISH; otherwise a worker key from config.workers
    next_instruction: str   # the instruction the supervisor wants to give the next worker
    next_context: dict      # structured key-value context injected as [Runtime context] for the next worker
    pipeline_context: dict  # immutable initial extra_context — auto-merged into every worker call
    rounds: int             # incremented each time the supervisor node runs
    worker_stats: list      # per-invocation token/cost tracking for each worker call
    # Verification loop ────────────────────────────────────────────────────────
    verification_retries: int   # Phase 1: times code grader has rejected output
    grader_flags: list          # issue codes from the most recent grader run
    grader_verdict: str         # "pass" | "fail" | "escalate" — drives verifier routing
    model_grader_retries: int   # Phase 2: times model grader has rejected reasoning


# ── Supervisor routing decision (structured output) ───────────────────────────

class SupervisorDecision(BaseModel):
    """The supervisor LLM must return one of these two shapes."""
    action: Literal["call_worker", "finish"]
    worker: str | None = None   # must match a key in the workers dict when calling
    instruction: str = ""       # task instruction for the worker, or final summary
    context: dict[str, Any] = {}  # structured params forwarded as [Runtime context] to the worker
    reasoning: str = ""         # supervisor's reasoning (stored in messages, useful for debugging)


# ── Build helpers ─────────────────────────────────────────────────────────────

def _build_worker_agent(config: AgentConfig):
    """Build a compiled ReAct agent for a worker from its AgentConfig."""
    model = _build_model(config.model)
    tools = get_tools_for_config(config.tools)
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=config.system_prompt.strip(),
        name=config.name,
    )


def _worker_node_fn(worker_agent, worker_name: str, worker_config: AgentConfig):
    """Return a node function that runs a worker agent and returns its result."""

    def worker_node(state: SupervisorState) -> dict[str, Any]:
        instruction = state.get("next_instruction", "")
        # pipeline_context (immutable initial extra_context) provides ground-truth values
        # such as report_id and deal_id.  next_context from the supervisor LLM can add or
        # override fields, but placeholders like "<report_id>" are masked by the real value.
        pipeline_ctx = state.get("pipeline_context") or {}
        llm_ctx = state.get("next_context") or {}
        # LLM-generated context can add new keys but must never override ground-truth
        # pipeline_context values (report_id, deal_id, etc.).  Also drop placeholders.
        extra_from_llm = {
            k: v for k, v in llm_ctx.items()
            if k not in pipeline_ctx
            and not (isinstance(v, str) and v.startswith("<") and v.endswith(">"))
        }
        worker_context = {**pipeline_ctx, **extra_from_llm}
        _log.info("supervisor → worker '%s': %s", worker_name, instruction[:120])

        # Build message — same block order as react_agent.run_agent:
        # [Runtime context] → [Feature flags] → [Task]
        parts: list[str] = []
        if worker_context:
            ctx_lines = "\n".join(f"  {k}: {v}" for k, v in worker_context.items())
            parts.append(f"[Runtime context]\n{ctx_lines}")
        if worker_config.feature_flags:
            flag_lines = "\n".join(f"  {k}: {v}" for k, v in worker_config.feature_flags.items())
            parts.append(f"[Feature flags]\n{flag_lines}")
        parts.append(f"[Task]\n{instruction}")

        from langchain_core.runnables import RunnableConfig as _RC
        result = worker_agent.invoke(
            {"messages": [HumanMessage(content="\n\n".join(parts))]},
            config=_RC(recursion_limit=worker_config.guardrails.max_iterations),
        )

        # Extract final AI text from the worker's message list
        ai_msgs = [m for m in result["messages"]
                   if m.__class__.__name__ == "AIMessage" and m.content]
        result_text = ai_msgs[-1].content if ai_msgs else "(no output)"
        # Log whether tools were used
        has_tool_calls = any(
            isinstance(m.content, list) for m in result["messages"]
            if m.__class__.__name__ in ("AIMessage", "ToolMessage")
        )
        _log.info("worker '%s' tool_calls_used=%s msg_count=%d", worker_name, has_tool_calls, len(result["messages"]))

        # Sum token usage from usage_metadata on every AI message in the worker result.
        # LangChain populates usage_metadata for Anthropic and OpenAI models.
        in_tok = out_tok = 0
        for msg in result.get("messages", []):
            meta = getattr(msg, "usage_metadata", None) or {}
            in_tok += meta.get("input_tokens", 0)
            out_tok += meta.get("output_tokens", 0)

        existing_stats = list(state.get("worker_stats") or [])
        stat: dict[str, Any] = {
            "name": worker_name,
            "order": len(existing_stats) + 1,
            "instruction": instruction[:300],
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }

        _log.info("worker '%s' finished: in=%d out=%d tokens — %s…", worker_name, in_tok, out_tok, result_text[:80])

        return {
            "messages": [
                AIMessage(
                    content=f"[{worker_name} result]\n{result_text}",
                    name=worker_name,
                )
            ],
            "worker_stats": existing_stats + [stat],
        }

    return worker_node


def _supervisor_node_fn(supervisor_config: AgentConfig, workers: dict[str, dict]):
    """Return the supervisor node function.

    workers = {worker_key: {"config": AgentConfig, "agent": compiled_graph, "description": str}}
    """
    model = _build_model(supervisor_config.model)
    router = model.with_structured_output(SupervisorDecision, method="function_calling")

    # Build the worker menu once (it doesn't change between calls)
    worker_menu = "\n".join(
        f"  - {key}: {info['description']}"
        for key, info in workers.items()
    )
    max_rounds = supervisor_config.routing.max_rounds

    def supervisor_node(state: SupervisorState) -> dict[str, Any]:
        rounds = state.get("rounds", 0)

        # Hard stop — prevents infinite loops
        if rounds >= max_rounds:
            _log.warning("supervisor hit max_rounds=%d, forcing FINISH", max_rounds)
            return {
                "messages": [AIMessage(
                    content=f"[Supervisor] Reached maximum rounds ({max_rounds}). Stopping.",
                    name="supervisor",
                )],
                "next_worker": "",
                "next_instruction": "",
                "next_context": {},
                "rounds": rounds + 1,
            }

        # Format conversation history for the routing call
        history_parts = []
        for msg in state["messages"]:
            role = msg.__class__.__name__.replace("Message", "")
            name = getattr(msg, "name", None)
            label = f"[{name}]" if name else f"[{role}]"
            history_parts.append(f"{label} {msg.content}")
        history = "\n\n".join(history_parts)

        # Build the routing prompt
        routing_prompt = f"""{supervisor_config.system_prompt.strip()}

Available workers:
{worker_menu}

Conversation so far:
{history}

Decide what to do next.
- If a worker still needs to be called: action=call_worker, worker=<key>, instruction=<specific task>, context={{key: value, ...}}
- If the overall task is complete: action=finish, instruction=<summary of what was accomplished>

The `context` dict carries structured parameters that the worker needs as named inputs
(e.g. region, batch_size). The worker reads these from its [Runtime context] block.
Always populate context with the required inputs for the chosen worker.

Be concise in your reasoning. Always provide an instruction."""

        decision: SupervisorDecision = router.invoke([HumanMessage(content=routing_prompt)])

        _log.info(
            "supervisor round=%d decision=%s worker=%s reasoning=%s",
            rounds + 1, decision.action, decision.worker, decision.reasoning[:80],
        )

        if decision.action == "finish" or not decision.worker:
            return {
                "messages": [AIMessage(
                    content=decision.instruction or "Task complete.",
                    name="supervisor",
                )],
                "next_worker": "",
                "next_instruction": "",
                "next_context": {},
                "rounds": rounds + 1,
            }

        return {
            "messages": [AIMessage(
                content=f"[Supervisor → {decision.worker}] {decision.instruction} (reasoning: {decision.reasoning})",
                name="supervisor",
            )],
            "next_worker": decision.worker,
            "next_instruction": decision.instruction,
            "next_context": decision.context,
            "rounds": rounds + 1,
        }

    return supervisor_node


def _route_from_supervisor(state: SupervisorState) -> str:
    """Conditional edge: routes to the chosen worker or ends the graph."""
    return state.get("next_worker") or END


# ── Verification loop (Phase 1) ───────────────────────────────────────────────

def _make_verifier_router(verification_worker: str):
    """Return a conditional-edge function that routes from the verifier node."""
    def _route(state: SupervisorState) -> str:
        verdict = state.get("grader_verdict", "pass")
        if verdict == "fail":
            return verification_worker   # retry the evaluator
        return "supervisor"              # pass or escalate → let supervisor finish
    return _route


def _make_verifier_node(
    verification_worker: str,
    max_retries: int,
    model_grader_enabled: bool = False,
    model_grader_max_retries: int = 1,
    model_grader_model: str = "gpt-4o-mini",
):
    """Return a LangGraph node function that runs the Propguru quality gates.

    Phase 1 (code grader) always runs first.
    Phase 2 (model grader) runs only after Phase 1 passes, if enabled.

    On PASS  → routes back to supervisor (which will call finish).
    On FAIL  → dismisses stale proposal, injects feedback, retries evaluator.
    On ESCALATE (max retries exceeded) → persists flags, routes to supervisor
              so the HITL proposal reaches the analyst with a grader-flag marker.
    """
    from fde_agent.agent.propguru_verifier import (
        run_propguru_code_grader,
        run_propguru_model_grader,
        save_grader_result,
        dismiss_stale_action,
        reset_report_to_draft,
        extract_report_id,
        extract_action_id,
    )
    from fde_agent.config.settings import settings as _settings

    def _do_dismiss_and_reset(action_id: str | None, report_id: str, note: str) -> None:
        if action_id:
            dismiss_stale_action(action_id, note, _settings.api_base_url, _settings.api_key)
        reset_report_to_draft(report_id, _settings.api_base_url, _settings.api_key)

    def verifier_node(state: SupervisorState) -> dict[str, Any]:
        retries = state.get("verification_retries", 0)
        mg_retries = state.get("model_grader_retries", 0)
        messages = state.get("messages", [])

        report_id = extract_report_id(messages)
        if not report_id:
            _log.warning("verifier: could not extract report_id from messages — passing through")
            return {
                "grader_verdict": "pass",
                "grader_flags": [],
                "messages": [AIMessage(
                    content="[Verifier] report_id not found in message history — skipping checks.",
                    name="verifier",
                )],
            }

        action_id = extract_action_id(messages)

        # ── Phase 1: Code Grader ───────────────────────────────────────────────
        _log.info(
            "verifier: code-grading report %s (attempt %d/%d)",
            report_id, retries + 1, max_retries + 1,
        )
        code_result = run_propguru_code_grader(report_id, _settings.api_base_url, _settings.api_key)

        if not code_result.passed:
            flag_str = ", ".join(code_result.flags)

            # Save before deciding whether to escalate or retry
            save_grader_result(
                report_id, retries + 1, code_result.flags,
                _settings.api_base_url, _settings.api_key,
                model_grader_retries=mg_retries,
            )

            if retries >= max_retries:
                _log.warning(
                    "verifier: ESCALATE (code) — report %s failed %d time(s): %s",
                    report_id, retries + 1, flag_str,
                )
                return {
                    "grader_verdict": "escalate",
                    "grader_flags": code_result.flags,
                    "messages": [AIMessage(
                        content=(
                            f"[Verifier] GRADER_FLAGGED — report {report_id} did not pass "
                            f"code-grader checks after {retries + 1} attempt(s). Flags: {flag_str}. "
                            f"Escalating to HITL with grader-flag marker."
                        ),
                        name="verifier",
                    )],
                }

            _log.info(
                "verifier: FAIL code (retry %d/%d) — report %s: %s",
                retries + 1, max_retries, report_id, flag_str,
            )
            _do_dismiss_and_reset(
                action_id, report_id,
                f"Code grader rejected (attempt {retries + 1}/{max_retries + 1}): {flag_str}",
            )
            return {
                "grader_verdict": "fail",
                "grader_flags": code_result.flags,
                "verification_retries": retries + 1,
                "messages": [
                    AIMessage(
                        content=(
                            f"[Verifier] FAIL (attempt {retries + 1}/{max_retries + 1}) — "
                            f"report {report_id} did not pass code-grader checks: {flag_str}."
                        ),
                        name="verifier",
                    ),
                    HumanMessage(content=code_result.feedback),
                ],
            }

        # ── Phase 2: Model Grader (only runs after code grader passes) ─────────
        if not model_grader_enabled:
            _log.info("verifier: PASS — report %s (model grader disabled)", report_id)
            save_grader_result(
                report_id, retries, [],
                _settings.api_base_url, _settings.api_key,
                model_grader_retries=mg_retries,
            )
            return {
                "grader_verdict": "pass",
                "grader_flags": [],
                "messages": [AIMessage(
                    content=f"[Verifier] PASS — report {report_id} cleared all code-grader checks.",
                    name="verifier",
                )],
            }

        _log.info(
            "verifier: model-grading report %s (attempt %d/%d)",
            report_id, mg_retries + 1, model_grader_max_retries + 1,
        )
        mg_result = run_propguru_model_grader(
            report_id, action_id,
            _settings.api_base_url, _settings.api_key,
            model_grader_model,
        )
        combined_flags = mg_result.flags

        save_grader_result(
            report_id, retries, combined_flags,
            _settings.api_base_url, _settings.api_key,
            model_grader_retries=mg_retries + (0 if mg_result.passed else 1),
        )

        if mg_result.passed:
            score_str = (
                f"score {mg_result.overall_score:.1f}/10"
                if mg_result.overall_score > 0
                else "skipped (no reasoning)"
            )
            _log.info("verifier: PASS — report %s model-grader %s", report_id, score_str)
            return {
                "grader_verdict": "pass",
                "grader_flags": combined_flags,
                "messages": [AIMessage(
                    content=(
                        f"[Verifier] PASS — report {report_id} cleared code-grader and "
                        f"model-grader checks ({score_str})."
                    ),
                    name="verifier",
                )],
            }

        # Model grader FAIL
        mg_flag_str = ", ".join(mg_result.flags)
        if mg_retries >= model_grader_max_retries:
            _log.warning(
                "verifier: ESCALATE (model) — report %s failed %d time(s): score=%.2f",
                report_id, mg_retries + 1, mg_result.overall_score,
            )
            return {
                "grader_verdict": "escalate",
                "grader_flags": combined_flags,
                "model_grader_retries": mg_retries + 1,
                "messages": [AIMessage(
                    content=(
                        f"[Verifier] REASONING_FLAGGED — report {report_id} reasoning scored "
                        f"{mg_result.overall_score:.1f}/10 after {mg_retries + 1} attempt(s). "
                        f"Escalating to HITL with reasoning-quality marker."
                    ),
                    name="verifier",
                )],
            }

        _log.info(
            "verifier: FAIL model (retry %d/%d) — report %s score=%.2f",
            mg_retries + 1, model_grader_max_retries, report_id, mg_result.overall_score,
        )
        _do_dismiss_and_reset(
            action_id, report_id,
            f"Model grader rejected reasoning (attempt {mg_retries + 1}/{model_grader_max_retries + 1}): "
            f"score {mg_result.overall_score:.1f}/10",
        )
        return {
            "grader_verdict": "fail",
            "grader_flags": combined_flags,
            "model_grader_retries": mg_retries + 1,
            "messages": [
                AIMessage(
                    content=(
                        f"[Verifier] FAIL model-grader (attempt {mg_retries + 1}/"
                        f"{model_grader_max_retries + 1}) — "
                        f"report {report_id} reasoning score {mg_result.overall_score:.1f}/10."
                    ),
                    name="verifier",
                ),
                HumanMessage(content=mg_result.feedback),
            ],
        }

    return verifier_node


# ── Public builder ────────────────────────────────────────────────────────────

def build_supervisor_graph(config: AgentConfig):
    """Build and compile the supervisor + workers graph from an AgentConfig.

    The config must have type="supervisor" and at least one entry in workers[].
    Each worker is loaded by name from the agent configs directory.

    Returns a compiled LangGraph StateGraph ready to .invoke() / .stream().
    """
    if not config.workers:
        raise ValueError(f"Supervisor '{config.name}' has no workers defined in YAML.")

    # ── Load and build all workers ────────────────────────────────────────────
    workers: dict[str, dict] = {}
    for worker_ref in config.workers:
        try:
            worker_config = load_agent_config(worker_ref.agent)
        except FileNotFoundError:
            raise ValueError(
                f"Supervisor '{config.name}' references worker '{worker_ref.agent}' "
                f"but no matching agent config was found."
            )
        workers[worker_ref.agent] = {
            "config": worker_config,
            "agent": _build_worker_agent(worker_config),
            "description": worker_ref.description or worker_config.description,
        }
        _log.info("supervisor '%s' loaded worker '%s'", config.name, worker_ref.agent)

    # ── Build the graph ───────────────────────────────────────────────────────
    graph = StateGraph(SupervisorState)

    # Supervisor node
    graph.add_node("supervisor", _supervisor_node_fn(config, workers))

    # Worker nodes
    for worker_key, worker_info in workers.items():
        graph.add_node(
            worker_key,
            _worker_node_fn(worker_info["agent"], worker_key, worker_info["config"]),
        )

    # Verification loop config — optional feature driven by feature_flags in the YAML
    verification_enabled = bool(config.feature_flags.get("verification_loop", False))
    verification_worker = config.feature_flags.get("verification_after_worker", "")
    max_retries = int(config.feature_flags.get("verification_max_retries", 2))
    # Phase 2: model grader feature flags
    mg_enabled = bool(config.feature_flags.get("verification_model_grader_enabled", False))
    mg_max_retries = int(config.feature_flags.get("verification_model_grader_max_retries", 1))
    mg_model = str(config.feature_flags.get(
        "verification_model_grader_model", "gpt-4o-mini"
    ))

    if verification_enabled and verification_worker and verification_worker in workers:
        _log.info(
            "supervisor '%s': verification loop enabled after '%s' "
            "(code_max_retries=%d model_grader=%s)",
            config.name, verification_worker, max_retries, mg_enabled,
        )
        graph.add_node(
            "__verifier__",
            _make_verifier_node(
                verification_worker, max_retries,
                mg_enabled, mg_max_retries, mg_model,
            ),
        )

    # Edges
    graph.add_edge(START, "supervisor")

    # Conditional routing from supervisor → worker or END
    graph.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {worker_key: worker_key for worker_key in workers} | {END: END},
    )

    # Worker → next node: verification target goes to verifier; others go back to supervisor
    for worker_key in workers:
        if verification_enabled and worker_key == verification_worker and verification_worker in workers:
            graph.add_edge(worker_key, "__verifier__")
        else:
            graph.add_edge(worker_key, "supervisor")

    # Verifier conditional edge: fail → retry evaluator; pass/escalate → supervisor
    if verification_enabled and verification_worker and verification_worker in workers:
        graph.add_conditional_edges(
            "__verifier__",
            _make_verifier_router(verification_worker),
            {"supervisor": "supervisor", verification_worker: verification_worker},
        )

    return graph.compile()


# ── High-level runner (mirrors react_agent.run_agent interface) ───────────────

def run_supervisor(
    config: AgentConfig,
    user_message: str,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the supervisor graph synchronously and return a structured result.

    Returns the same keys as react_agent.run_agent() so callers don't need to
    know which agent type they're running.
    Guardrail checks are handled by run_agent before this is called.
    """
    import time, uuid
    from langchain_core.runnables import RunnableConfig
    from fde_agent.agent.react_agent import _metrics_from_langsmith, _langsmith_url
    from fde_agent.telemetry import current_trace_id, jaeger_url

    graph = build_supervisor_graph(config)

    # Build the initial human message with optional context
    parts: list[str] = []
    if extra_context:
        ctx_lines = "\n".join(f"  {k}: {v}" for k, v in (extra_context or {}).items())
        parts.append(f"[Runtime context]\n{ctx_lines}")
    parts.append(f"[Task]\n{user_message}")
    initial_message = HumanMessage(content="\n\n".join(parts))

    initial_state: SupervisorState = {
        "messages": [initial_message],
        "next_worker": "",
        "next_instruction": "",
        "next_context": {},
        "pipeline_context": dict(extra_context) if extra_context else {},
        "rounds": 0,
        "worker_stats": [],
        "verification_retries": 0,
        "grader_flags": [],
        "grader_verdict": "",
        "model_grader_retries": 0,
    }

    # Assign a stable run_id so LangSmith can aggregate the full trace cost
    ls_run_id = uuid.uuid4()
    runnable_config = RunnableConfig(
        recursion_limit=config.guardrails.max_iterations,
        run_id=ls_run_id,
    )

    start = time.perf_counter()
    result = graph.invoke(initial_state, config=runnable_config)
    elapsed = round(time.perf_counter() - start, 3)

    # The final output is the last AIMessage from the supervisor
    supervisor_msgs = [
        m for m in result["messages"]
        if m.__class__.__name__ == "AIMessage" and getattr(m, "name", "") == "supervisor"
    ]
    output_text = supervisor_msgs[-1].content if supervisor_msgs else "(no output)"

    # Collect all messages for the audit trail
    messages_summary = [
        {
            "role": getattr(m, "name", m.__class__.__name__.replace("Message", "").lower()),
            "content": str(m.content)[:500],
        }
        for m in result["messages"]
    ]

    # Per-worker stats captured by _worker_node_fn via usage_metadata
    worker_stats: list[dict] = list(result.get("worker_stats") or [])

    # Token counts and cost come from LangSmith — not from message usage_metadata.
    # All messages in the supervisor state are synthetic AIMessages constructed by
    # the supervisor/worker nodes; none carry usage_metadata.  LangSmith aggregates
    # prompt_tokens + completion_tokens across the full run tree (supervisor LLM +
    # all worker LLM calls) in a single read_run call.
    input_tokens = output_tokens = 0
    cost_usd = 0.0
    langsmith_trace_url = None
    if config.observability.langsmith_tracing:
        metrics = _metrics_from_langsmith(str(ls_run_id))
        input_tokens = metrics["input_tokens"]
        output_tokens = metrics["output_tokens"]
        cost_usd = metrics["cost_usd"]
        langsmith_trace_url = _langsmith_url(str(ls_run_id))

        # Enrich each worker stat with its own LangSmith child-run URL and cost.
        # LangGraph records each node execution as a child run named by node key.
        try:
            from fde_agent.agent.react_agent import _get_ls_client
            from fde_agent.config.settings import settings as _settings
            ls_client = _get_ls_client()
            if ls_client and worker_stats:
                worker_names = {w.agent for w in config.workers}
                child_iter = ls_client.list_runs(
                    project_name=_settings.langchain_project,
                    parent_run_id=str(ls_run_id),
                )
                child_runs = [r for r in child_iter if r.name in worker_names]
                child_runs.sort(key=lambda r: r.start_time or "")
                # Match child runs to stats in call order per worker name
                visit: dict[str, int] = {}
                for stat in worker_stats:
                    name = stat["name"]
                    idx = visit.get(name, 0)
                    same_name = [r for r in child_runs if r.name == name]
                    if idx < len(same_name):
                        cr = same_name[idx]
                        stat["langsmith_run_id"] = str(cr.id)
                        stat["langsmith_trace_url"] = _langsmith_url(str(cr.id))
                        if cr.total_cost:
                            stat["cost_usd"] = float(cr.total_cost)
                    visit[name] = idx + 1
        except Exception as _exc:
            _log.warning("Could not fetch worker child runs from LangSmith: %s", _exc)

    # OTel trace — valid because run_agent opens a span before calling us
    otel_trace_id = current_trace_id()
    otel_trace_url = jaeger_url(otel_trace_id)

    return {
        "output": output_text,
        "messages": messages_summary,
        "thread_id": str(uuid.uuid4()),
        "tool_calls": [],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "elapsed_seconds": elapsed,
        "langsmith_run_id": str(ls_run_id),
        "langsmith_trace_url": langsmith_trace_url,
        "otel_trace_id": otel_trace_id,
        "otel_trace_url": otel_trace_url,
        "blocked": False,
        "rounds": result.get("rounds", 0),
        "sub_agents": worker_stats,
        "verification_retries": result.get("verification_retries", 0),
        "grader_flags": result.get("grader_flags", []),
        "grader_verdict": result.get("grader_verdict", ""),
        "model_grader_retries": result.get("model_grader_retries", 0),
    }
