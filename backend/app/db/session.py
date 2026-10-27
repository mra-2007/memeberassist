"""Database engine and session management.

The application uses synchronous SQLAlchemy sessions run in FastAPI's thread
pool. That keeps the tool layer simple to reason about and to audit, which
matters more here than the small throughput gain of an async driver.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide engine built from ``DATABASE_URL``."""
    settings = get_settings()
    if settings.is_sqlite:
        engine = create_engine(
            settings.resolved_database_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        _enable_sqlite_foreign_keys(engine)
        return engine
    return create_engine(
        settings.resolved_database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked, which would hide real bugs."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Session factory bound to the process engine."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background work."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
