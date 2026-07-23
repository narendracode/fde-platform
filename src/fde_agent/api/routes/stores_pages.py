"""Stores UI page routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fde_agent.config import settings
from fde_agent.db.models import MemoryChunk, MemoryDocument, MemoryStore
from fde_agent.db.session import get_session

_API_KEY = settings.api_key

router = APIRouter(tags=["stores-pages"])
templates = Jinja2Templates(directory="src/fde_agent/templates")


@router.get("/stores", response_class=HTMLResponse)
async def stores_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    stores_raw = (
        (await session.execute(select(MemoryStore).order_by(MemoryStore.company, MemoryStore.name)))
        .scalars()
        .all()
    )

    stores = []
    for s in stores_raw:
        doc_count = (
            await session.execute(select(func.count()).where(MemoryDocument.store_id == s.id))
        ).scalar_one()
        chunk_count = (
            await session.execute(select(func.count()).where(MemoryChunk.store_id == s.id))
        ).scalar_one()
        stores.append(
            {
                "id": str(s.id),
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "company": s.company,
                "memory_type": s.memory_type,
                "embedding_model": s.embedding_model,
                "is_active": s.is_active,
                "doc_count": doc_count,
                "chunk_count": chunk_count,
            }
        )

    return templates.TemplateResponse(
        request,
        "stores/stores.html",
        {
            "stores": stores,
            "companies": ["propguru", "sandhar", "fundly"],
            "active_page": "stores",
            "api_key": _API_KEY,
        },
    )


@router.get("/stores/{slug}", response_class=HTMLResponse)
async def store_detail_page(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(MemoryStore).where(MemoryStore.slug == slug))
    ).scalar_one_or_none()
    if not store:
        return HTMLResponse(f"<h1>Store '{slug}' not found</h1>", status_code=404)

    doc_count = (
        await session.execute(select(func.count()).where(MemoryDocument.store_id == store.id))
    ).scalar_one()
    chunk_count = (
        await session.execute(select(func.count()).where(MemoryChunk.store_id == store.id))
    ).scalar_one()

    docs_raw = (
        (
            await session.execute(
                select(MemoryDocument)
                .where(MemoryDocument.store_id == store.id)
                .order_by(MemoryDocument.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    docs = []
    for d in docs_raw:
        cc = (
            await session.execute(select(func.count()).where(MemoryChunk.document_id == d.id))
        ).scalar_one()
        docs.append(
            {
                "id": str(d.id),
                "title": d.title,
                "source_type": d.source_type,
                "status": d.status,
                "uploaded_by": d.uploaded_by,
                "approved_by": d.approved_by,
                "chunk_count": cc,
                "preview": (d.raw_content or "")[:300],
                "created_at": d.created_at.strftime("%Y-%m-%d %H:%M"),
            }
        )

    store_data = {
        "id": str(store.id),
        "slug": store.slug,
        "name": store.name,
        "description": store.description,
        "company": store.company,
        "memory_type": store.memory_type,
        "embedding_model": store.embedding_model,
        "chunk_size": store.chunk_size,
        "chunk_overlap": store.chunk_overlap,
        "is_active": store.is_active,
        "doc_count": doc_count,
        "chunk_count": chunk_count,
    }

    return templates.TemplateResponse(
        request,
        "stores/store_detail.html",
        {
            "store": store_data,
            "docs": docs,
            "companies": ["propguru", "sandhar", "fundly"],
            "active_page": "stores",
            "api_key": _API_KEY,
        },
    )
