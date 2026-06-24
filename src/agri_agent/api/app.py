"""FastAPI application — agent platform REST API."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agri_agent.api.routes import agents, health, runs
from agri_agent.config.settings import settings

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(
    title="AgriScience Agent Platform",
    description=(
        "Central API for deploying, running, and auditing AI agents. "
        "Pairs with LangFlow UI at http://localhost:7860."
    ),
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


@app.on_event("startup")
async def on_startup():
    logging.info("AgriScience Agent API started")
