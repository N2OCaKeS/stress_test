# app/api/v1/schemas/vm.py
from __future__ import annotations

from enum import Enum
from typing import Optional, Dict, List
import re

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.networks import IPvAnyAddress


# ---------- Общие вещи: статусы ----------
class VMFixedStatus(str, Enum):
    free = "free"
    run_test = "run test"
    debug_test = "debug test"


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def normalize_status(value: str) -> str:
    v = value.strip()
    v_low = v.lower().replace("_", " ")
    if v_low in {s.value for s in VMFixedStatus}:
        return VMFixedStatus(v_low).value
    if not _USERNAME_RE.match(v):
        raise ValueError(
            "invalid status: must be one of fixed statuses "
            f"{[s.value for s in VMFixedStatus]} or a valid username (3-64, a-zA-Z0-9._-)"
        )
    return v


# ---------- DB-view моделей ВМ ----------
class VMBase(BaseModel):
    name: str = Field(..., examples=["vm-ubuntu-01"])
    cpu: int = Field(..., gt=0, examples=[4])
    ram: int = Field(..., gt=0, examples=[8192])
    ip_address: IPvAnyAddress = Field(..., examples=["10.0.0.55"])
    server_id: int = Field(..., examples=[2])


class VMCreate(VMBase):
    """Используется воркером для записи в БД."""


class VMUpdate(BaseModel):
    name: Optional[str] = None
    cpu: Optional[int] = Field(None, gt=0)
    ram: Optional[int] = Field(None, gt=0)
    ip_address: Optional[IPvAnyAddress] = None


class VMStatusUpdate(BaseModel):
    status: str = Field(..., description="fixed статус (free/run test/debug test) или логин пользователя")

    @field_validator("status")
    @classmethod
    def _normalize_status(cls, v: str) -> str:
        return normalize_status(v)

    model_config = {
        "json_schema_extra": {
            "example": {"status": "run test"}
        }
    }


class VMRead(VMBase):
    id: int
    status: Optional[str] = None
    # для UI — расшифрованный пароль, если сохранён
    password: Optional[str] = Field(default=None, description="Расшифрованный пароль ВМ (если сохранён).")

    model_config = {"from_attributes": True}


# ---------- Payload’ы для публичных эндпоинтов ----------
class CreateVMItem(BaseModel):
    cpu: int = Field(..., gt=0, examples=[4])
    ram: int = Field(..., gt=0, examples=[8192])
    ip: IPvAnyAddress = Field(..., examples=["10.0.0.55"])


class BatchVMCreateRequest(BaseModel):
    server_id: Optional[int] = Field(default=None, examples=[2])
    server_name: Optional[str] = Field(default=None, examples=["stand12_srv-main"])
    ip_range_id: int = Field(..., examples=[5])
    # общий пароль для всей группы ВМ (в открытом виде; шифрует воркер при записи в БД)
    password: str = Field(..., min_length=1, examples=["S3curePass!"])
    # ключ — имя ВМ
    vms: Dict[str, CreateVMItem]

    @model_validator(mode="after")
    def _exactly_one_server_ref(self):
        has_id = self.server_id is not None
        has_name = bool(self.server_name and self.server_name.strip())
        if has_id == has_name:
            raise ValueError("Provide exactly one of: server_id or server_name.")
        if self.server_name is not None:
            self.server_name = self.server_name.strip()
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "server_name": "stand12_srv-main",
                "ip_range_id": 5,
                "password": "S3curePass!",
                "vms": {
                    "ws-01": {"cpu": 4, "ram": 8192, "ip": "10.0.0.101"},
                    "ws-02": {"cpu": 8, "ram": 16384, "ip": "10.0.0.102"}
                }
            }
        }
    }


class PatchItem(BaseModel):
    cpu: Optional[int] = Field(None, gt=0)
    ram: Optional[int] = Field(None, gt=0)


class BatchVMUpdateRequest(BaseModel):
    # ключ — имя ВМ
    vms: Dict[str, PatchItem]

    @model_validator(mode="after")
    def _non_empty_patches(self):
        empty = [k for k, p in self.vms.items() if p.cpu is None and p.ram is None]
        if empty:
            raise ValueError(f"patch for {empty} must specify cpu and/or ram")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "vms": {
                    "ws-01": {"cpu": 8},
                    "ws-02": {"ram": 32768},
                    "ws-03": {"cpu": 4, "ram": 8192}
                }
            }
        }
    }


class VMDeleteRequest(BaseModel):
    # Можно указывать и ids, и names одновременно (хотя разрешение/поиск БД дальше всё равно уточнит фактически найденные)
    ids: Optional[List[int]] = Field(default=None, description="Идентификаторы ВМ")
    names: Optional[List[str]] = Field(default=None, description="Имена ВМ")

    @model_validator(mode="after")
    def _at_least_one(self):
        if not (self.ids or self.names):
            raise ValueError("Provide at least one of: ids or names (both allowed).")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "ids": [1, 2, 3],
                "names": ["ws-01", "ws-02"]
            }
        }
    }


class AstraUpdateRequest(BaseModel):
    rc: str = Field(..., min_length=1, examples=["1.8.1.6"])
    # можно выбрать по ids или по names; обе группы допустимы одновременно — возьмём объединение
    ids: Optional[List[int]] = Field(default=None, description="Идентификаторы ВМ")
    names: Optional[List[str]] = Field(default=None, description="Имена ВМ")

    @model_validator(mode="after")
    def _at_least_one_selector(self):
        if not (self.ids or self.names):
            raise ValueError("Provide at least one of: ids or names (both allowed).")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": "1.8.1.6",
                "ids": [10, 12],
                "names": ["ws-01", "ws-02"]
            }
        }
    }


