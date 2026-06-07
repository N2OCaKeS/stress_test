"""Declarative-base для всех ORM-моделей. Alembic читает metadata отсюда."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц сервиса."""

    pass
