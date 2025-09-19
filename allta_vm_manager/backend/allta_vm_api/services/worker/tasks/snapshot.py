# tasks/snapshot.py

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Dict, List, Any, Callable, Awaitable, Tuple

from celery import shared_task, Task
from sqlalchemy import select

from tasks.common import _require, _std_ok, _std_error
from utils.ssh import SimpleSSH
from lib.db import get_async_sessionmaker

from lib.vm import VirtualMachine
from lib.vm_snapshot import VMSnapshot


# ============ infra / base ============

class TaskFailure(Exception):
    def __init__(self, meta: dict):
        self.meta = meta
        super().__init__(json.dumps(meta, ensure_ascii=False))


class BaseTask(Task):
    throws = (TaskFailure,)


def _fail(self, envelope: dict, message: str, *, stage: str | None = None,
          command: str | None = None, rc: int | None = None,
          stdout: str | None = None, stderr: str | None = None,
          extra: dict | None = None):
    meta = _std_error(envelope, error=message)
    meta["error"]["stage"] = stage
    meta["error"]["command"] = command
    meta["error"]["exit_code"] = rc
    meta["error"]["stdout"] = stdout
    meta["error"]["stderr"] = stderr
    if extra:
        meta.setdefault("data", {}).update(extra)
    raise TaskFailure(meta)


def run_ssh(self, envelope: dict, ssh: SimpleSSH, command: str, *, stage: str) -> dict[str, Any]:
    r = ssh.run_command(command=command)
    rc = r.get("rc")
    if rc != 0:
        _fail(
            self, envelope, f"{stage} failed",
            stage=stage, command=command, rc=rc,
            stdout=r.get("stdout"), stderr=r.get("stderr"),
        )
    return r


# ============ helpers ============

