# Stores / Memory Architecture
## Design Plan — Semantic Knowledge Base with RAG

**Status**: Awaiting approval  
**Scope**: Platform-level addition. First use case: Vastu Shastra scoring for Propguru.

---

## 1. Is the Thought Process Correct?

**Yes, largely.** The core ideas are sound:

| Your Idea | Verdict | Note |
|---|---|---|
| Generic "Stores" across all companies | ✓ Correct | Prevents per-domain RAG silos |
| Semantic / Episodic / Procedural types | ✓ Correct | Well-established cognitive architecture distinction |
| Semantic first, others as enhancement | ✓ Correct | Right priority order |
| pgvector + chunking for semantic | ✓ Correct | Fits existing PostgreSQL stack with zero new infra |
| Slug-based logical grouping | ✓ Correct | Clean isolation; one store per domain/topic |
| Approval workflow before RAG availability | ✓ Correct | Necessary quality gate for factual knowledge |
| Pluggable via agent YAML | ✓ Correct | Consistent with existing config-driven design |
| Dedicated upload UI per company | ✓ Correct | Operations team needs a self-service surface |
| Vastu score feeds into final evaluation | ✓ Correct | Treat as a new weighted criterion category |

**One refinement worth considering:** Vastu scoring could be modelled as a new **category** in the existing 30-criterion scoring model (alongside Amenity, Location, Property, Society) rather than a free-floating score bolted on at the report level. This keeps the price-calculation formula clean — Vastu criteria get weights, normalized scores, and feed through the same `score_factor → recommended_price` path.

---

## 2. Alternative Approaches Considered

### A. Structured Rules Instead of RAG
Vastu has codified rules (N-facing = good, SE kitchen = bad, etc.). These could be a deterministic decision matrix, not a vector search.

**Pros**: Predictable, zero LLM cost, auditable.  
**Cons**: Rigid. Vastu interpretations vary by school, region, and property type. A knowledge base lets the operations team load the specific tradition they follow and update it without code changes.

**Verdict**: RAG is the better fit here because the knowledge is text-heavy, nuanced, and needs to be updatable by non-engineers.

### B. Dedicated Vastu Microservice
Build a standalone vastu-scoring API, decouple entirely from the agent platform.

**Verdict**: Overkill for now. Over-engineering a use case that fits naturally into the existing tool/agent pattern.

### C. Use LlamaIndex or a Managed Vector DB (Pinecone, Weaviate)
Established RAG frameworks with more features out of the box.

**Verdict**: Adds infra complexity and cost. pgvector inside the existing PostgreSQL instance is sufficient for the data volumes expected (hundreds to low thousands of chunks), avoids a new service dependency, and keeps the stack consistent.

### D. Hybrid: Structured Criteria + RAG for Reasoning
Encode a small fixed set of vastu criteria as structured boolean/scale scores (e.g. "North/NE facing → score 5"), and use RAG only for the reasoning narrative, not the score itself.

**Verdict**: A reasonable middle-ground. Can be implemented within the proposed architecture — the vastu tool can first do a deterministic score, then augment the reasoning text with retrieved passages.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     PLATFORM CORE (new addition)             │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  STORES LAYER                          │  │
│  │                                                        │  │
│  │  Store Registry   Document Ingestion   Vector Search   │  │
│  │  (slug, type,     (upload → approve    (pgvector        │  │
│  │   company,         → chunk → embed)     cosine sim)     │  │
│  │   embedding_model)                                     │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
    Propguru          Sandhar          Fundly
    (vastu-shastra)   (safety-regs)   (drug-protocols)
```

The Stores layer is platform infrastructure. Each company/domain creates named stores and populates them. Agents access stores via tools declared in their YAML.

---

## 4. Memory Type Taxonomy

### 4.1 Semantic Memory (Phase 1 — this plan)
- **What it is**: General knowledge and facts that don't change based on who's asking or when
- **Storage**: Chunked text + embeddings in pgvector
- **Retrieval**: Cosine similarity search on query embedding
- **Vastu example**: Rules, interpretations, direction significance, room placement guidelines

### 4.2 Episodic Memory (Phase 2 — future)
- **What it is**: Records of specific past events, timestamped, associated with an entity
- **Storage**: Structured rows (not vector) — similar to `agent_actions` audit trail
- **Retrieval**: Filter by entity + recency, optionally ranked by similarity
- **Future example**: "Past deals where vastu score was overridden by analyst, with outcome"

### 4.3 Procedural Memory (Phase 2 — future)
- **What it is**: Step-by-step instructions, workflows, SOPs
- **Storage**: Could be structured steps or vector-searched passages
- **Retrieval**: Task-based lookup ("how do I score a 4-BHK flat?")
- **Future example**: Company-specific evaluation SOP injected into agent system prompts

---

## 5. Database Schema (New Platform Tables)

### `memory_stores`
```
id              UUID PK
slug            VARCHAR(100) UNIQUE     -- "vastu-shastra", "safety-regulations"
name            VARCHAR(200)
description     TEXT
company         VARCHAR(50)             -- propguru | sandhar | fundly | platform
memory_type     VARCHAR(30)             -- semantic | episodic | procedural
embedding_model VARCHAR(100)            -- "text-embedding-3-small" (configurable)
chunk_size      INTEGER DEFAULT 512     -- tokens per chunk
chunk_overlap   INTEGER DEFAULT 64      -- overlap tokens between chunks
is_active       BOOLEAN DEFAULT true
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

