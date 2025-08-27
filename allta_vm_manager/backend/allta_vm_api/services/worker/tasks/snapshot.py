from __future__ import annotations
from celery import shared_task
from tasks.common import log, _require, _std_ok
from utils.ssh import SimpleSSH

@shared_task(name="task_snapshot_create", bind=True) # Work
def task_snapshot_create(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names", "snapshot_name"])
    server = envelope.get("server") or {}
    vm_names = envelope.get("vm_names") or []
    snap     = envelope.get("snapshot_name")
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    parts = [f"sudo allta-vm snapshot create --snapshot-name {snap}"]
    for v in vm_names:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh.run_command(command=cmd)

    log.info("Ok snapshot.create RUN: task_id=%s snapshot=%s vm_names=%s",
             envelope["task_id"], snap, vm_names)
    return _std_ok(envelope)

@shared_task(name="task_snapshot_delete", bind=True) # Work
def task_snapshot_delete(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names", "snapshot_name"])
    server = envelope.get("server") or {}
    vm_names = envelope.get("vm_names") or []
    snap     = envelope.get("snapshot_name")
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])    

    parts = [f"sudo allta-vm snapshot delete --snapshot-name {snap}"]
    for v in vm_names:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh.run_command(command=cmd)

    log.info("Ok snapshot.delete RUN: task_id=%s snapshot=%s vm_names=%s",
             envelope["task_id"], snap, vm_names)
    return _std_ok(envelope)

@shared_task(name="task_snapshot_revert", bind=True) # Work
def task_snapshot_revert(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names", "snapshot_name"])
    server = envelope.get("server") or {}
    vm_names = envelope.get("vm_names") or []
    snap     = envelope.get("snapshot_name")
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])    

    parts = [f"sudo allta-vm snapshot revert --snapshot-name {snap}"]
    for v in vm_names:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh.run_command(command=cmd)

    log.info("Ok snapshot.revert RUN: task_id=%s snapshot=%s vm_names=%s",
             envelope["task_id"], snap, vm_names)
    return _std_ok(envelope)
