"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.routers import clerk_proxy, dicomweb, health, settings, studies, thumbnails, uploads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_tables() -> None:
    """Create tables if they don't exist (dev/test only; production uses db/schema.sql)."""
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("sqlite") or not database_url:
        # Development/test: auto-create
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ensured (dev/test mode)")
    # Production: tables managed by db/schema.sql applied at deploy time


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    _ensure_tables()

    # Start worker only in production mode (not during tests)
    if not os.environ.get("TESTING"):
        from app.worker import start_worker
        start_worker()

    yield

    # Shutdown
    from app.worker import stop_worker
    stop_worker()


app = FastAPI(
    title="Radiology Worklist MVP",
    description="De-identified chest X-ray ingestion and AI analysis pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(health.router)
app.include_router(studies.router)
app.include_router(uploads.router)
app.include_router(thumbnails.router)
app.include_router(dicomweb.router)
app.include_router(settings.router)
app.include_router(clerk_proxy.router)

DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
ASSETS_DIR = DIST_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def serve_spa(path: str):
    """Serve the built React application while preserving API 404 semantics."""
    if path.startswith(("api/", "dicomweb/")):
        raise HTTPException(status_code=404, detail="Not found")

    requested = (DIST_DIR / path).resolve()
    if DIST_DIR in requested.parents and requested.is_file():
        return FileResponse(requested)

    index_file = DIST_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
    raise HTTPException(status_code=503, detail="Frontend build is not available")