### `memory_documents`
```
id              UUID PK
store_id        UUID FK → memory_stores
title           VARCHAR(300)
source_type     VARCHAR(30)             -- pdf | text | url | markdown
raw_content     TEXT                    -- extracted text (for audit/re-chunking)
file_path       VARCHAR(500) NULLABLE   -- if stored on disk/object storage
status          VARCHAR(20)             -- pending | approved | rejected
uploaded_by     VARCHAR(100)
approved_by     VARCHAR(100) NULLABLE
approved_at     TIMESTAMPTZ NULLABLE
rejection_notes TEXT NULLABLE
metadata        JSONB                   -- {pages, file_size, source_url, etc.}
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

### `memory_chunks`
```
id              UUID PK
document_id     UUID FK → memory_documents
store_id        UUID FK → memory_stores
chunk_index     INTEGER                 -- position in document
content         TEXT                    -- the chunk text shown to the LLM
embedding       vector(1536)            -- pgvector column (model-dependent dimension)
token_count     INTEGER
metadata        JSONB                   -- {page_number, section_heading, etc.}
created_at      TIMESTAMPTZ
```

**Index**: `CREATE INDEX ON memory_chunks USING ivfflat (embedding vector_cosine_ops)` — enables fast approximate nearest-neighbour search.

---

## 6. Ingestion Pipeline

```
Upload (PDF/text)
      │
      ▼
Text Extraction
  (pypdf for PDF, plain read for text)
      │
      ▼
Document record created (status="pending")
      │
      ▼
Operations Lead reviews → Approve / Reject
      │ (on approval)
      ▼
Chunking
  (sliding window: chunk_size tokens, chunk_overlap overlap)
      │
      ▼
Embedding
  (OpenAI text-embedding-3-small or configured model)
      │
      ▼
Chunks + embeddings written to memory_chunks
      │
      ▼
Store is live — available to agents
```

Chunking and embedding run as a background task (Celery) triggered on approval. Re-indexing is possible by re-approving a document.

---

## 7. YAML Integration

Stores are declared in agent YAML under a `memory` block:

```yaml
agent:
  name: propguru-scorer
  # ... existing fields ...

  memory:
    stores:
      - slug: vastu-shastra
        type: semantic
        top_k: 5                    # number of chunks to retrieve
        score_threshold: 0.72       # minimum cosine similarity (0–1)
```

The platform resolves the slug to a `memory_stores` record at runtime. If the store is inactive or the slug doesn't exist, the tool gets an empty result set (fail-open — the agent continues without RAG context rather than crashing).

---

## 8. Tool Design

### `query_semantic_store(store_slug, query, top_k=5)`
```python
@tool
def query_semantic_store(store_slug: str, query: str, top_k: int = 5) -> str:
    """Retrieve relevant knowledge chunks from a semantic memory store.

    Embeds the query and returns the top_k most relevant approved chunks
    from the named store, ranked by cosine similarity.

    Args:
        store_slug: Identifier of the knowledge base (e.g. 'vastu-shastra')
        query: Natural language query describing what knowledge to retrieve
        top_k: Number of chunks to return (default 5)
    """
```

Returns JSON with `[{content, similarity_score, document_title, chunk_index}]`.

This is a **platform-level generic tool** — it works for any store, any domain. It is registered once in `TOOL_REGISTRY` and available to any agent that declares it.

---

## 9. Vastu Scoring — Propguru Integration

### 9.1 New Criterion Category

Add a fifth category to the evaluation model: **Vastu** (initially 1 criterion, expandable).

```
CRIT-031  Vastu Compliance    category=vastu    scoring_type=scale_1_5    weight=TBD
```

Weight is configurable by the Operations Lead via the master data UI (same as existing criteria).

### 9.2 New Worker Agent: `propguru-vastu-scorer`

Added to the supervisor's worker list. Runs after `propguru-scorer` (location), before `propguru-evaluator`.

**Inputs from pipeline context**: `report_id`, `property_type`, `facing`, `latitude`, `longitude`, `bedrooms`

**Workflow**:
1. Compose a vastu query string from property attributes:
   `"4BHK apartment, North-East facing entrance, East-facing main door, 3rd floor"`
2. Call `query_semantic_store("vastu-shastra", query, top_k=5)`
3. Review retrieved vastu rules against property attributes
4. Synthesize a score (1–5) with reasoning
5. Call `propguru_save_evaluation_score(report_id, "CRIT-031", score, ...)`

### 9.3 New YAML: `propguru-vastu-scorer.yaml`
```yaml
agent:
  name: propguru-vastu-scorer
  memory:
    stores:
      - slug: vastu-shastra
        type: semantic
        top_k: 5
  tools:
    - name: query_semantic_store
    - name: propguru_save_evaluation_score
