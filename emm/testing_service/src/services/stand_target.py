"""Цель стенда в server_service: физический сервер или ВМ.

`test_stands` ссылается либо на `Server.id` (`target_type=server`), либо на
`Vm.id` (`target_type=vm`). Все вызовы в server_service (бронь, подготовка,
connection-info, batch-status) идут через `StandTarget`: `server_client`
сам выбирает путь `/internal/servers/{id}/…` или `/internal/vms/{id}/…`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TARGET_SERVER = "server"
TARGET_VM = "vm"

StandTargetType = Literal["server", "vm"]


@dataclass(frozen=True)
class StandTarget:
    type: StandTargetType
    id: str

    @classmethod
    def server(cls, server_id: str) -> "StandTarget":
        return cls(TARGET_SERVER, server_id)

    @classmethod
    def vm(cls, vm_id: str) -> "StandTarget":
        return cls(TARGET_VM, vm_id)

    @property
    def is_vm(self) -> bool:
        return self.type == TARGET_VM

    @property
    def key(self) -> str:
        """Ключ кэшей (`server:<id>` / `vm:<id>`): id серверов и ВМ — разные пространства."""
        return f"{self.type}:{self.id}"


def target_of(stand) -> StandTarget:
    """Цель стенда по его строке (`TestStand`)."""
    if getattr(stand, "target_type", TARGET_SERVER) == TARGET_VM:
        return StandTarget.vm(stand.vm_id)
    return StandTarget.server(stand.server_id)


def is_vm(stand) -> bool:
    return getattr(stand, "target_type", TARGET_SERVER) == TARGET_VM
