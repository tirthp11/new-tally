"""Declarative base and model registry import point for Alembic autogeneration."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def import_all_models() -> None:
    """Import every model module so Base.metadata knows all tables."""
    from app.models import (  # noqa: F401
        audit_log,
        connector,
        document,
        job,
        masters_cache,
        tally_company,
        user,
        voucher,
    )
