"""SQLAlchemy engine and session factory for PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import get_database_url


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """Return cached SQLAlchemy engine for the configured database URL."""
    return create_engine(url or get_database_url(), pool_pre_ping=True)


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Build a SQLAlchemy sessionmaker bound to the configured engine."""
    return sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Yield a scoped SQLAlchemy session for FastAPI dependencies."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
