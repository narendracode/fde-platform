"""Celery tasks for async agent execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from celery.utils.log import get_task_logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fde_agent.agent.react_agent import run_agent
from fde_agent.config.loader import load_agent_config
from fde_agent.config.settings import settings
from fde_agent.db.models import AgentRun, MemoryChunk, MemoryDocument, PropguruDeal
from fde_agent.queue.celery_app import celery_app

logger = get_task_logger(__name__)

# Celery tasks run synchronously, so we use a sync SQLAlchemy engine
_sync_engine = create_engine(settings.database_url_sync, pool_pre_ping=True)


def _get_sync_session() -> Session:
    return Session(_sync_engine)


@celery_app.task(
    name="fde_agent.queue.tasks.run_agent_task",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def run_agent_task(
    self,
    run_id: str,
    agent_name: str,
    user_message: str,
    thread_id: str | None = None,
    extra_context: dict | None = None,
) -> dict:
    """Execute an agent run and persist results to the DB.

    Called via .delay() or .apply_async() from the API layer.
    """
    with _get_sync_session() as session:
        run: AgentRun | None = session.get(AgentRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"AgentRun {run_id} not found")

        run.status = "running"
        run.started_at = datetime.now(UTC)
        session.commit()

    try:
        config = load_agent_config(agent_name)
        result = run_agent(
            config=config,
            user_message=user_message,
            thread_id=thread_id,
            extra_context=extra_context,
        )

        with _get_sync_session() as session:
            run = session.get(AgentRun, uuid.UUID(run_id))
            if run:
                run.status = "completed"
                run.output = {
                    "text": result["output"],
                    "tool_calls": result["tool_calls"],
                    "sub_agents": result.get("sub_agents", []),
                }
                run.thread_id = result["thread_id"]
                run.input_tokens = result["input_tokens"]
                run.output_tokens = result["output_tokens"]
                run.cost_usd = result.get("cost_usd", 0.0)
                run.langsmith_run_id = result.get("langsmith_run_id")
                run.langsmith_trace_url = result.get("langsmith_trace_url")
                run.otel_trace_id = result.get("otel_trace_id")
                run.otel_trace_url = result.get("otel_trace_url")
                run.completed_at = datetime.now(UTC)
                session.commit()

        return result

    except Exception as exc:
        logger.exception("Agent run %s failed: %s", run_id, exc)
        is_final_failure = self.request.retries >= self.max_retries
        with _get_sync_session() as session:
            run = session.get(AgentRun, uuid.UUID(run_id))
            if run:
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = datetime.now(UTC)
                # On final failure, unlock the deal so the user can re-trigger evaluation
                if is_final_failure:
                    deal_id_str = (run.input or {}).get("extra_context", {}).get("deal_id")
                    if deal_id_str:
                        deal = session.get(PropguruDeal, uuid.UUID(deal_id_str))
                        if deal and deal.stage == "evaluation_pending":
                            deal.stage = "lead"
                session.commit()
        raise self.retry(exc=exc)


@celery_app.task(
    name="fde_agent.queue.tasks.embed_document_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def embed_document_task(
    self,
    document_id: str,
    store_id: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    embedding_model: str = "text-embedding-3-small",
) -> dict:
    """Chunk and embed an approved memory document, writing vectors to memory_chunks."""
    with _get_sync_session() as session:
        doc: MemoryDocument | None = session.get(MemoryDocument, uuid.UUID(document_id))
        if doc is None:
            raise ValueError(f"MemoryDocument {document_id} not found")
        if not doc.raw_content:
            return {"status": "skipped", "reason": "no content"}

        # Delete existing chunks (re-indexing support)
        session.query(MemoryChunk).filter(MemoryChunk.document_id == doc.id).delete()
        session.commit()

        # Chunk text (~4 chars per token)
        char_size = chunk_size * 4
        char_overlap = chunk_overlap * 4
        text = doc.raw_content
        raw_chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + char_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                raw_chunks.append(chunk)
            if end == len(text):
                break
            start = end - char_overlap

        if not raw_chunks:
            return {"status": "skipped", "reason": "no chunks produced"}

        # Embed all chunks in one API call (batch)
        try:
            from openai import OpenAI
            oai = OpenAI()
            emb_resp = oai.embeddings.create(
                model=embedding_model,
                input=raw_chunks,
            )
            embeddings = [item.embedding for item in sorted(emb_resp.data, key=lambda x: x.index)]
        except Exception as exc:
            logger.exception("Embedding API failed for document %s: %s", document_id, exc)
            raise self.retry(exc=exc)

        # Insert chunks with embeddings
        for idx, (chunk_text, embedding) in enumerate(zip(raw_chunks, embeddings)):
            chunk = MemoryChunk(
                document_id=doc.id,
                store_id=uuid.UUID(store_id),
                chunk_index=idx,
                content=chunk_text,
                token_count=len(chunk_text) // 4,
                embedding=embedding,
            )
            session.add(chunk)
        session.commit()

        logger.info(
            "Embedded document %s: %d chunks written to store %s",
            document_id, len(raw_chunks), store_id,
        )
        return {"status": "completed", "chunks_written": len(raw_chunks)}
