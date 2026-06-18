"""Пакет ORM-моделей — импортируем все модели, чтобы Alembic их видел."""

from src.models.dispatch_outbox import DispatchOutbox
from src.models.entity_permission import EntityPermission
from src.models.ipmi_controller import IpmiController
from src.models.os_version import OsVersion
from src.models.server import Server
from src.models.secrets_outbox import ReencryptOutboxEntry
from src.models.server_account import ServerAccount, ServerAccountServer
from src.models.server_account_ignored_login import ServerAccountIgnoredLogin
from src.models.server_account_user_acl import ServerAccountUserAcl
from src.models.server_disk import ServerDisk

__all__ = [
    "DispatchOutbox",
    "EntityPermission",
    "IpmiController",
    "OsVersion",
    "ReencryptOutboxEntry",
    "Server",
    "ServerAccount",
    "ServerAccountIgnoredLogin",
    "ServerAccountServer",
    "ServerAccountUserAcl",
    "ServerDisk",
]
