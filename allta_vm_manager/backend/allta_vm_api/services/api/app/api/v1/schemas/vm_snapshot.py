from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# одинаковая структура server в тасках
from app.api.v1.schemas.vm import ServerTaskInfo


# ----- Pydantic-схема для ответа -----
class VMSnapshotRead(BaseModel):
    id: int = Field(..., examples=[42])
    vm_id: int = Field(..., examples=[10])
    name: str = Field(..., examples=["golden-2025-02"])
    created_at: Optional[datetime] = Field(None, examples=["2025-03-01T12:34:56Z"])

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": 42,
                "vm_id": 10,
                "name": "golden-2025-02",
                "created_at": "2025-03-01T12:34:56Z"
            }
        },
    }


# -------- Операции со снимками (таски для воркера) --------
class SnapshotTaskOperation(str, Enum):
    create = "snapshot.create"
    delete = "snapshot.delete"
    revert = "snapshot.revert"


class SnapshotTaskEnvelope(BaseModel):
    """
    Конверт для Redis: единый вид.
    vms — сопоставление { vm_name: vm_id }.
    """
    task_id: str = Field(..., examples=["d2a5b0b3-8f3f-49e0-9b1b-abd4c2e27a00"])
    operation: SnapshotTaskOperation
    server: ServerTaskInfo = Field(
        ...,
        description="Данные сервера, обязательны для всех операций."
    )
    vms: Dict[str, int] = Field(
        ...,
        description="Сопоставление { vm_name: vm_id }",
        examples=[{"ws-01": 10, "ws-02": 12}],
    )
    snapshot_name: str = Field(..., min_length=1, max_length=128, examples=["golden-2025-02"])
    json_remote_path: str = Field(
        ...,
        description="Полный путь на сервере, где воркер ожидает JSON задания",
        examples=["/opt/allta_vm/jobs/d2a5b0b3-8f3f-49e0-9b1b-abd4c2e27a00.json"],
    )

    @model_validator(mode="after")
    def _check_minimum(self):
        if not self.vms:
            raise ValueError("vms mapping must be non-empty")
        if not self.snapshot_name or not self.snapshot_name.strip():
            raise ValueError("snapshot_name is required")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": "d2a5b0b3-8f3f-49e0-9b1b-abd4c2e27a00",
                "operation": "snapshot.create",
                "server": {
                    "id": 2,
                    "ip": "10.0.0.5",
                    "username": "root",
                    "password": "p@ss",
                    "phy_if": "eth0"
                },
                "vms": {"ws-01": 10, "ws-02": 12},
                "snapshot_name": "golden-2025-02",
                "json_remote_path": "/opt/allta_vm/jobs/d2a5b0b3-8f3f-49e0-9b1b-abd4c2e27a00.json",
            }
        }
    }


# -------- Публичные payload'ы API --------
class SnapshotSelection(BaseModel):
    """
    Входной payload для create/delete/revert.
    Можно указывать ids и/или names одновременно — возьмём объединение.
    """
    ids: Optional[List[int]] = Field(default=None, description="Список id ВМ", examples=[[10, 12]])
    names: Optional[List[str]] = Field(default=None, description="Список имён ВМ", examples=[["ws-01", "ws-02"]])
    snapshot: str = Field(..., min_length=1, max_length=128, examples=["golden-2025-02"])

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
                "snapshot": "golden-2025-02"
            }
        }
    }
