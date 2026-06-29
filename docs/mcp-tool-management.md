# MCP for Tool Management — Analysis and Design

## 1. The Question

As the number of tools grows, the current single-registry pattern will become hard to manage.
Should we introduce **MCP (Model Context Protocol)** as the tool management layer?

This document answers: what MCP is, how it would change the architecture, when it becomes the right
answer, and what the concrete trade-offs are for this platform specifically.

---

## 2. Current Architecture and Its Actual Scaling Limits

### What we have today

```
agents/configs/*.yaml
  └── tools: [{name: "get_pending_orders", enabled: true}, ...]

src/agri_agent/agent/tools/
  ├── __init__.py       _TOOL_REGISTRY (flat dict, 11 tools)
  ├── calculator.py     (1 tool)
  ├── search.py         (1 tool — wraps Tavily)
  ├── dispatch.py       (5 tools — call /api/v1/orders)
  ├── outreach.py       (3 tools — mock retailer data + email)
  └── platform.py       (1 tool — propose_action, calls /api/v1/actions)

react_agent.py
  └── get_tools_for_config(agent.tools) → [BaseTool, ...] → create_react_agent(model, tools)
```

Every tool is:
- A Python `@tool`-decorated function in the same process as the agent runtime
- Imported at startup into a flat dictionary
- Enabled/disabled per agent in YAML by name

### Where this pattern breaks

| Scale trigger | Why it becomes a problem |
|---|---|
| **Multiple teams contributing tools** | All tools live in platform Python code. A pharma team adding `check_credit_limit` must open a PR against the agent platform repo — they don't own it and don't understand it. |
| **Domain-specific deployment cadence** | A bug in `get_pending_orders` requires redeploying the entire platform (`api` + `worker` containers). A fix that affects one tool redeploys everything. |
| **Tools that need their own secrets** | `send_email` needs SMTP credentials. `check_payment_status` needs a payment gateway key. Today these env vars go into the platform's `.env`. As tools multiply, the platform accumulates credentials it doesn't conceptually own. |
| **Non-Python tool implementations** | A data team writes their tools in Go or Node.js. Today they can't — all tools must be Python functions in this package. |
| **Tool reuse across AI applications** | A second AI application (e.g. a chatbot) wants to call the same order tools. Today it can't — they're embedded inside this platform. |

### Current scale assessment

11 tools across 5 files is **not yet a problem**. The pain points above are real but haven't
been hit yet. This is the right time to plan, not necessarily the right time to build.

---

## 3. What MCP Is

**Model Context Protocol (MCP)** is an open standard (by Anthropic) for connecting AI models
to external tools and data sources over a well-defined protocol.

It defines three primitives:
- **Tools** — callable functions (equivalent to LangChain tools)
- **Resources** — readable data (files, DB rows, API responses)
- **Prompts** — reusable prompt templates

We only care about **Tools** for this use case.

### How it works (simplified)

```
MCP Server (any language)         MCP Client (the agent runtime)
  ├── tools/list → [{name, schema}]  ← client discovers available tools
  ├── tools/call → {result}          ← client calls a specific tool
  └── transport: stdio | HTTP/SSE
```

A **MCP Server** exposes a set of tools over the protocol. It can be a Python process,
a Node.js service, a Go binary — anything that speaks the protocol.

A **MCP Client** connects to one or more servers, discovers their tools, and calls them
when the agent needs them.

### How it integrates with LangGraph

`langchain-mcp-adapters` is the bridge library. It converts MCP tools into LangChain
`BaseTool` objects — the same type that `create_react_agent` already accepts. From the
agent's perspective, MCP tools are **identical** to the current local tools.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({"orders": {"url": "http://orders-mcp:8001/mcp", "transport": "streamable_http"}}) as client:
    tools = client.get_tools()                      # → [BaseTool, BaseTool, ...]
    agent = create_react_agent(model, tools, ...)   # unchanged
```

The agent runtime changes minimally. The tools just come from network servers instead of
local imports.

---

## 4. Pros and Cons for This Platform

### Pros

**Decoupling — the primary benefit**

Domain teams own their tools entirely. The pharma order team owns `orders-mcp-server`.
The outreach team owns `outreach-mcp-server`. They can add, fix, and redeploy tools without
touching the agent platform. The platform team owns the agent runtime, YAML configs, and
the platform tool (`propose_action`).

```
Before:  platform team → reviews PRs from all domain teams → deploys everything together
After:   domain team → deploys their MCP server independently
         platform team → deploys agent runtime independently
