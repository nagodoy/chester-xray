"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from chester.config import settings


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
