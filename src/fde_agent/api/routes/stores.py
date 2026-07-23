"""Platform stores API — semantic knowledge base management."""
from __future__ import annotations

import io
import logging
import textwrap
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.api.dependencies import verify_api_key
from fde_agent.db.models import MemoryChunk, MemoryDocument, MemoryStore
from fde_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/stores", tags=["stores"])
_log = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _store_out(s: MemoryStore, doc_count: int = 0, chunk_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "company": s.company,
        "memory_type": s.memory_type,
        "embedding_model": s.embedding_model,
        "chunk_size": s.chunk_size,
        "chunk_overlap": s.chunk_overlap,
        "is_active": s.is_active,
        "doc_count": doc_count,
        "chunk_count": chunk_count,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _doc_out(d: MemoryDocument, chunk_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "store_id": str(d.store_id),
        "title": d.title,
        "source_type": d.source_type,
        "status": d.status,
        "uploaded_by": d.uploaded_by,
        "approved_by": d.approved_by,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "rejection_notes": d.rejection_notes,
        "doc_metadata": d.doc_metadata,
        "chunk_count": chunk_count,
        "preview": (d.raw_content or "")[:500],
        "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(),
    }


async def _get_store(slug: str, session: AsyncSession) -> MemoryStore:
    store = (await session.execute(
        select(MemoryStore).where(MemoryStore.slug == slug)
    )).scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail=f"Store '{slug}' not found")
    return store


