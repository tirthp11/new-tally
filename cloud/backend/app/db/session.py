"""Database engine and session management.

One external PostgreSQL, reached only through DATABASE_URL. The same code runs
against the developer's own pgAdmin-managed PostgreSQL and against Railway's
PostgreSQL in production.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Point it at your PostgreSQL "
                "(see .env.example at the repository root)."
            )
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.expire_all()
        db.close()
