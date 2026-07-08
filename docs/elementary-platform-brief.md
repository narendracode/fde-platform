# Agentic Platform Architecture 

---

## Overview

This brief presents a technical architecture that directly addresses Elementary's four stated needs: extraction on unknown documents, dynamic production evals, attribute-based access control, and secure sandbox execution. All the details in the ducument can demonstrated as a working proof-of-concept if required.

---

## Platform Foundation

The architecture is built on four core principles: **declarative agent definition**, **isolated execution**, **persistent auditability**, and **human-in-the-loop by default**.

### YAML-Defined Agents

Every agent from a simple extraction task to a multi-agent planning pipeline is declared as a YAML file. This is the single source of truth: it controls the LLM, tools, guardrails, cost caps, observability, and feature flags. Engineers ship agents by committing a file. No per-team tooling divergence. No undocumented configurations.

```yaml
agent:
  name: credit-memo-extractor
  type: react
  model:
    provider: anthropic        # swap to openai or other models without code change
    name: claude-sonnet-4-6
    max_cost_usd: 2.00
  feature_flags:
    enable_refinement: true    # activates conversational human review
    human_in_the_loop: true    # routes output to approval inbox before execution
  guardrails:
    max_iterations: 20
    blocked_patterns: ["ignore previous instructions"]
  tools:
    - name: extract_document_fields
      enabled: true
    - name: propose_action
      enabled: true            # HITL platform tool
```

### Triggers and Execution

```
                    ┌─────────────────────────────────────────────────┐
                    │              Trigger Layer                       │
                    │                                                  │
                    │  REST API    Webhooks    Message Queue           │
                    │  (FastAPI)   (Events)    (Celery + Redis)        │
                    │                 │                                │
                    │  Bots: WhatsApp · Slack · MS Teams · Telegram   │
                    └──────────────────────┬──────────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────────┐
                    │            Agent Execution Layer                 │
                    │                                                  │
                    │   LangGraph ReAct / Supervisor Agents            │
                    │   YAML Config Loader → Tool Registry             │
                    │   Isolated container per run (Docker)            │
                    │   Rate-limited · Cost-capped · Guardrailed       │
                    └──────────────────────┬──────────────────────────┘
                                           │
          ┌────────────────────────────────┼──────────────────────────┐
          │                                │                           │
  ┌───────▼────────┐            ┌──────────▼──────────┐    ┌─────────▼──────────┐
  │  PostgreSQL    │            │  Knowledge Layer     │    │  Observability     │
  │  agent_runs    │            │  Vector + Graph RAG  │    │  LangSmith traces  │
  │  agent_actions │            │  S3 document store   │    │  OpenTelemetry     │
  │  audit trail   │            │  Reranker            │    │  LLMOps dataset    │
  └────────────────┘            └─────────────────────┘    └────────────────────┘
```

The platform already supports: multi-agent supervisor-worker pipelines, Human-in-the-Loop approval inbox, SSE-streamed conversational refinement, deep-linked audit trails, and GitOps deployment of new agents.

---

## Extraction and query on Documents
This has been broken down into multiple steps.

### Ingestion Pipeline

```
Document (PDF · DOCX · XLSX · Email · Scan)
        │
        ▼
  ┌─────────────────────────────────────────┐
  │  Pre-processing                         │
  │  OCR/Text extraction                    │
  └──────────────────┬──────────────────────┘
                     │
        ┌────────────▼────────────┐
        │   Chunking Strategy     │
        │   Semantic or others    |
        └────────────┬────────────┘
                     │
         ┌───────────┴────────────┐
         │                        │
  ┌──────▼────────┐      ┌────────▼────────┐
  │  Vector Store │      │   Graph Store   │
  │  (pgvector /  │      │   (Neo4j)       │
  │   Pinecone)   │      │   Entities +    │
  │  Semantic     │      │   Relationships │
  │  similarity   │      │   Multi-hop     │
  └──────┬─────── ┘      └────────┬────────┘
         │                         │
         └────────────┬────────────┘
                      │  Hybrid Retrieval
                 ┌────▼──────┐
                 │  Reranker  │  Cross-encoder scores candidate chunks
                 │  (Cohere / │  Returns top-K with confidence
                 │  BGE)      │
                 └─────┬──────┘
                       │
                 Agent Context Window
```