def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping character-based chunks (~4 chars per token)."""
    char_size = chunk_size * 4
    char_overlap = overlap * 4
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + char_size, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = end - char_overlap
    return [c for c in chunks if c]


# ── Store CRUD ─────────────────────────────────────────────────────────────────

class CreateStore(BaseModel):
    slug: str
    name: str
    description: str | None = None
    company: str = "platform"
    memory_type: str = "semantic"
    embedding_model: str = "text-embedding-3-small"
    chunk_size: int = 512
    chunk_overlap: int = 64


class PatchStore(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


@router.get("")
async def list_stores(
    company: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    q = select(MemoryStore).order_by(MemoryStore.company, MemoryStore.name)
    if company:
        q = q.where(MemoryStore.company == company)
    stores = (await session.execute(q)).scalars().all()

    result = []
    for s in stores:
        doc_count = (await session.execute(
            select(func.count()).where(MemoryDocument.store_id == s.id)
        )).scalar_one()
        chunk_count = (await session.execute(
            select(func.count()).where(MemoryChunk.store_id == s.id)
        )).scalar_one()
        result.append(_store_out(s, doc_count, chunk_count))
    return result


@router.post("", status_code=201)
async def create_store(
    req: CreateStore,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    existing = (await session.execute(
        select(MemoryStore).where(MemoryStore.slug == req.slug)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Store with slug '{req.slug}' already exists")

    store = MemoryStore(**req.model_dump())
    session.add(store)
    await session.commit()
    await session.refresh(store)
    return _store_out(store)


@router.get("/{slug}")
async def get_store(
    slug: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    doc_count = (await session.execute(
        select(func.count()).where(MemoryDocument.store_id == store.id)
    )).scalar_one()
    chunk_count = (await session.execute(
        select(func.count()).where(MemoryChunk.store_id == store.id)
    )).scalar_one()
    return _store_out(store, doc_count, chunk_count)


@router.patch("/{slug}")
async def patch_store(
    slug: str,
    req: PatchStore,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(store, k, v)
    store.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(store)
    return _store_out(store)


# ── Document upload & management ───────────────────────────────────────────────

@router.get("/{slug}/documents")
async def list_documents(
    slug: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    q = select(MemoryDocument).where(MemoryDocument.store_id == store.id).order_by(MemoryDocument.created_at.desc())
    if status:
        q = q.where(MemoryDocument.status == status)
    docs = (await session.execute(q)).scalars().all()

    result = []
    for d in docs:
        chunk_count = (await session.execute(
            select(func.count()).where(MemoryChunk.document_id == d.id)
        )).scalar_one()
        result.append(_doc_out(d, chunk_count))
    return result


@router.post("/{slug}/documents", status_code=201)
async def upload_document(
    slug: str,
    file: UploadFile = File(...),
    title: str = Form(...),
    uploaded_by: str = Form(default="system"),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    raw = await file.read()
    filename = file.filename or "upload"

    if filename.lower().endswith(".pdf"):
        source_type = "pdf"
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
            page_count = len(reader.pages)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"PDF extraction failed: {exc}") from exc
        doc_meta: dict = {"original_filename": filename, "pages": page_count, "file_size": len(raw)}
    else:
        source_type = "text"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=422, detail="File must be UTF-8 text or PDF")
        doc_meta = {"original_filename": filename, "file_size": len(raw)}

    doc = MemoryDocument(
        store_id=store.id,
        title=title,
        source_type=source_type,
        raw_content=text,
        status="pending",
        uploaded_by=uploaded_by,
        doc_metadata=doc_meta,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return _doc_out(doc)


@router.get("/{slug}/documents/{doc_id}")
async def get_document(
    slug: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    doc = (await session.execute(
        select(MemoryDocument)
        .where(MemoryDocument.id == uuid.UUID(doc_id))
        .where(MemoryDocument.store_id == store.id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunk_count = (await session.execute(
        select(func.count()).where(MemoryChunk.document_id == doc.id)
    )).scalar_one()
    return _doc_out(doc, chunk_count)


class RejectRequest(BaseModel):
    notes: str | None = None
    rejected_by: str = "system"


@router.patch("/{slug}/documents/{doc_id}/approve")
async def approve_document(
    slug: str,
    doc_id: str,
    approved_by: str = "system",
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    doc = (await session.execute(
        select(MemoryDocument)
        .where(MemoryDocument.id == uuid.UUID(doc_id))
        .where(MemoryDocument.store_id == store.id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.raw_content:
        raise HTTPException(status_code=422, detail="Document has no text content to index")

    doc.status = "approved"
    doc.approved_by = approved_by
    doc.approved_at = datetime.now(timezone.utc)
    doc.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # Trigger background embedding task
    try:
        from fde_agent.queue.tasks import embed_document_task
        embed_document_task.delay(
            document_id=str(doc.id),
            store_id=str(store.id),
            chunk_size=store.chunk_size,
            chunk_overlap=store.chunk_overlap,
            embedding_model=store.embedding_model,
        )
        embed_status = "queued"
    except Exception as exc:
        _log.warning("Failed to queue embed task for doc %s: %s", doc.id, exc)
        embed_status = "queue_failed"

    return {"status": "approved", "document_id": str(doc.id), "embed_status": embed_status}


@router.patch("/{slug}/documents/{doc_id}/reject")
async def reject_document(
    slug: str,
    doc_id: str,
    req: RejectRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    doc = (await session.execute(
        select(MemoryDocument)
        .where(MemoryDocument.id == uuid.UUID(doc_id))
        .where(MemoryDocument.store_id == store.id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = "rejected"
    doc.rejection_notes = req.notes
    doc.updated_at = datetime.now(timezone.utc)

    # Remove any existing chunks
    await session.execute(
        delete(MemoryChunk).where(MemoryChunk.document_id == doc.id)
    )
    await session.commit()
    return {"status": "rejected", "document_id": str(doc.id)}


@router.delete("/{slug}/documents/{doc_id}", status_code=204)
async def delete_document(
    slug: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = await _get_store(slug, session)
    doc = (await session.execute(
        select(MemoryDocument)
        .where(MemoryDocument.id == uuid.UUID(doc_id))
        .where(MemoryDocument.store_id == store.id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await session.execute(delete(MemoryChunk).where(MemoryChunk.document_id == doc.id))
    await session.delete(doc)
    await session.commit()


# ── Query (used by agents via query_semantic_store tool) ──────────────────────

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


@router.post("/{slug}/query")
async def query_store(
    slug: str,
    req: QueryRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    store = (await session.execute(
        select(MemoryStore)
        .where(MemoryStore.slug == slug)
        .where(MemoryStore.is_active == True)  # noqa: E712
    )).scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail=f"Store '{slug}' not found or inactive")

    # Check there are approved chunks
    chunk_count = (await session.execute(
        select(func.count()).where(MemoryChunk.store_id == store.id)
    )).scalar_one()
    if chunk_count == 0:
        return {"chunks": [], "note": "No indexed content in this store yet."}

    # Embed the query
    try:
        from openai import AsyncOpenAI
        oai = AsyncOpenAI()
        emb_resp = await oai.embeddings.create(
            model=store.embedding_model,
            input=req.query,
        )
        query_vector = emb_resp.data[0].embedding
    except Exception as exc:
        _log.error("Embedding failed for store query: %s", exc)
        raise HTTPException(status_code=502, detail=f"Embedding API error: {exc}") from exc

    # Cosine similarity search via pgvector
    try:
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import cast, text as sa_text

        # Use raw SQL for cosine distance — most reliable across pgvector versions
        rows = (await session.execute(
            sa_text("""
                SELECT
                    mc.id,
                    mc.chunk_index,
                    mc.content,
                    mc.chunk_metadata,
                    md.title as document_title,
                    1 - (mc.embedding <=> cast(:vec AS vector)) as similarity
                FROM memory_chunks mc
                JOIN memory_documents md ON mc.document_id = md.id
                WHERE mc.store_id = :store_id
                  AND md.status = 'approved'
                ORDER BY mc.embedding <=> cast(:vec AS vector)
                LIMIT :limit
            """),
            {
                "vec": str(query_vector),
                "store_id": str(store.id),
                "limit": min(req.top_k, 20),
            },
        )).mappings().all()
    except Exception as exc:
        _log.error("pgvector search failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Vector search error: {exc}") from exc

    chunks = [
        {
            "content": r["content"],
            "similarity": round(float(r["similarity"]), 4),
            "document_title": r["document_title"],
            "chunk_index": r["chunk_index"],
        }
        for r in rows
    ]
    return {"store_slug": slug, "query": req.query, "chunks": chunks}
