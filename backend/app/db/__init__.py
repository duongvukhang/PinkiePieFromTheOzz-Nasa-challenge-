"""Database metadata, flag bitmasks, and ORM models."""

from app.db import models as _models  # noqa: F401  # register all ORM tables on Base.metadata
from app.db.base import Base, get_database_url
from app.db.flags import DetectionFlag

__all__ = ["Base", "DetectionFlag", "get_database_url"]
