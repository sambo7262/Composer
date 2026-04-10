from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import api_analysis, api_chat, api_health, api_library, api_settings, api_sync, pages
from app.services.encryption import get_encryptor
from app.services.sync_scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, encryption key, and sync scheduler. Shutdown: cleanup."""
    init_db()
    get_encryptor()  # Generate encryption key on first startup
    await start_scheduler()
    yield
    await stop_scheduler()


app = FastAPI(title="Composer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Include routers
app.include_router(api_analysis.router)
app.include_router(api_chat.router)
app.include_router(api_health.router)
app.include_router(api_library.router)
app.include_router(api_settings.router)
app.include_router(api_sync.router)
app.include_router(pages.router)
