"""FastAPI dependency injections — auth, DB session."""

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from agri_agent.config.settings import settings
from agri_agent.db.session import get_session  # noqa: F401 — re-exported for routes


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Validate the X-API-Key header. Extend this to JWT as needed."""
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return x_api_key
