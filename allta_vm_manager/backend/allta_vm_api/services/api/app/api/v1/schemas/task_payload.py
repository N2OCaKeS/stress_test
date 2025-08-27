from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, IPvAnyAddress

class ServerTaskInfo(BaseModel):
    ip: IPvAnyAddress
    username: str
    password: str
    phy_if: Optional[str] = None

class VMFullSpec(BaseModel):
    id: Optional[int] = None
    name: str
    cpu: int
    ram: int
    ip_address: IPvAnyAddress
    server_id: int
    extra: Dict[str, Any] = Field(default_factory=dict) 

class TaskEnvelope(BaseModel):
    task_id: str
    operation: Literal[
        "server.init",
        "server.remove",
        "vm.create",
        "vm.base_create",
        "vm.update",
        "vm.power_on",
        "vm.power_off",
        "snapshot.create",
        "snapshot.delete",
        "snapshot.revert",
        "vm.astra_update",
    ]
    server: ServerTaskInfo

    vms_full: Optional[List[VMFullSpec]] = None
    vm_names: Optional[List[str]] = None


    snapshot_name: Optional[str] = None
    rc: Optional[str] = None
    box: Optional[str] = None
    kernel: Optional[str] = None


    json_remote_path: Optional[str] = None 
