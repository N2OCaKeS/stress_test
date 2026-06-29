"""Точки входа task'ов — импорт этого пакета регистрирует всё на broker'е."""

from src.tasks import (  # noqa: F401
    dispatch_outbox,
    installed_packages,
    inventory,
    management_creds,
    management_user,
    passwords,
    power,
    prepare,
    users,
)
