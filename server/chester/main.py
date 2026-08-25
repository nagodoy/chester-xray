"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chester.api import (
    access_control,
    auth,
    dicomweb,
    health,
    settings_routes,
    studies,
    thumbnails,
    uploads,
)
from chester.config import settings
from chester.db import session_scope
from chester.schema import drift as schema_drift
from chester.security.access import bootstrap_env_admins

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Refuse to start on development defaults, then reconcile administrators.

    Schema is created by `python -m chester.schema` before the process starts. No
    DDL is issued here on purpose: the API and the worker come up in parallel, so
    two processes creating tables would race each other. What happens here is the
    read-only half -- reporting a database that no longer matches the models, which
    would otherwise only surface as a query error deep in a request.
    """
    settings.require_production_secrets()
    problems = schema_drift()
    if problems:
        logger.error(
            "The database does not match the models. Run `python -m chester.schema`; "
            "if it still reports this, the affected tables must be dropped and "
            "recreated. Found: %s",
            "; ".join(problems),
        )
    with session_scope() as session:
        bootstrap_env_admins(session)
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="Torax AI",
        description="De-identified chest radiograph ingestion and analysis.",
        version="2.0.0",
        lifespan=lifespan,
    )
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(studies.router)
    application.include_router(thumbnails.router)
    application.include_router(uploads.router)
    application.include_router(settings_routes.router)
    application.include_router(access_control.router)
    application.include_router(dicomweb.router)
    _mount_frontend(application)
    return application


DIST_DIR = Path(__file__).resolve().parent.parent.parent / "dist"
API_PREFIXES = ("api/", "dicomweb/", "wado/")


def _mount_frontend(application: FastAPI) -> None:
    """Serve the built single-page application, if there is one.

    Absent in development, where Vite serves the frontend and proxies the API.
    """
    assets = DIST_DIR / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_spa(path: str):
        # API routes must keep their own 404 semantics rather than being answered
        # with the application shell.
        if path.startswith(API_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found")

        if path:
            candidate = (DIST_DIR / path).resolve()
            # Confine to the build directory: a crafted path must not escape it.
            if candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)

        index = DIST_DIR / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(status_code=503, detail="Frontend build is not available")


app = create_app()
