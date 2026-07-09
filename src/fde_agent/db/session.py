"""Async SQLAlchemy session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fde_agent.config.settings import settings
from fde_agent.telemetry import instrument_sqlalchemy

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.log_level == "debug",
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Instrument after engine creation so the engine reference is always captured.
# instrument_sqlalchemy() is a no-op when OTEL_ENABLED is not set.
instrument_sqlalchemy(engine=engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