```

**Independent deployment and versioning**

A bug fix in `get_pending_orders` deploys only the `orders-mcp-server`. The agent platform
doesn't restart. Versioning becomes `orders-mcp-server:v1.2.0` — a standalone artifact.

**Language-agnostic tools**

A data team with Go expertise writes `get_market_data` as a Go MCP server. A Node.js
team writes `send_whatsapp_notification`. The platform doesn't care — it speaks the protocol.

**Tools are reusable across AI applications**

A second LangGraph agent, a chatbot, or an autonomous pipeline can connect to the same
`orders-mcp-server` and reuse the exact same tool implementations.

**Secrets live with the right owner**

`orders-mcp-server` holds the DB credentials for the orders domain. The agent platform
doesn't need them. Secrets are scoped to the service that conceptually owns them.

**Standard ecosystem**

MCP has a growing ecosystem: inspectors for debugging, test harnesses, registries for
tool discovery. This matures over time.

---

### Cons

**Local function calls become network calls — every tool invocation**

Today: `get_pending_orders(limit=50)` is a Python function call. Sub-millisecond.
With MCP: the agent runtime makes an HTTP call to `orders-mcp-server`. Adds ~1–5ms per
tool call on a local network (more in a real network). A run with 20 tool calls adds 20–100ms.
This is usually acceptable but it's not zero.

**Each MCP server is a new failure point**

If `orders-mcp-server` is down, any agent that uses order tools fails. Today, those
tools are in-process — they can't be "down" independently.

```
Current: agent fails if platform fails (one failure domain)
MCP:     agent fails if platform fails OR any MCP server it depends on fails
         (N+1 failure domains)
```

**Operational complexity — running the platform requires N more services**

`docker-compose up` today starts 6 services. With MCP you'd add a container per MCP
server domain. `docker compose up` starts 8–10 services. New developers have more to
understand before the first run works.

**Connection management per agent run**

The agent runtime must connect to (and potentially authenticate with) each MCP server at
run time. MCP connections have lifecycle: `__aenter__` / `__aexit__` for the async client.
This slightly complicates `react_agent.py` and means connection errors must be handled.

**`propose_action` doesn't belong in an MCP server**

`propose_action` is a platform-internal tool — it calls the platform's own API
(`/api/v1/actions`). Putting it in an external MCP server just adds a network hop and
a deployment to manage for a tool that is always platform-local. It should remain a
local tool even after MCP adoption, creating a mixed model (some local, some MCP).

**Harder to debug**

Today: a tool failure is a Python exception with a traceback in the worker log.
With MCP: you have the agent log, the MCP client log, and the MCP server log to
correlate. Cross-service tracing is needed for full observability.

**YAML config gets more complex**

Today YAML just lists tool names. With MCP, YAML either still lists names
(and the platform resolves which server they come from) or YAML lists servers
(and each server exposes its tools for discovery). Neither is difficult but both
require a design decision and migration.

---

## 5. Architecture Options

### Option A — One MCP Server per Domain (Recommended)

Group tools by the domain they belong to. Each domain team owns one server.

```
orders-mcp-server      → get_pending_orders, get_order_details,
  (port 8001)            get_dispatch_rules, dispatch_order, recommend_dispatch

outreach-mcp-server    → list_retailers, filter_prospects, send_email
  (port 8002)

utility-mcp-server     → calculator, web_search
  (port 8003)

[platform stays local] → propose_action (not in any MCP server)
```

**YAML config change:**
```yaml
# Current
tools:
  - name: get_pending_orders
    enabled: true
  - name: propose_action
    enabled: true

# With MCP (option: server-scoped)
tools:
  servers:
    - name: orders
      url: "${ORDERS_MCP_URL}"   # injected from env
  local:
    - name: propose_action
      enabled: true
```

Or keep the flat name list and resolve server-membership in a server registry config — the
agent YAML stays unchanged, only `get_tools_for_config()` changes.

**`react_agent.py` change (async):**
```python
async with MultiServerMCPClient({"orders": {"url": ...}}) as mcp:
    mcp_tools = mcp.get_tools()               # BaseTool list from MCP
    local_tools = get_local_tools_for_config(config)  # propose_action etc.
    all_tools = mcp_tools + local_tools
    agent = create_react_agent(model, all_tools, ...)