**Key design decisions:**
- **Raw document stored in S3.** Vector and graph indexes are derivatives — delete the S3 reference and all downstream indexes are cleared atomically.
- **GraphRAG for relationship traversal.** In private credit, relationships matter: *"Which entities guarantee this tranche?"* requires graph traversal, not nearest-neighbour search.
- **Per-agent knowledge bases.** The dashboard lets operators assign documents to specific agents. An extraction agent for Fund A never sees Fund B's documents.
- **YAML-declared knowledge scope.** Each agent YAML declares which knowledge base it can access, making data boundary violations impossible at the framework level.

---

## Dynamic Evals in Production (Harness)

**The challenge:** Static test sets/evals go stale. Production behaviour drifts. The platform needs a closed-loop evaluation system that continuously improves agent quality using live production signals.

### The Self-Learning Loop

```
  Production Runs (LangSmith traces)
          │
          ▼  Auto-tagged: feature, agent, session, outcome
  ┌───────────────────────────────────────┐
  │         Grading Layer                  │
  │                                        │
  │  Code Graders    → deterministic       │
  │  (format, regex, │   field presence,   │
  │   range checks)  │   schema validation │
  │                  │                     │
  │  Model Graders   → LLM-as-judge        │
  │  (faithfulness,  │   coherence,        │
  │   hallucination) │   completeness      │
  │                  │                     │
  │  Human Graders   → Annotation queues  │
  │  (edge cases,    │   Gold labels       │
  │   policy review) │   Reversal tagging  │
  └────────┬──────────────────────────────┘
           │  Graded traces → LLMOps Dataset
           ▼
  ┌────────────────────────────────────────┐
  │     Hill-Climbing Loop                  │
  │                                        │
  │  1. Score current prompt/config        │
  │  2. Propose variant (Context Hub)      │
  │  3. Run evaluator on dataset           │
  │  4. If score improves → promote        │
  │  5. Pin new version as :latest         │
  └────────┬───────────────────────────────┘
           │  Promoted prompt → picked up on next deploy
           ▼
  Agents Improve Without Code Deployments
```

**Grader types in practice:**

| Grader Type | Example Check | When to Use |
|---|---|---|
| Code | `extracted_fields` contains all required keys | Schema compliance, format validation |
| Model | "Does the credit memo summary faithfully represent the source?" | Semantic correctness, hallucination detection |
| Human | Analyst labels whether extraction is investment-decision-ready | High-stakes edge cases; training signal generation |

**LangSmith Context Hub** stores versioned system prompts as artifacts (e.g. `elementary/credit-memo-extractor:latest`). The hill-climbing loop proposes changes in the Hub, runs the eval suite, and promotes the version only when the score improves. Rollback is a single tag reassignment.

**Human-in-the-Loop eval gates:** flagged traces are routed to annotation queues. Analysts label, reject, or correct — these labels feed the dataset that drives the next evaluation round. The loop closes without any engineering intervention.

---

## Attribute-Based Access Control (ABAC)

**The challenge:** RBAC gives every analyst in a role the same access. Private credit demands finer grain: *an analyst can read Fund A CIMs but not Fund B; a senior analyst can trigger extraction but not approve disbursements; access changes based on deal stage.*

### Architecture

```
  Incoming Request (API / Bot / UI)
          │
          ▼
  ┌───────────────────────────────┐
  │     Identity Layer            │
  │     JWT / OAuth2 / SSO        │
  │     Extracts subject attrs:   │
  │     {user_id, department,     │
  │      fund_access: ["F-A"],    │
  │      clearance: "senior",     │
  │      mfa_verified: true}      │
  └──────────────┬────────────────┘
                 │
  ┌──────────────▼────────────────┐
  │     Policy Engine (OPA)       │
  │     Rego policy evaluation    │
  │                               │
  │     Input: subject attrs +    │
  │            resource attrs +   │
  │            action + context   │
  │                               │
  │     Decision: ALLOW / DENY    │
  └──────────────┬────────────────┘
                 │
  ┌──────────────▼────────────────┐
  │     Framework Interceptor     │
  │     (LangChain middleware)    │
  │                               │
  │     Tool calls → policy check │
  │     Document retrieval →      │
  │       attribute filter        │
  │     Agent actions →           │
  │       approval gate           │
  └───────────────────────────────┘
```

