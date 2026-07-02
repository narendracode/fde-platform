"""Sandhar UI page routes."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from agri_agent.config.settings import settings

router = APIRouter(tags=["sandhar-ui"])

_templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@router.get("/sandhar", response_class=HTMLResponse)
async def sandhar_dashboard(request: Request):
    return templates.TemplateResponse(request, "sandhar/dashboard.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_dashboard",
    })


@router.get("/sandhar/plan", response_class=HTMLResponse)
async def sandhar_plan(request: Request):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return templates.TemplateResponse(request, "sandhar/plan.html", {
        "api_key": settings.api_key,
        "default_date": tomorrow,
        "today": date.today().isoformat(),
        "active_page": "sandhar_plan",
    })


@router.get("/sandhar/floor", response_class=HTMLResponse)
async def sandhar_floor(request: Request):
    return templates.TemplateResponse(request, "sandhar/floor.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_floor",
    })


@router.get("/sandhar/master", response_class=HTMLResponse)
async def sandhar_master(request: Request):
    return templates.TemplateResponse(request, "sandhar/master.html", {
        "api_key": settings.api_key,
        "active_page": "sandhar_master",
    })


@router.get("/sandhar/simulation", response_class=HTMLResponse)
async def sandhar_simulation(request: Request):
    return templates.TemplateResponse(request, "sandhar/simulation.html", {
        "api_key": settings.api_key,
        "today": date.today().isoformat(),
        "active_page": "sandhar_simulation",
    })
