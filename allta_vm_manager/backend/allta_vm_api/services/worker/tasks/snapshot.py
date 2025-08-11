# services/worker/worker/tasks_snapshot.py
from typing import Iterable
from celery_app import celery_app
from utils.ssh import SimpleSSH as ssh_run

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="snapshot.create")
def snapshot_create(*, host_ip: str, username: str, password: str, vms: Iterable[str], snapshot_name: str, port: int = 22):
    parts = [f"sudo allta-vm snapshot create --snapshot-name {snapshot_name}"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "snapshot_create_done", "count": len(list(vms))}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="snapshot.delete")
def snapshot_delete(*, host_ip: str, username: str, password: str, vms: Iterable[str], snapshot_name: str, port: int = 22):
    parts = [f"sudo allta-vm snapshot delete --snapshot-name {snapshot_name}"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "snapshot_delete_done", "count": len(list(vms))}

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5, name="snapshot.revert")
def snapshot_revert(*, host_ip: str, username: str, password: str, vms: Iterable[str], snapshot_name: str, port: int = 22):
    parts = [f"sudo allta-vm snapshot revert --snapshot-name {snapshot_name}"]
    for v in vms:
        parts.append(f"--vms {v}")
    cmd = " ".join(parts)
    ssh_run(host_ip, username, password, cmd, port)
    return {"ip": host_ip, "status": "snapshot_revert_done", "count": len(list(vms))}
