"""FastAPI application — agent platform REST API."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fde_agent.api.routes import actions, agents, approvals, dashboard, health, orders, outreach, pages, runs
from fde_agent.api.routes import settings as settings_router
from fde_agent.api.routes.sandhar import pages as sandhar_pages
from fde_agent.api.routes.sandhar import (
    master as sandhar_master,
    skills as sandhar_skills,
    attendance as sandhar_attendance,
    workorders as sandhar_workorders,
    constraints as sandhar_constraints,
    planning as sandhar_planning,
    execution as sandhar_execution,
    alerts as sandhar_alerts,
    kpi as sandhar_kpi,
    simulation as sandhar_simulation,
)
from fde_agent.api.routes.propguru import pages as propguru_pages
from fde_agent.api.routes.propguru import (
    master as propguru_master,
    deals as propguru_deals,
    evaluation as propguru_evaluation,
    simulation as propguru_simulation,
)
from fde_agent.config.settings import settings

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(
    title="Agent Platform",
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

# ── Sandhar Production Planning ──────────────────────────────────────────────
app.include_router(sandhar_master.router)
app.include_router(sandhar_skills.router)
app.include_router(sandhar_attendance.router)
app.include_router(sandhar_workorders.router)
app.include_router(sandhar_constraints.router)
app.include_router(sandhar_planning.router)
app.include_router(sandhar_execution.router)
app.include_router(sandhar_alerts.router)
app.include_router(sandhar_kpi.router)
app.include_router(sandhar_simulation.router)
app.include_router(sandhar_pages.router)

# ── Propguru Property Evaluation ─────────────────────────────────────────────
app.include_router(propguru_master.router)
app.include_router(propguru_deals.router)
app.include_router(propguru_evaluation.router)
app.include_router(propguru_simulation.router)
app.include_router(propguru_pages.router)


@app.on_event("startup")
async def on_startup():
    from fde_agent.telemetry import (
        instrument_fastapi,
        instrument_redis,
        setup_otel,
    )
    setup_otel()
    instrument_fastapi(app)
    instrument_redis()
    logging.info("Fundly Agent API started")
