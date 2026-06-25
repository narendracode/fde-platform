"""Platform settings API — feature flags and operational toggles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.api.dependencies import verify_api_key
from agri_agent.db.models import PlatformSettings
from agri_agent.db.session import get_session

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

# Default values returned when a key has never been set
_DEFAULTS: dict[str, Any] = {
    "ai_automation_enabled": False,
    "active_dispatch_agent": "order-dispatch-review",
}


class UpdateSettingRequest(BaseModel):
    value: Any


@router.get("")
async def get_all_settings(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Return all platform settings merged with defaults."""
    rows = await session.execute(select(PlatformSettings))
    stored = {r.key: r.value for r in rows.scalars().all()}
    merged = {**_DEFAULTS, **stored}
    return merged


@router.get("/{key}")
async def get_setting(
    key: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Get a single platform setting by key."""
    result = await session.execute(select(PlatformSettings).where(PlatformSettings.key == key))
    row = result.scalar_one_or_none()
    if row:
        return {"key": key, "value": row.value}
    if key in _DEFAULTS:
        return {"key": key, "value": _DEFAULTS[key]}
    raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")


@router.patch("/{key}")
async def update_setting(
    key: str,
    req: UpdateSettingRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(verify_api_key),
):
    """Create or update a platform setting."""
    result = await session.execute(select(PlatformSettings).where(PlatformSettings.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = req.value
    else:
        session.add(PlatformSettings(key=key, value=req.value))
    await session.commit()
    return {"key": key, "value": req.value}
