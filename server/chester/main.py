"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from chester.security.access import bootstrap_env_admins

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Refuse to start on development defaults, then reconcile administrators.

    Schema is applied by `alembic upgrade head` before the process starts. There is
    deliberately no DDL here: creating tables at startup is how an application and
    its migrations drift apart.
    """
    settings.require_production_secrets()
    with session_scope() as session:
        bootstrap_env_admins(session)
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="Chester research worklist",
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
    return application


app = create_app()
