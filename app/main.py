from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Configure logging so our logger.info() calls show up in container logs
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import (
    api_analysis,
    api_chat,
    api_health,
    api_library,
    api_rating_sync,
    api_settings,
    api_setup,  # Phase 6
    api_sync,
    api_vibes,  # Phase 6 Plan 04
    api_webhooks,
    pages,
)
from app.services.encryption import get_encryptor
from app.services.event_bus import get_event_bus, start_dispatcher, stop_dispatcher
from app.services.sync_scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, encryption key, event bus + dispatcher, sync scheduler.

    Order matters (Phase 5):
    1. init_db() — schema + migrations.
    2. get_encryptor() — generate encryption key on first startup.
    3. get_event_bus() — ensure asyncio.Queue exists.
    4. start_dispatcher() — start consumer BEFORE any producer (scheduler/webhooks).
    5. start_scheduler() — APScheduler registers library_sync + plex_polling jobs.
    6. maybe_trigger_first_run_backfill() — fires once if Phase 5 just deployed
       onto a populated DB whose user_rating column is still NULL across the board.
    """
    init_db()
    get_encryptor()
    # Phase 5: queue → dispatcher → scheduler order is mandatory.
    get_event_bus()
    await start_dispatcher()
    await start_scheduler()
    # Phase 5 (D-10 / Pitfall 7): auto-trigger backfill if we have tracks but
    # nothing is rated — classic "first deploy onto an existing v1 library".
    from app.services.backfill_service import maybe_trigger_first_run_backfill

    asyncio.create_task(maybe_trigger_first_run_backfill())
    yield
    await stop_scheduler()
    await stop_dispatcher()


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
app.include_router(api_webhooks.router)  # Phase 5
app.include_router(api_rating_sync.router)  # Phase 5
app.include_router(api_setup.router)  # Phase 6 — MUST be before pages.router
app.include_router(api_vibes.router)  # Phase 6 Plan 04 — MUST be before pages.router

# Register feature_chip_text helper as a Jinja2 global so templates can call it.
from app.services.vibe_helpers import feature_chip_text  # noqa: E402

templates.env.globals["feature_chip_text"] = feature_chip_text

app.include_router(pages.router)