**Policy example (Rego):**
```
allow {
    input.user.clearance == "senior"
    input.resource.fund_id in input.user.fund_access
    input.action == "read_credit_memo"
    time.clock(input.now)[0] >= 6    # business hours only
}
```

**LangChain integration point:** A custom `BaseCallbackHandler` intercepts every tool call before execution and every retrieval before it returns results. Policy decisions are cached per session to avoid latency impact. The YAML agent definition declares the `resource_attributes` for each tool, giving the policy engine the context it needs without hardcoding rules in agent logic.

---

## Secure Sandbox Execution

| Layer | Mechanism | What It Prevents |
|---|---|---|
| **Container isolation** | Each agent run in its own ephemeral Docker container; no shared filesystem | Cross-run contamination, lateral movement |
| **Tool whitelist** | Only tools declared in the YAML are loadable; registry rejects unknown names | Prompt injection into arbitrary tool calls |
| **Code execution sandbox** | Restricted Python interpreter (AST-based, no `eval`, no `exec`, no network); separate gVisor container for untrusted code | Arbitrary code execution from injected inputs |
| **Network egress control** | Agents can only call pre-approved endpoints (declared in YAML) | Data exfiltration to external URLs |
| **Secret isolation** | Secrets injected at runtime from Vault/AWS Secrets Manager; never in YAML or DB | Secret leakage in logs, traces, or prompts |
| **Data tenancy** | Row-level security in PostgreSQL; S3 bucket policies per fund/customer | Cross-customer data access |
| **Cost cap enforcement** | `max_cost_usd` in YAML; hard-stop before LLM call if budget exceeded | Runaway agent spend |
| **Blocked pattern guardrail** | Input regex matching before any LLM call | Prompt injection attacks |
| **Audit trail** | Every tool call, input, output, and token count persisted in `agent_runs` + LangSmith | Forensic investigation of any run |

---

## Cross-Cutting Capabilities

### Memory Architecture

```
  ┌────────────────────────────────────────────────────────────┐
  │  Short-term (session)   │  Long-term (persistent)          │
  │                         │                                  │
  │  LangGraph thread_id    │  PostgreSQL vector store         │
  │  In-context messages    │  Cross-session user facts        │
  │  Tool call history      │  Agent learnings from past runs  │
  │  Cleared on close       │  Queryable as a knowledge tool   │
  └────────────────────────────────────────────────────────────┘
  ┌────────────────────────────────────────────────────────────┐
  │  Episodic (run summaries)  │  Semantic (knowledge base)    │
  │                            │                               │
  │  Compressed past runs      │  Extracted document chunks    │
  │  Pattern-matched to new    │  GraphRAG entity graph        │
  │  inputs for context        │  Hybrid retrieval + rerank    │
  └────────────────────────────────────────────────────────────┘
```

### Multi-Model / Multi-Cloud

The YAML `model.provider` field accepts `anthropic`, `openai`, `azure`, `bedrock`, or `vertex`. The agent engine builds the appropriate LangChain chat model at runtime. Switching a production agent from Anthropic to Azure OpenAI requires one line in the YAML — no code change, no redeployment. Per-agent model selection enables cost optimization: lightweight agents use Haiku-class models; high-stakes extraction uses frontier models.

---

## Proof-of-Concept Scope

If approved, the following capabilities will be demonstrated end-to-end in a working implementation:

| Capability | Demo Scenario |
|---|---|
| Unknown document extraction | Upload a CIM PDF; agent extracts key metrics without a predefined schema; results shown in a structured review UI |
| Dynamic eval loop | Run an extraction agent; grade outputs with code + model graders; human annotator labels one edge case; promote improved prompt via LangSmith Hub |
| ABAC in action | Same extraction request as two users with different fund access attributes; one gets results, one gets denied — policy shown in real time |
| Secure sandbox | Attempt prompt injection via document content; blocked pattern and tool whitelist prevent execution; full audit trail captured |
| Conversational refinement (HITL) | Agent proposes an extraction result; analyst refines it via chat before approving; session history persisted and deep-linked |

The POC will be built on the existing platform (FastAPI + LangGraph + PostgreSQL + Redis + LangSmith + Docker), extended with the components described in this brief. Each capability is independently demonstrable.

---

*This is just a rough draft, we will decide further action based on team discussions.