class AlltaUpdateRequest(BaseModel):
    ids: Optional[List[int]] = Field(default=None, description="Идентификаторы ВМ")
    names: Optional[List[str]] = Field(default=None, description="Имена ВМ")
    password: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Если задан, после allta update пароль будет изменён на указанный.",
        examples=["S3curePass!"],
    )

    @model_validator(mode="after")
    def _at_least_one_selector(self):
        if not (self.ids or self.names):
            raise ValueError("Provide at least one of: ids or names (both allowed).")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "ids": [10, 12],
                "names": ["ws-01", "ws-02"],
                "password": "S3curePass!",
            }
        }
    }


class PasswdRefreshRequest(BaseModel):
    ids: Optional[List[int]] = Field(default=None, description="Идентификаторы ВМ")
    names: Optional[List[str]] = Field(default=None, description="Имена ВМ")
    password: str = Field(
        ...,
        min_length=1,
        description="Новый пароль, который будет установлен после allta update на всех снимках.",
        examples=["S3curePass!"],
    )

    @model_validator(mode="after")
    def _at_least_one_selector(self):
        if not (self.ids or self.names):
            raise ValueError("Provide at least one of: ids or names (both allowed).")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "ids": [10, 12],
                "names": ["ws-01", "ws-02"],
                "password": "S3curePass!",
            }
        }
    }


class CreateDefaultVMsRequest(BaseModel):
    # общий пароль для «базовых» ВМ
    password: str = Field(..., examples=["S3curePass!"])

    model_config = {
        "json_schema_extra": {
            "example": {"password": "S3curePass!"}
        }
    }


# ---------- (Опционально) схемы для конвертов в Redis — если ты их тоже делаешь публичными ----------
class ServerTaskInfo(BaseModel):
    id: int = Field(..., description="ID сервера", examples=[2])
    ip: str = Field(..., examples=["10.0.0.5"])
    username: str = Field(..., examples=["root"])
    password: str = Field(..., examples=["p@ss"])
    phy_if: Optional[str] = Field(None, description="Обязателен для server.init/server.remove", examples=["eth0"])


class VMSpec(BaseModel):
    cpu: int
    ram: int
    ip_bridge: IPvAnyAddress
    server_id: int
    host_port: str = Field(default="22", alias="host-port")
    model_config = {"populate_by_name": True}


class TaskOperation(str, Enum):
    server_init = "server.init"
    server_remove = "server.remove"
    vm_create = "vm.create"
    vm_base_create = "vm.base_create"
    vm_delete = "vm.delete"
    vm_update = "vm.update"
    vm_start = "vm.start"
    vm_stop = "vm.stop"
    vm_astra_update = "vm.astra_update"
    vm_allta_update = "vm.allta_update"


class TaskEnvelope(BaseModel):
    task_id: str
    operation: TaskOperation
    server: ServerTaskInfo
    json_remote_path: str

    vm_password: Optional[str] = None
    new_password: Optional[str] = None
    vms_full: Optional[Dict[str, VMSpec]] = None
    vm_names: Optional[List[str]] = None
    snapshot_name: Optional[str] = None
    rc: Optional[str] = None  # только для vm.astra_update

    @model_validator(mode="after")
    def _by_op(self):
        if self.operation in {TaskOperation.server_init, TaskOperation.server_remove} and not self.server.phy_if:
            raise ValueError("server.phy_if is required for server.init/server.remove")
        if self.operation in {TaskOperation.vm_create, TaskOperation.vm_base_create}:
            if not self.vms_full:
                raise ValueError("vms_full is required for vm.create/vm.base_create")
            if not self.vm_password:
                raise ValueError("vm_password is required for vm.create/vm.base_create")
        if self.operation in {TaskOperation.vm_delete, TaskOperation.vm_start, TaskOperation.vm_stop} and not self.vm_names:
            raise ValueError("vm_names is required for vm.delete/vm.start/vm.stop")
        if self.operation == TaskOperation.vm_update and not self.vms_full:
            raise ValueError("vms_full is required for vm.update (patch as full spec per VM)")
        if self.operation == TaskOperation.vm_astra_update:
            if not self.vm_names:
                raise ValueError("vm_names is required for vm.astra_update")
            if not self.rc:
                raise ValueError("rc is required for vm.astra_update")
        if self.operation == TaskOperation.vm_allta_update and not self.vm_names:
            raise ValueError("vm_names is required for vm.allta_update")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": "f3c1e3a2-5a0d-4d0f-9b8c-0fd2e8b0c1b2",
                "operation": "vm.create",
                "server": {
                    "id": 2,
                    "ip": "10.0.0.5",
                    "username": "root",
                    "password": "p@ss",
                    "phy_if": "eth0"
                },
                "vm_password": "S3curePass!",
                "vms_full": {
                    "ws-01": {"cpu": 4, "ram": 8192, "ip_bridge": "10.0.0.101", "server_id": 2, "host-port": "22"}
                },
                "json_remote_path": "/opt/allta_vm/jobs/f3c1e3a2-5a0d-4d0f-9b8c-0fd2e8b0c1b2.json"
            }
        }
    }
