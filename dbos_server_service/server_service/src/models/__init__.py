"""Пакет ORM-моделей — импортируем все модели, чтобы Alembic их видел."""

from src.models.entity_permission import EntityPermission
from src.models.ipmi_controller import IpmiController
from src.models.os_version import OsVersion
from src.models.server import Server
from src.models.server_account import ServerAccount
from src.models.server_disk import ServerDisk

__all__ = [
    "EntityPermission",
    "IpmiController",
    "OsVersion",
    "Server",
    "ServerAccount",
    "ServerDisk",
]