```

### Option B — One MCP Server for All External Tools

A single `domain-tools-mcp-server` hosts every non-platform tool. Simpler operationally
but loses the team-ownership and independent-deployment benefits. Not recommended
once more than one team contributes tools.

### Option C — MCP Gateway / Registry

A single gateway MCP server that proxies requests to upstream MCP servers. Agents always
connect to one URL; the gateway routes by tool name. Adds routing flexibility and a single
point of connection for agents. Adds a new service to maintain. Useful at scale (20+
tool servers) but premature here.

---

## 6. What Changes, What Stays the Same

### Stays identical

| Component | Why unchanged |
|---|---|
| `create_react_agent(model, tools)` | MCP tools are `BaseTool` objects — same type |
| YAML agent config structure | Tool names in YAML still work; server resolution is internal |
| `/api/v1/actions`, `/approvals` | Platform HITL system is unaffected |
| `propose_action` | Stays a local Python tool — platform-internal |
| LangSmith / OTel tracing | Tool spans still recorded; MCP just adds an HTTP hop |
| Agent YAML guardrails, system prompts | Completely unchanged |
| DB schema, Alembic migrations | Unchanged |

### Changes

| Component | What changes |
|---|---|
| `tools/__init__.py` | `_TOOL_REGISTRY` split: local tools dict + MCP server config |
| `get_tools_for_config()` | Becomes async; calls `MultiServerMCPClient` |
| `react_agent.py` | `build_agent()` becomes async context manager for MCP connection |
| Docker Compose | +1 service per MCP server domain |
| `agents/configs/*.yaml` | Option: add `tools.servers` section; or keep flat names |
| Secrets / env vars | Domain credentials move to their respective MCP server containers |
| New files | `tools/mcp_client.py` (connection config + tool resolution logic) |

### New artifacts

| Artifact | Description |
|---|---|
| `mcp_servers/orders/` | MCP server implementing order tools (same logic, new wrapper) |
| `mcp_servers/outreach/` | MCP server implementing outreach/retailer tools |
| `mcp_servers/utility/` | MCP server implementing calculator and web_search |

The tool logic itself (the body of `get_pending_orders`, `list_retailers`, etc.) is
**copied or moved** to the MCP server — not rewritten. The `@tool` decorator is replaced
by the MCP SDK's tool registration pattern, but the HTTP calls to `/api/v1/orders` stay
the same.

---

## 7. Migration Strategy (Phased)

### Phase 0 — Prerequisite: make `react_agent.py` async (if not already)

MCP client connections are async. If `build_agent()` is sync today, it needs to become
async before MCP can be introduced. This is an isolated refactor.

### Phase 1 — Pilot: migrate one domain to MCP

Pick the domain with the clearest ownership — `orders` is a good candidate since the
dispatch tools are domain-specific and call `/api/v1/orders`.

- Create `mcp_servers/orders/` — move `dispatch.py` tool logic there
- Add `orders` MCP server to Docker Compose
- Update `tools/__init__.py` to load order tools from MCP in parallel with the local registry
- Keep local tools as fallback during transition

All agents that used order tools by name should work without YAML changes.

### Phase 2 — Migrate outreach tools

Same pattern for `outreach.py` tools.

### Phase 3 — Migrate utility tools

`calculator` and `web_search` — lower value since they have no domain ownership question,
but migrating them completes the story.

### Phase 4 — Remove local registry entries for migrated tools

Once all agents are confirmed working against MCP servers, remove the local Python
implementations from the platform package.

`propose_action` permanently stays local — never migrated to MCP.

---

## 8. Recommendation

**Don't implement MCP now. Plan for it at the next team scaling event.**

### Why not now

The specific pain points MCP solves — team boundaries, independent deployment, cross-language
tools — don't exist yet. 11 tools in 5 files takes minutes to understand and change.
Introducing MCP now adds operational complexity (3 new services, async connection management,
network failure modes) without a clear problem to solve.

### When to trigger the migration

Implement MCP when the first of these is true:

1. **A second team (not the platform team)** wants to add tools and shouldn't need to
   understand or modify the agent platform codebase.

2. **A tool needs credentials the platform shouldn't hold** — e.g. direct DB access,
   external payment gateway keys, secrets that belong to a domain team.

3. **A tool needs to be written in a non-Python language** — a Go service, a Node.js
   function, a Rust binary.

4. **The same tools need to be used by a second AI application** outside this platform.

5. **Tool deployment cadence diverges** — a domain team is making multiple tool deploys
   per day while the platform deploys weekly. Coupling them creates a bottleneck.

None of these are true yet.

### What to do now (preparatory)

Without writing MCP infrastructure, do two things that make the future migration easier:

1. **Keep domain tool files strictly separated** — no cross-imports between `dispatch.py`,
   `outreach.py`, etc. Each file should be independently movable to its own service.

2. **Pin server URLs in config, not code** — `_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")`
   is already done. MCP server URLs would follow the same pattern.

These have zero cost now and mean the MCP migration is a lift-and-shift of each file,
not a refactor.

---

## 9. Summary

| Question | Answer |
|---|---|
| Will MCP help as tools grow? | **Yes — but for team and deployment reasons, not file count** |
| Is 11 tools a management problem today? | **No** |
| Does MCP change the agent or YAML? | **Minimally — tools look identical to the agent** |
| What's the biggest cost? | **Operational: N more services, network failure modes, async connection lifecycle** |
| What's the biggest benefit? | **Domain team autonomy: own and deploy your tools independently** |
| When to start? | **When a second team needs to contribute tools** |
| What to do now? | **Keep domain tool files cleanly separated; nothing else** |
| Does `propose_action` move to MCP? | **Never — it's platform-internal** |
