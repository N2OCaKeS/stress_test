"""Declarative base — все ORM-модели наследуются от `Base`."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Корневой класс для всех ORM-моделей auth_service."""
    pass
