"""Пакет ORM-моделей — импортируем все модели, чтобы Alembic их видел."""

from src.models.acs_department_access import AcsDepartmentAccess
from src.models.acs_settings import AcsSettings
from src.models.box import Box
from src.models.console_macro import ConsoleMacro
from src.models.dispatch_outbox import DispatchOutbox
from src.models.entity_permission import EntityPermission
from src.models.ipmi_controller import IpmiController
from src.models.management_user_config import ManagementUserConfig
from src.models.os_version import OsVersion
from src.models.password_policy_settings import PasswordPolicySettings
from src.models.probe_settings import ProbeSettings
from src.models.resource_role_permission import ResourceRolePermission
from src.models.server import Server
from src.models.secrets_migration_state import SecretsMigrationState
from src.models.secrets_outbox import ReencryptOutboxEntry
from src.models.server_account import (
    ServerAccount,
    ServerAccountServer,
    ServerAccountVm,
)
from src.models.server_account_ignored_login import ServerAccountIgnoredLogin
from src.models.server_disk import ServerDisk
from src.models.vm import Vm
from src.models.vm_disk import VmDisk
from src.models.vm_image import VmImage
from src.models.vm_ip_pool import VmIpPool
from src.models.vm_package_inventory import VmPackageInventory
from src.models.vm_preset import VmPreset
from src.models.vm_snapshot import VmSnapshot

__all__ = [
    "AcsDepartmentAccess",
    "AcsSettings",
    "Box",
    "ConsoleMacro",
    "DispatchOutbox",
    "EntityPermission",
    "IpmiController",
    "ManagementUserConfig",
    "OsVersion",
    "PasswordPolicySettings",
    "ProbeSettings",
    "ReencryptOutboxEntry",
    "ResourceRolePermission",
    "SecretsMigrationState",
    "Server",
    "ServerAccount",
    "ServerAccountIgnoredLogin",
    "ServerAccountServer",
    "ServerAccountVm",
    "ServerDisk",
    "Vm",
    "VmDisk",
    "VmImage",
    "VmIpPool",
    "VmPackageInventory",
    "VmPreset",
    "VmSnapshot",
]
