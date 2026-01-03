from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cocbot.config import settings

app = FastAPI(title="CoC Dice Bot Dashboard")

templates_dir = settings.ROOT / "apps" / "dashboard" / "templates"
static_dir = settings.ROOT / "apps" / "dashboard" / "static"

templates = Jinja2Templates(directory=str(templates_dir))

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # temporary placeholder page so we know it's running
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "db_path": str(settings.DB_PATH)},
    )
