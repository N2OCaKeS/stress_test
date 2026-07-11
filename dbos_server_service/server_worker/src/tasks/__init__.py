"""Точки входа task'ов — импорт этого пакета регистрирует всё на broker'е."""

from src.tasks import (  # noqa: F401
    astra_update,
    box_download,
    dispatch_outbox,
    installed_packages,
    inventory,
    management_creds,
    management_user,
    passwords,
    power,
    prepare,
    users,
    vms,
    vms_accounts,
    vms_disks,
    vms_inventory,
    vms_lifecycle,
    vms_network,
    vms_packages,
    vms_snapshots,
    vms_status,
)