async def _retry_for(predicate: Callable[[], Awaitable[bool]], *, timeout_s: int = 60, interval_s: int = 2) -> bool:
    """
    Retry predicate() every interval_s until it returns True or timeout_s passes.
    Exceptions inside predicate are ignored and retried.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while True:
        try:
            if await predicate():
                return True
        except Exception:
            pass
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval_s)


def _list_snapshots(ssh: SimpleSSH, vm_name: str) -> List[str]:
    """
    virsh --name prints one snapshot name per line
    """
    r = ssh.run_command(
        command=f"sudo virsh -c qemu:///system snapshot-list --domain '{vm_name}' --name || true"
    )
    out = (r.get("stdout") or "")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _current_snapshot_name(ssh: SimpleSSH, vm_name: str) -> Tuple[int, str]:
    """
    Return (rc, name). Uses --name to get just the name (no localization noise).
    """
    r = ssh.run_command(
        command=f"sudo virsh -c qemu:///system snapshot-current --domain '{vm_name}' --name || true"
    )
    rc = r.get("rc", 0)
    name = (r.get("stdout") or "").strip()
    return rc, name


def _ensure_same_server_in_payload(vms_names: List[str], expected_server_id: int | None,
                                   rows: List[VirtualMachine]) -> Tuple[bool, str | None]:
    if not rows:
        return False, "no VMs found in DB"
    sids = {vm.server_id for vm in rows}
    if len(sids) != 1:
        return False, f"selected VMs belong to server_ids={sorted(sids)}"
    if expected_server_id is not None and int(expected_server_id) not in sids:
        return False, f"expected server_id={expected_server_id}, got {sorted(sids)}"
    db_names = {vm.name for vm in rows}
    missing = [n for n in vms_names if n not in db_names]
    if missing:
        return False, f"VMs not found in DB: {missing}"
    return True, None


def _normalize_vm_selection(self, envelope: dict) -> Tuple[List[str], Dict[str, int]]:
    """
    Support both formats:
      - vm_names: ["ws-01", ...]
      - vms: {"ws-01": 6, ...}

    Returns:
      (names, name_to_id_map)
    """
    vm_names = envelope.get("vm_names")
    vms_map = envelope.get("vms") or {}

    if vm_names and isinstance(vm_names, list) and vm_names:
        names = list(dict.fromkeys([str(x) for x in vm_names]))  # dedup, keep order
        # if also provided vms dict, ensure consistency if possible
        if isinstance(vms_map, dict) and vms_map:
            # keep only ids for provided names
            name_to_id = {n: int(vms_map[n]) for n in names if n in vms_map}
        else:
            name_to_id = {}
        return names, name_to_id

    if isinstance(vms_map, dict) and vms_map:
        # keys are names, values are ids
        names = list(vms_map.keys())
        name_to_id = {str(k): int(v) for k, v in vms_map.items()}
        return names, name_to_id

    _fail(self, envelope, "no VMs provided (need vm_names or vms)", stage="payload-validate")
    raise RuntimeError("unreachable")


# ============ tasks ============

@shared_task(name="task_snapshot_create", bind=True, base=BaseTask)
def task_snapshot_create(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "snapshot_name"])
    server = envelope["server"]
    snapshot_name: str = envelope["snapshot_name"]

    names, provided_ids = _normalize_vm_selection(self, envelope)

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            # fetch VMs by names (to validate server_id & get ids where missing)
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            rows = res.scalars().all()
            ok, reason = _ensure_same_server_in_payload(names, server.get("id"), rows)
            if not ok:
                _fail(self, envelope, reason or "invalid VMs", stage="db-precheck")

            # final vm_id map: prefer provided_ids, fallback to DB ids
            vm_id_by_name: Dict[str, int] = {
                vm.name: provided_ids.get(vm.name, vm.id) for vm in rows
            }

            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
            # fire create
            cmd = "sudo allta-vm snapshot create " + " ".join([f"--vms {n}" for n in names]) + f" --name '{snapshot_name}'"
            run_ssh(self, envelope, ssh, cmd, stage="snapshot-create")

            want = snapshot_name.lower()
            last_seen = {n: [] for n in names}

            async def _all_present() -> bool:
                ok_local = True
                for name in names:
                    snaps = _list_snapshots(ssh, name)
                    last_seen[name] = snaps
                    if want not in {s.lower() for s in snaps}:
                        ok_local = False
                return ok_local

            if not await _retry_for(_all_present, timeout_s=60, interval_s=2):
                _fail(self, envelope, "verify failed: snapshot not found after create",
                      stage="post-check", extra={"snapshot": snapshot_name, "seen": last_seen})

            # write snapshots in DB (use vm_id from provided map if present)
            for name in names:
                vm_id = vm_id_by_name[name]
                # keep actual case as returned by virsh, if differs
                actual = next((s for s in last_seen[name] if s.lower() == want), snapshot_name)
                # ensure not duplicating existing record for this vm_id/name pair
                exists_q = await db.execute(
                    select(VMSnapshot).where(VMSnapshot.vm_id == vm_id, VMSnapshot.name == actual)
                )
                if not exists_q.scalars().first():
                    db.add(VMSnapshot(vm_id=vm_id, name=actual))

            return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}",
              stage="unexpected", stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_snapshot_delete", bind=True, base=BaseTask)
def task_snapshot_delete(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "snapshot_name"])
    server = envelope["server"]
    snapshot_name: str = envelope["snapshot_name"]

    names, provided_ids = _normalize_vm_selection(self, envelope)

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            rows = res.scalars().all()
            ok, reason = _ensure_same_server_in_payload(names, server.get("id"), rows)
            if not ok:
                _fail(self, envelope, reason or "invalid VMs", stage="db-precheck")

            vm_id_by_name: Dict[str, int] = {
                vm.name: provided_ids.get(vm.name, vm.id) for vm in rows
            }

            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
            cmd = "sudo allta-vm snapshot delete " + " ".join([f"--vms {n}" for n in names]) + f" --name '{snapshot_name}'"
            run_ssh(self, envelope, ssh, cmd, stage="snapshot-delete")

            want = snapshot_name.lower()
            last_seen = {n: [] for n in names}

            async def _all_absent() -> bool:
                ok_local = True
                for name in names:
                    snaps = _list_snapshots(ssh, name)
                    last_seen[name] = snaps
                    if want in {s.lower() for s in snaps}:
                        ok_local = False
                return ok_local

            if not await _retry_for(_all_absent, timeout_s=60, interval_s=2):
                _fail(self, envelope, "verify failed: snapshot still exists after delete",
                      stage="post-check", extra={"snapshot": snapshot_name, "seen": last_seen})

            # delete from DB (case-insensitive match)
            for name in names:
                vm_id = vm_id_by_name[name]
                # fetch all snaps for vm_id and delete those matching by lower()
                snaps_q = await db.execute(select(VMSnapshot).where(VMSnapshot.vm_id == vm_id))
                for row in snaps_q.scalars().all():
                    if row.name and row.name.lower() == want:
                        await db.delete(row)

            return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}",
              stage="unexpected", stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_snapshot_revert", bind=True, base=BaseTask)
def task_snapshot_revert(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "snapshot_name"])
    server = envelope["server"]
    snapshot_name: str = envelope["snapshot_name"]

    names, provided_ids = _normalize_vm_selection(self, envelope)

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db:
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            rows = res.scalars().all()
            ok, reason = _ensure_same_server_in_payload(names, server.get("id"), rows)
            if not ok:
                _fail(self, envelope, reason or "invalid VMs", stage="db-precheck")

            # provided_ids не обязателен для revert; БД не меняем
            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
            cmd = "sudo allta-vm snapshot revert " + " ".join([f"--vms {n}" for n in names]) + f" --name '{snapshot_name}'"
            run_ssh(self, envelope, ssh, cmd, stage="snapshot-revert")

            want = snapshot_name
            last_current = {}

            async def _all_current() -> bool:
                ok_local = True
                tmp = {}
                for name in names:
                    rc, cur = _current_snapshot_name(ssh, name)
                    tmp[name] = {"rc": rc, "current": cur}
                    if rc != 0 or cur != want:
                        ok_local = False
                nonlocal last_current
                last_current = tmp
                return ok_local

            if not await _retry_for(_all_current, timeout_s=60, interval_s=2):
                _fail(self, envelope, "verify failed: snapshot is not current after revert",
                      stage="post-check", extra={"snapshot": snapshot_name, "current": last_current})

            return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}",
              stage="unexpected", stdout=None, stderr=traceback.format_exc())
