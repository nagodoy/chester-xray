"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine
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


class Base(DeclarativeBase):
    pass


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
