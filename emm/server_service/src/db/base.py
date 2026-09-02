"""Declarative-base для всех ORM-моделей. Sub-классим SQLAlchemy DeclarativeBase."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц. Alembic читает metadata именно отсюда."""

    pass