```

### 9.4 Supervisor Update

`propguru-evaluation-supervisor.yaml` workers list becomes:
1. `propguru-scorer` (location criteria)
2. `propguru-vastu-scorer` (vastu criterion) ← new
3. `propguru-evaluator` (price calculation + HITL)

---

## 10. UI — Stores Management

### 10.1 Navigation

```
Sidebar
  ├── Propguru
  │     ├── Deals
  │     ├── Evaluation
  │     ├── Master Data
  │     └── Stores          ← new
  ├── Sandhar
  │     └── Stores          ← new (empty for now)
  └── Fundly
        └── Stores          ← new (empty for now)
```

Or a single top-level **Stores** page filtered by company via tabs.

### 10.2 Stores List Page (`/stores`)

- List of all stores (name, slug, type, company, document count, chunk count, status)
- "+ New Store" button (name, slug, type, embedding model, chunk size)
- Click → Store Detail

### 10.3 Store Detail Page (`/stores/{slug}`)

Two panels:

**Left: Store metadata** — config, stats (document count, chunk count, last indexed at)

**Right: Documents list**

| Title | Type | Status | Uploaded | Actions |
|---|---|---|---|---|
| Vastu Shastra Guide 2024 | PDF | ✓ Approved | 2 days ago | View / Re-index |
| Directional Vastu Rules | Text | ⏳ Pending | 1 hour ago | Approve / Reject |
| Old Reference v1 | PDF | ✗ Rejected | 5 days ago | — |

**Upload button** → file picker (PDF, .txt, .md) → creates document in `pending` state.

### 10.4 Document Detail / Approval

- Shows extracted text (first 2000 chars preview)
- Page count, file size, upload metadata
- Approve → triggers chunking + embedding Celery task
- Reject → prompts for rejection notes

---

## 11. API Routes (New)

```
# Store registry
GET    /api/v1/stores                        -- list all stores (filter: company, type)
POST   /api/v1/stores                        -- create a store
GET    /api/v1/stores/{slug}                 -- store detail + stats
PATCH  /api/v1/stores/{slug}                 -- update config
DELETE /api/v1/stores/{slug}                 -- soft-delete (is_active=false)

# Document management
GET    /api/v1/stores/{slug}/documents       -- list documents
POST   /api/v1/stores/{slug}/documents       -- upload document (multipart form)
GET    /api/v1/stores/{slug}/documents/{id}  -- document detail + chunk count
PATCH  /api/v1/stores/{slug}/documents/{id}/approve   -- approve → triggers indexing
PATCH  /api/v1/stores/{slug}/documents/{id}/reject    -- reject with notes
DELETE /api/v1/stores/{slug}/documents/{id}           -- remove + delete chunks

# Query (used by tools; also exposed for testing)
POST   /api/v1/stores/{slug}/query           -- {query, top_k} → ranked chunks
```

---

## 12. What Does NOT Change

| Component | Change Needed? |
|---|---|
| Platform orchestration (LangGraph, Celery) | No |
| HITL workflow | No |
| Verification / code grader | Minor: add CRIT-031 to coverage check |
| Existing 30 criteria + scoring model | No (new criterion is additive) |
| Price calculation formula | No (score_factor formula already handles any N criteria) |
| Approval workflow (AgentActions) | No |
| Propguru deals pipeline | No |

---

## 13. Implementation Phases

### Phase 1 — Stores Platform + Vastu (this plan)

1. DB migration: `memory_stores`, `memory_documents`, `memory_chunks` (with pgvector extension)
2. Ingestion pipeline: upload → text extract → approve → chunk → embed → index
3. `query_semantic_store` platform tool
4. Stores API routes + Jinja2 UI pages
5. Vastu criterion (CRIT-031) added to seed data
6. `propguru-vastu-scorer` agent YAML + supervisor update
7. Manual testing: upload vastu PDF → approve → trigger evaluation → verify vastu score appears

### Phase 2 — Episodic Memory (future)
- Past deal outcomes linked to property attributes
- Structured retrieval with recency weighting
- "Similar past deal" context injected into evaluator reasoning

### Phase 3 — Procedural Memory (future)
- Company SOPs uploaded as structured steps
- Injected into agent system prompts or as tool responses

---

## 14. Open Questions Before Implementation

1. **Vastu criterion weight** — what weight (out of the ~4.0 avg across current criteria) should CRIT-031 carry? Operations Lead should decide.
2. **Embedding model** — `text-embedding-3-small` (cheap, fast, 1536-dim) or `text-embedding-3-large` (better, 3072-dim)? Default to `text-embedding-3-small`.
3. **Score derivation for vastu** — should the vastu tool output a raw 1–5 score directly, or output "compliant/non-compliant" booleans per rule and aggregate? Recommend: LLM synthesizes a 1–5 scale score with free-text reasoning.
4. **Who approves vastu documents?** — Any authenticated user, or a designated "Operations Lead" role? For POC: any API-key holder.
5. **Re-indexing** — if a document is re-approved after edits, should old chunks be deleted and replaced? Yes — simplest is delete-and-reinsert on re-approval.
6. **File storage** — for POC, store extracted text in the DB (no object storage). For production, S3/GCS for raw files.
