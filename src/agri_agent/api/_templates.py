"""Shared Jinja2Templates instance used by all page route modules."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from agri_agent.config.settings import settings

_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Parse comma-separated string and expose as list to every template.
templates.env.globals["companies"] = [
    c.strip().lower() for c in settings.companies_to_show.split(",") if c.strip()
]
