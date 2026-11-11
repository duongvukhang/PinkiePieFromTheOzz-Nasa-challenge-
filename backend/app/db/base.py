"""SQLAlchemy declarative base and database URL helper."""

import os

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_database_url() -> str:
    """Return configured PostgreSQL connection URI from environment."""
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://sphx:sphx_dev_password@localhost:5432/sphx",
    )
