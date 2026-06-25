# Agent YAML Config Reference

Every agent is defined as a YAML file in `agents/configs/`.
All fields live under the top-level `agent:` key.

---

## `guardrails`

Controls hard limits on agent execution. Applied by `react_agent.py` before and during every run.

```yaml
guardrails:
  max_iterations: 15
  timeout_seconds: 120
  blocked_patterns:
    - "ignore previous instructions"
    - "jailbreak"
```

### `max_iterations`

| | |
|---|---|
| Type | `integer` |
| Default | `15` |
| Used as | `RunnableConfig(recursion_limit=...)` passed to LangGraph |

The maximum number of **graph steps** the agent may take before LangGraph raises
`GraphRecursionError`. This is a step counter, not call-stack depth.

Each tool use costs **2 steps** (one LLM decision node + one tool execution node).
The full run also needs 2 extra steps (opening and closing LLM calls).

**Sizing formula:**
```
max_iterations = (expected maximum tool calls × 2) + 5
```

The `+5` covers: opening call (1) + closing call (1) + one retry step (2) + headroom (1).

| Scenario | Typical tool calls | Recommended value |
|---|---|---|
| Simple Q&A, no tools | 0 | `5` |
| Single lookup + answer | 1–3 | `10–15` (default) |
| Multi-step research | 5–10 | `25–30` |
| Outreach loop (per-item emails) | 10–20 | `40–50` |

See `docs/langgraph-recursion-limit.md` for a full step-by-step breakdown.

---

### `timeout_seconds`

| | |
|---|---|
| Type | `integer` |
| Default | `120` |
| Used as | `RunnableConfig(timeout=...)` passed to LangGraph |

Maximum wall-clock time in seconds the agent may run before LangGraph cancels
the run with a timeout error. Protects against hung LLM calls or slow tool
responses blocking a worker indefinitely.

For agents that call slow external APIs or send many emails in a loop, increase
this proportionally (`timeout_seconds = max_iterations × avg_step_latency_seconds`).

---

### `blocked_patterns`

| | |
|---|---|
| Type | `list[string]` |
| Default | `[]` (empty — no patterns blocked) |
| Used as | Python `re.search(pattern, user_message, re.IGNORECASE)` |

A list of regular expression patterns. If the user's message matches any of them,
the agent is **not invoked at all** — the run is rejected immediately with status
`blocked` and no LLM call is made.

Each pattern is a standard Python regex evaluated with `re.IGNORECASE`.

```yaml
blocked_patterns:
  - "ignore previous instructions"   # prompt injection attempt
  - "jailbreak"                       # jailbreak keyword
  - "(?:rm|delete)\s+-rf"             # shell destructive command pattern
```

**Important:** Patterns are matched against the raw user message only, not against
tool outputs or the system prompt. They are a first-line defence against obvious
prompt injection, not a comprehensive security layer.

---

## `observability`

Controls what telemetry is emitted for this agent's runs. All four flags default
to `true` — you opt out by setting them to `false`.

```yaml
observability:
  langsmith_tracing: true
  log_inputs: true
  log_outputs: true
  log_tool_calls: true
```

### `langsmith_tracing`

| | |
|---|---|
| Type | `boolean` |
| Default | `true` |
| Condition | Only active when `LANGSMITH_TRACING=true` in `.env` AND this flag is `true` |

When enabled, every run is sent to LangSmith as a trace including: full
prompt/completion text, tool call inputs and outputs, token counts per step,
latency per step, and total cost (computed server-side by LangSmith).

After each run the platform reads back `run.total_cost` from LangSmith and stores
it as `cost_usd` in the `agent_runs` table. If this flag is `false` (or the
global `LANGSMITH_TRACING` env var is off), `cost_usd` is stored as `0.0`.

The run response includes `langsmith_run_id` and `langsmith_trace_url` (a clickable
deep-link) only when this is active.

Set to `false` for:
- Agents that handle sensitive data that must not leave your infrastructure
- Cost reduction in high-volume low-value pipelines
- Local development without a LangSmith API key

---

### `log_inputs`, `log_outputs`, `log_tool_calls`

| | |
|---|---|
| Type | `boolean` |
| Default | `true` |
| Status | **Declared but not yet implemented in the runtime** |

These three flags are defined in `AgentConfig` and accepted by the YAML parser,
but `react_agent.py` does not currently read them. They are reserved for a future
structured logging layer that will control whether the agent's input message,
output text, and tool call payloads are written to the platform's structured log
stream.

Until that layer is built:
- Setting them to `false` has **no effect** — the values are parsed but ignored.
- LangSmith still traces everything when `langsmith_tracing: true` regardless of
  these flags.

---

## Full example with all fields

```yaml
agent:
  name: my-agent
  description: "One sentence description."
  version: "1.0.0"

  guardrails:
    max_iterations: 25       # (2 tools × 2 steps) + 5 headroom × 3 expected calls
    timeout_seconds: 180     # 3 minutes — agent may call a slow external API
    blocked_patterns:
      - "ignore previous instructions"
      - "jailbreak"
      - "drop table"

  observability:
    langsmith_tracing: true   # send traces to LangSmith (requires LANGSMITH_TRACING=true in .env)
    log_inputs: true          # reserved — no effect yet
    log_outputs: true         # reserved — no effect yet
    log_tool_calls: true      # reserved — no effect yet
```
