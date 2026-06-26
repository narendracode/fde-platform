"""FastAPI application — agent platform REST API."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agri_agent.api.routes import actions, agents, approvals, dashboard, health, orders, outreach, pages, runs
from agri_agent.api.routes import settings as settings_router
from agri_agent.config.settings import settings

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(
    title="Fundly Agent Platform",
    description="Central API for deploying, running, and auditing AI agents.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(agents.router)
app.include_router(runs.router)
app.include_router(orders.router)
app.include_router(settings_router.router)
app.include_router(actions.router)
app.include_router(outreach.router)
app.include_router(pages.router)
app.include_router(dashboard.router)
app.include_router(approvals.router)


@app.on_event("startup")
async def on_startup():
    from agri_agent.telemetry import (
        instrument_fastapi,
        instrument_redis,
        setup_otel,
    )
    setup_otel()
    instrument_fastapi(app)
    instrument_redis()
    logging.info("Fundly Agent API started")
