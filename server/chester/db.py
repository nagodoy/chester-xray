"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, MetaData, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from chester.config import settings


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC, on every backend.

    PostgreSQL round-trips ``TIMESTAMP WITH TIME ZONE`` faithfully, but SQLite has
    no timezone storage and hands back naive values. Without normalization the same
    comparison works in production and raises "can't compare offset-naive and
    offset-aware datetimes" in tests, which is a difference between environments
    rather than a difference in behaviour.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class JsonDocument(TypeDecorator):
    """A JSON document stored as ``JSONB`` on PostgreSQL and ``JSON`` on SQLite.

    Plain ``JSON`` keeps the document as text, so every read reparses it and no
    index can be built over its keys. ``JSONB`` is the type these columns want, and
    the one the project's earlier schema already used; mapping them to ``sa.JSON``
    was an unintended downgrade carried in by the rewrite. SQLite has only one JSON
    representation, so the variant matters solely on PostgreSQL.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


def _engine_kwargs(url: str) -> tuple[str, dict]:
    """Normalize the URL and pick pooling appropriate to the driver."""
    if url.startswith("sqlite"):
        return url, {"connect_args": {"check_same_thread": False}}
    # Managed Postgres hands out a plain postgresql:// URL while this project uses
    # psycopg 3 rather than the legacy psycopg2 driver.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url, {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


_url, _kwargs = _engine_kwargs(settings.database_url)
engine = create_engine(_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# Deterministic constraint names on every backend, so a constraint declared inline
# (unique=True on a column) is not created anonymously. Names that differ between
# PostgreSQL and SQLite would make a violation surface as a different error string
# in tests than in production, and an anonymous constraint cannot be referenced at
# all when a table has to be rebuilt.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for background work outside a request."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
