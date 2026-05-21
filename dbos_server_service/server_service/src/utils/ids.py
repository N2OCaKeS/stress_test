"""Генерация prefixed ID. По одной фабрике на каждый prefix."""

import uuid


def _new_id(prefix: str) -> str:
    """`prefix + uuid4.hex` — единственная точка генерации, единый формат."""
    return f"{prefix}{uuid.uuid4().hex}"


def server_id() -> str:
    """`srv_<uuid>` — для таблицы servers."""
    return _new_id("srv_")


def os_version_id() -> str:
    """`osv_<uuid>` — для os_versions. Сейчас неиспользуется (CRUD заглушки)."""
    return _new_id("osv_")


def server_account_id() -> str:
    """`acc_<uuid>` — для server_accounts."""
    return _new_id("acc_")


def ipmi_controller_id() -> str:
    """`ipm_<uuid>` — для ipmi_controllers."""
    return _new_id("ipm_")


def entity_permission_id() -> str:
    """`prm_<uuid>` — для entity_permissions."""
    return _new_id("prm_")


def server_disk_id() -> str:
    """`dsk_<uuid>` — для server_disks."""
    return _new_id("dsk_")
