"""Seed demo agent configs into the database.

Run: uv run python scripts/seed_data.py
Or:  make seed (inside Docker)
"""

import asyncio
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from agri_agent.config.loader import list_agent_configs
from agri_agent.config.settings import settings
from agri_agent.db.models import Agent, Base


async def seed():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    configs = list_agent_configs()
    if not configs:
        print("No agent configs found in agents/configs/")
        return

    async with SessionLocal() as session:
        for cfg in configs:
            result = await session.execute(select(Agent).where(Agent.name == cfg.name))
            existing = result.scalar_one_or_none()
            if existing:
                existing.description = cfg.description
                existing.version = cfg.version
                existing.config = cfg.model_dump()
                print(f"  Updated: {cfg.name}")
            else:
                agent = Agent(
                    name=cfg.name,
                    description=cfg.description,
                    version=cfg.version,
                    config=cfg.model_dump(),
                )
                session.add(agent)
                print(f"  Inserted: {cfg.name}")
        await session.commit()

    print(f"\nSeeded {len(configs)} agent(s) successfully.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
