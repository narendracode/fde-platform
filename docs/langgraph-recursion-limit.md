# LangGraph Recursion Limit — Concept Guide

## What it is

`recursion_limit` is a **step counter**, not Python call-stack depth.
Every time any node in the graph executes, the counter increments by one.
When it reaches the limit before the graph arrives at `__end__`, LangGraph
raises `GraphRecursionError`.

```
GraphRecursionError: Recursion limit of N reached without hitting a stop condition.
```

---

## The ReAct graph structure

The `create_agent` harness compiles a two-node graph:

```
     ┌─────────┐
 ──► │  agent  │ ◄──────────────────┐
     └────┬────┘                    │
          │ decides to call a tool  │
          ▼                        │
     ┌─────────┐                   │
     │  tools  │ ──────────────────┘
     └─────────┘
          │ no more tools needed
          ▼
        __end__
```

Every arrow is one **step**. Each tool use therefore costs **2 steps**:
one for the LLM deciding to call it, one for the tool actually running.

---

## Step-by-step breakdown for a typical run

Using the pharma outreach agent as a concrete example
(list retailers → filter prospects → send 6 emails):

```
Step 1   agent  → LLM decides: call list_retailers
Step 2   tools  → list_retailers executes, returns JSON
Step 3   agent  → LLM decides: call filter_prospects
Step 4   tools  → filter_prospects executes, returns 6 prospects
Step 5   agent  → LLM decides: call send_email (prospect 1)
Step 6   tools  → send_email executes
Step 7   agent  → LLM decides: call send_email (prospect 2)
Step 8   tools  → send_email executes
Step 9   agent  → LLM decides: call send_email (prospect 3)
Step 10  tools  → send_email executes
Step 11  agent  → LLM decides: call send_email (prospect 4)
Step 12  tools  → send_email executes
Step 13  agent  → LLM decides: call send_email (prospect 5)
Step 14  tools  → send_email executes
Step 15  agent  → LLM decides: call send_email (prospect 6)
Step 16  tools  → send_email executes
Step 17  agent  → no more tools needed, writes final answer
         __end__
```

Total: **17 steps**.

With `recursion_limit = 5`, the graph dies at step 5 — right as it
is about to send the first email. The filter ran fine; the emails never went out.

---

## The formula

```
steps needed = 1                          (first LLM call)
             + (number of tool calls × 2) (each tool = decision + execution)
             + 1                          (final LLM call to write the answer)
```

### Examples

| Task | Tool calls | Steps needed |
|---|---|---|
| List → filter → send 6 emails | 8 | 1 + 16 + 1 = **18** |
| List → filter → send 20 emails | 22 | 1 + 44 + 1 = **46** |
| Web search + summarise 3 pages | 4 | 1 + 8 + 1 = **10** |
| Simple Q&A, no tools | 0 | 1 + 0 + 1 = **2** |

---

## When you will hit the limit

| Scenario | Why the limit is reached |
|---|---|
| Sending N emails in a loop | Each email is a separate tool call — steps grow linearly with N |
| Web search across multiple pages | Each page fetch = 2 steps |
| Agent retries after a tool error | Each retry attempt = 2 extra steps |
| Chained reasoning (think → search → refine) | Extra LLM calls accumulate even without external tools |
| Multi-step data pipelines | Every transform that goes through a tool adds 2 steps |

---

## Where the limit is configured

The `max_iterations` field in the agent's YAML maps directly to `recursion_limit`:

```yaml
# agents/configs/my-agent.yaml
guardrails:
  max_iterations: 25   # becomes recursion_limit in RunnableConfig
```

```python
# src/fde_agent/agent/react_agent.py
runnable_config = RunnableConfig(
    recursion_limit=config.guardrails.max_iterations,
    ...
)
```

---

## Sizing the limit

A practical rule:

```
max_iterations = (expected maximum number of tool calls × 2) + 5
```

The `+ 5` provides headroom for:
- The opening and closing LLM calls (2 steps)
- One unexpected retry (2 steps)
- One extra reasoning step (1 step)

### Sizing for the pharma outreach agent

| Scenario | Tool calls | Formula | Recommended limit |
|---|---|---|---|
| Delhi NCR (~10 retailers, ~6 prospects) | 8 | 16 + 5 | **25** |
| Large region (~50 retailers, ~30 prospects) | 32 | 64 + 5 | **70** |

> If the number of prospects is variable and unbounded, consider batching
> emails in groups inside the `send_email` tool itself rather than having
> the agent loop over them one by one. That collapses N email tool calls
> into 1, keeping the step count fixed regardless of prospect count.

---

## Quick reference

| `recursion_limit` value | What it means in practice |
|---|---|
| 5 | Handles at most ~1 tool call before crashing on any real workflow |
| 15 (platform YAML default) | Safe for up to ~6 tool calls — fine for simple lookup + filter tasks |
| 25 | Safe for up to ~10 tool calls — covers most single-region outreach runs |
| 50+ | Needed for bulk operations or agents that loop over large lists |
