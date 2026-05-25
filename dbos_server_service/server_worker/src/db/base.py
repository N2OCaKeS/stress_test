"""Declarative base — единый `MetaData` для всех моделей worker'а."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Корневой DeclarativeBase. Все модели наследуются от него — Alembic
    подбирает их по `Base.metadata`."""
    pass
