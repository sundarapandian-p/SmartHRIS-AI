"""SQLAlchemy declarative base for ORM models (added in later phases)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata registry for Alembic autogenerate."""

    pass
