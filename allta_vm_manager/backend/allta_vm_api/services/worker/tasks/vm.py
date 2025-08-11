# services/worker/worker/tasks_vm.py
from typing import Iterable, Optional
from celery_app import celery_app
from utils.ssh import SimpleSSH as ssh_run

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.create")
def vm_create(*, host_ip: str, username: str, password: str,
              info_path: str, rc: str, box: Optional[str] = None, kernel: Optional[str] = None,
              port: int = 22):
    """
    sudo allta-vm vm create --info-path ... --rc ... [--box ...] [--kernel ...]
    """
    parts = [f"sudo allta-vm vm create --info-path {info_path} --rc {rc}"]
    if box:    parts.append(f"--box {box}")
    if kernel: parts.append(f"--kernel {kernel}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_create_done"}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.base_create")
def vm_base_create(*, host_ip: str, username: str, password: str, port: int = 22):
    cmd = "sudo allta-vm vm base-create"
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_base_create_done"}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.delete")
def vm_delete(*, host_ip: str, username: str, password: str, vms: Iterable[str], port: int = 22):
    """
    sudo allta-vm vm delete --vms vm1 --vms vm2 ...
    """
    parts = ["sudo allta-vm vm delete"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_delete_done", "count": len(list(vms))}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.update")
def vm_update(*, host_ip: str, username: str, password: str, info_path: str, port: int = 22):
    cmd = f"sudo allta-vm vm update --info-path {info_path}"
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_update_done"}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.stop")
def vm_stop(*, host_ip: str, username: str, password: str, vms: Iterable[str], port: int = 22):
    parts = ["sudo allta-vm vm stop"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_stop_done", "count": len(list(vms))}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.start")
def vm_start(*, host_ip: str, username: str, password: str, vms: Iterable[str], port: int = 22):
    parts = ["sudo allta-vm vm start"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_start_done", "count": len(list(vms))}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="vm.astra_update")
def vm_astra_update(*, host_ip: str, username: str, password: str, info_path: str, rc: str, port: int = 22):
    cmd = f"sudo allta-vm vm astra-update --info-path {info_path} --rc {rc}"
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "vm_astra_update_done"}
