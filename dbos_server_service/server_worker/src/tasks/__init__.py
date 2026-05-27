"""Точки входа task'ов — импорт этого пакета регистрирует всё на broker'е."""

from src.tasks import (  # noqa: F401
    installed_packages,
    inventory,
    passwords,
    power,
    users,
)
