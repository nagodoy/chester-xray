"""Shared test fixtures.

The schema under test is built by running the real migrations rather than
``Base.metadata.create_all``. That way a migration that fails to reproduce the models
breaks the tests here, instead of only in the environment that applies it.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("PSEUDONYM_SECRET", "test-pseudonym-secret")
os.environ.setdefault("DICOM_INGEST_TOKEN", "test-dicom-ingest-token")

SERVER_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    return f"sqlite+pysqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"


@pytest.fixture(scope="session")
def migrated_engine(database_url: str):
    """A database whose schema was produced by ``alembic upgrade head``."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture
def session(migrated_engine) -> Generator:
    """A session wrapped in a transaction that is rolled back after each test."""
    from sqlalchemy.orm import sessionmaker

    connection = migrated_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def organization(session):
    from chester.models import Organization

    org = Organization(name="Test Org", slug="test-org")
    session.add(org)
    session.flush()
    return org


@pytest.fixture
def make_user(session, organization):
    from chester.models import User
    from chester.security.roles import ROLE_TECHNICIAN

    def _make(email: str, role: str = ROLE_TECHNICIAN, *, org=None, **kwargs):
        user = User(
            email=email.strip().casefold(),
            organization_id=(org or organization).id,
            role=role,
            **kwargs,
        )
        session.add(user)
        session.flush()
        return user

    return _make
