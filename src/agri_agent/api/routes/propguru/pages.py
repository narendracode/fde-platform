"""Propguru UI page routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from agri_agent.api._templates import templates
from agri_agent.config.settings import settings

router = APIRouter(tags=["propguru-ui"])


@router.get("/propguru", response_class=HTMLResponse)
async def propguru_dashboard(request: Request):
    return templates.TemplateResponse(request, "propguru/dashboard.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_dashboard",
    })


@router.get("/propguru/master", response_class=HTMLResponse)
async def propguru_master(request: Request):
    return templates.TemplateResponse(request, "propguru/master.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_master",
    })


@router.get("/propguru/simulation", response_class=HTMLResponse)
async def propguru_simulation(request: Request):
    return templates.TemplateResponse(request, "propguru/simulation.html", {
        "api_key": settings.api_key,
        "active_page": "propguru_simulation",
    })
