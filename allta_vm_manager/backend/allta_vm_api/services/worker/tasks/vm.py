from __future__ import annotations

import asyncio
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any, Callable, Awaitable

from celery import shared_task, Task
from sqlalchemy import select, delete as sqla_delete

from tasks.common import log, _require, _std_ok, _std_error
from utils.ssh import SimpleSSH
from utils.crypto import Crypto
from lib.db import get_async_sessionmaker

from lib.vm import VirtualMachine
from lib.vm_snapshot import VMSnapshot


_CRYPTO = Crypto()


class TaskFailure(Exception):
    def __init__(self, meta: dict):
        self.meta = meta
        super().__init__(json.dumps(meta, ensure_ascii=False))


class BaseTask(Task):
    throws = (TaskFailure,)


# --- helpers -----------------------------------------------------------------

async def _ping(ip: str, timeout_s: int = 5) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout_s), ip,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        return (await proc.wait()) == 0
    except Exception:
        return False


def _ensure_same_server_in_payload(vms_full: Dict[str, Dict], expected: int | None) -> tuple[bool, str | None]:
    if not vms_full:
        return False, "vms_full is empty"
    sids = {int(spec.get("server_id", -1)) for spec in vms_full.values()}
    if len(sids) != 1:
        return False, f"multiple server_id in vms_full: {sorted(sids)}"
    sid = next(iter(sids))
    if expected is not None and sid != int(expected):
        return False, f"server_id mismatch: envelope.server.id={expected} vs vms_full.server_id={sid}"
    return True, None


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


def _parse_domstate_stdout(stdout: str) -> str:
    return (stdout or "").strip().lower()


def _ssh_domstate(ssh: SimpleSSH, name: str) -> str:
    r = ssh.run_command(command=f"sudo virsh -c qemu:///system domstate '{name}' || true")
    return _parse_domstate_stdout(r.get("stdout", ""))


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


def _list_snapshots(ssh: SimpleSSH, vm_name: str) -> List[str]:
    """Список имён снапшотов домена (только имена), пустой список при ошибке."""
    r = ssh.run_command(
        command=f"sudo virsh -c qemu:///system snapshot-list --domain '{vm_name}' --name || true"
    )
    out = (r.get("stdout") or "")
    return [line.strip() for line in out.splitlines() if line.strip()]


# --- generic retry ------------------------------------------------------------

async def _retry_for(predicate: Callable[[], Awaitable[bool]], *, timeout_s: int = 60, interval_s: int = 2) -> bool:
    """Ретраим predicate() каждые interval_s, пока не True или не таймаут."""
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


# --- tasks -------------------------------------------------------------------

@shared_task(name="task_vm_create", bind=True, base=BaseTask)
def task_vm_create(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vms_full", "vm_password", "json_remote_path"])
    server = envelope["server"]
    vms_full: Dict[str, Dict] = envelope["vms_full"] or {}
    json_remote_path: str = envelope["json_remote_path"]
    vm_password = envelope["vm_password"]

    ok, reason = _ensure_same_server_in_payload(vms_full, expected=server.get("id"))
    if not ok:
        _fail(self, envelope, reason or "invalid payload", stage="payload-validate")

    names = list(vms_full.keys())
    ips = [str(s["ip_bridge"]) for s in vms_full.values()]
    if len(names) != len(set(names)):
        _fail(self, envelope, "duplicate VM names in payload", stage="payload-validate")
    if len(ips) != len(set(ips)):
        _fail(self, envelope, "duplicate VM IPs in payload", stage="payload-validate")

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            if res.scalars().first():
                _fail(self, envelope, "some VM names already exist in DB", stage="db-precheck")

            res = await db.execute(select(VirtualMachine).where(VirtualMachine.ip_address.in_(ips)))
            if res.scalars().first():
                _fail(self, envelope, "some VM IPs already used in DB", stage="db-precheck")

            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

            remote_dir = os.path.dirname(json_remote_path)
            run_ssh(self, envelope, ssh, f"sudo mkdir -p {remote_dir}", stage="mkdir-json-dir")

            export_cmd = f"echo '{json.dumps(vms_full)}' | sudo tee {json_remote_path}"
            run_ssh(self, envelope, ssh, export_cmd, stage="export-json")

            create_cmd = f"cd {remote_dir} && sudo allta-vm vm create --info-path {json_remote_path} --new-password {vm_password}"
            run_ssh(self, envelope, ssh, create_cmd, stage="allta-vm-create")

            created_vm_objs: list[VirtualMachine] = []
            enc_pwd = _CRYPTO.encrypt(vm_password)
            for name, spec in vms_full.items():
                vm_obj = VirtualMachine(
                    name=name,
                    cpu=int(spec["cpu"]),
                    ram=int(spec["ram"]),
                    ip_address=str(spec["ip_bridge"]),
                    server_id=int(spec["server_id"]),
                    status="free",
                    password_enc=enc_pwd,
                )
                db.add(vm_obj)
                created_vm_objs.append(vm_obj)

            await db.flush()

            last_status: dict[str, dict] = {}

            async def _check_all_ready() -> bool:
                nonlocal last_status
                tmp: dict[str, dict] = {}
                for name in names:
                    ip = str(vms_full[name]["ip_bridge"])
                    domstate = _ssh_domstate(ssh, name)
                    ping_ok = await _ping(ip)
                    tmp[name] = {"ip": ip, "ping": ping_ok, "domstate": domstate}
                last_status = tmp
                # Пинг больше не блокирует задачу: просто фиксируем состояние.
                return True

            await _retry_for(_check_all_ready, timeout_s=60, interval_s=2)

            # Снимки: проверяем существование 1.7.5.9 и 1.8.1.6, затем пишем в БД
            base_snaps = ["1.7.5.9", "1.8.1.6"]
            base_lower = [s.lower() for s in base_snaps]
            added_snapshots: dict[str, list[str]] = {vm.name: [] for vm in created_vm_objs}

            for vm_obj in created_vm_objs:
                existing = _list_snapshots(ssh, vm_obj.name)
                existing_lower = {e.lower(): e for e in existing}
                for need_l, need in zip(base_lower, base_snaps):
                    if need_l in existing_lower:
                        real = existing_lower[need_l]
                        db.add(VMSnapshot(vm_id=vm_obj.id, name=real))
                        added_snapshots[vm_obj.name].append(real)

            await db.flush()

            out = _std_ok(envelope)
            out["data"] = {"post_check": {"ping": last_status}, "snapshots_added": added_snapshots}
            return out

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_base_create", bind=True, base=BaseTask)
def task_vm_base_create(self, envelope: dict) -> dict:
    """
    Базовое создание ВМ с пост-проверками:
      1) ping всех ВМ (ретраи 60/2);
      2) наличие снапшотов 1.7.5.9 и 1.8.1.6 у каждой ВМ (ретраи 60/2),
         затем запись этих снапшотов в БД.
    """
    _require(envelope, ["task_id", "operation", "server", "vms_full", "vm_password"])
    server = envelope["server"]
    vms_full: Dict[str, Dict] = envelope["vms_full"] or {}
    vm_password = envelope.get("vm_password")

    ok, reason = _ensure_same_server_in_payload(vms_full, expected=server.get("id"))
    if not ok:
        _fail(self, envelope, reason or "invalid payload", stage="payload-validate")

    names = list(vms_full.keys())
    ips = [str(s["ip_bridge"]) for s in vms_full.values()]
    if len(names) != len(set(names)):
        _fail(self, envelope, "duplicate VM names in payload", stage="payload-validate")
    if len(ips) != len(set(ips)):
        _fail(self, envelope, "duplicate VM IPs in payload", stage="payload-validate")

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            # DB prechecks
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            if res.scalars().first():
                _fail(self, envelope, "some VM names already exist in DB", stage="db-precheck")

            res = await db.execute(select(VirtualMachine).where(VirtualMachine.ip_address.in_(ips)))
            if res.scalars().first():
                _fail(self, envelope, "some VM IPs already used in DB", stage="db-precheck")

            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

            # Запускаем base-create
            run_ssh(self, envelope, ssh,
                    f"cd /opt/allta_vm/vm && sudo allta-vm vm base-create --new-password {vm_password}",
                    stage="allta-vm-base-create")

            # Вставляем ВМ в БД
            created_vm_objs: list[VirtualMachine] = []
            enc_pwd = _CRYPTO.encrypt(vm_password)
            for name, spec in vms_full.items():
                vm_obj = VirtualMachine(
                    name=name,
                    cpu=int(spec["cpu"]),
                    ram=int(spec["ram"]),
                    ip_address=str(spec["ip_bridge"]),
                    server_id=int(spec["server_id"]),
                    status="free",
                    password_enc=enc_pwd,
                )
                db.add(vm_obj)
                created_vm_objs.append(vm_obj)

            await db.flush()

            # (1) post-check: ping + домстейт (для информации), ретраи 60/2
            last_status: dict[str, dict] = {}

            async def _check_all_ready() -> bool:
                nonlocal last_status
                tmp: dict[str, dict] = {}
                for name in names:
                    ip = str(vms_full[name]["ip_bridge"])
                    domstate = _ssh_domstate(ssh, name)
                    ping_ok = await _ping(ip)
                    tmp[name] = {"ip": ip, "ping": ping_ok, "domstate": domstate}
                last_status = tmp
                # Пинг больше не блокирует задачу: просто фиксируем состояние.
                return True

            await _retry_for(_check_all_ready, timeout_s=60, interval_s=2)

            # (2) post-check: наличие снапшотов 1.7.5.9 и 1.8.1.6 у КАЖДОЙ ВМ, ретраи 60/2
            base_snaps = ["1.7.5.9", "1.8.1.6"]
            base_lower = [s.lower() for s in base_snaps]
            missing_by_vm: dict[str, List[str]] = {}

            async def _all_snapshots_present() -> bool:
                nonlocal missing_by_vm
                all_ok = True
                tmp_missing: dict[str, List[str]] = {}
                for vm_obj in created_vm_objs:
                    existing = _list_snapshots(ssh, vm_obj.name)
                    existing_lower = {e.lower(): e for e in existing}
                    missing = [orig for orig, low in zip(base_snaps, base_lower) if low not in existing_lower]
                    if missing:
                        all_ok = False
                        tmp_missing[vm_obj.name] = missing
                missing_by_vm = tmp_missing
                return all_ok

            if not await _retry_for(_all_snapshots_present, timeout_s=60, interval_s=2):
                _fail(self, envelope, "post-check failed (snapshots)", stage="post-check",
                      extra={"missing_snapshots": missing_by_vm})

            # Если всё ок — сохраняем реальные имена снапшотов в БД
            added_snapshots: dict[str, list[str]] = {vm.name: [] for vm in created_vm_objs}
            for vm_obj in created_vm_objs:
                existing = _list_snapshots(ssh, vm_obj.name)
                existing_lower = {e.lower(): e for e in existing}
                for need_l, need in zip(base_lower, base_snaps):
                    real = existing_lower.get(need_l)
                    if real:
                        db.add(VMSnapshot(vm_id=vm_obj.id, name=real))
                        added_snapshots[vm_obj.name].append(real)

            await db.flush()

            out = _std_ok(envelope)
            out["data"] = {
                "post_check": {"ping": last_status},
                "snapshots_added": added_snapshots
            }
            return out

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_update", bind=True, base=BaseTask)
def task_vm_update(self, envelope: dict) -> dict:
    """
    Обновление CPU/RAM + пост-проверка версии /etc/astra/build_version на каждой ВМ (ретраи 60/2).
    Если передан envelope['rc'] — сверяем строго.
    """
    _require(envelope, ["task_id", "operation", "server", "vms_full", "json_remote_path"])
    server = envelope["server"]
    vms_full: Dict[str, Dict] = envelope["vms_full"] or {}
    json_remote_path: str = envelope["json_remote_path"]
    expected_rc: str | None = envelope.get("rc")
    names = list(vms_full.keys())

    ok, reason = _ensure_same_server_in_payload(vms_full, expected=server.get("id"))
    if not ok:
        _fail(self, envelope, reason or "invalid payload", stage="payload-validate")

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
            rows = res.scalars().all()
            if not rows:
                _fail(self, envelope, "no VMs found to update", stage="db-precheck")
            sids = {vm.server_id for vm in rows}
            if len(sids) != 1 or int(server.get("id")) not in sids:
                _fail(self, envelope, f"selected VMs belong to server_ids={sorted(sids)}, expected={server.get('id')}",
                      stage="db-precheck")

            ssh_srv = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

            remote_dir = os.path.dirname(json_remote_path)
            run_ssh(self, envelope, ssh_srv, f"sudo mkdir -p {remote_dir}", stage="mkdir-json-dir")

            export_cmd = f"echo '{json.dumps(vms_full)}' | sudo tee {json_remote_path}"
            run_ssh(self, envelope, ssh_srv, export_cmd, stage="export-json")

            update_cmd = f"cd {remote_dir} && sudo allta-vm vm update --info-path {json_remote_path}"
            run_ssh(self, envelope, ssh_srv, update_cmd, stage="allta-vm-update")

            found = {vm.name: vm for vm in rows}
            updated = 0
            for name, spec in vms_full.items():
                vm = found.get(name)
                if not vm:
                    continue
                if spec.get("cpu") is not None:
                    vm.cpu = int(spec["cpu"])
                if spec.get("ram") is not None:
                    vm.ram = int(spec["ram"])
                updated += 1

            # пост-проверка версии внутри каждой ВМ
            last_versions: dict[str, dict] = {}

            async def _versions_ok() -> bool:
                nonlocal last_versions
                ok_all = True
                tmp: dict[str, dict] = {}
                for vm in rows:
                    name = vm.name
                    ip = str(vms_full.get(name, {}).get("ip_bridge") or vm.ip_address or "")
                    if not ip:
                        tmp[name] = {"ip": "", "version": "", "error": "no ip"}
                        ok_all = False
                        continue
                    try:
                        pwd = _CRYPTO.decrypt(vm.password_enc) if vm.password_enc else None
                        if not pwd:
                            tmp[name] = {"ip": ip, "version": "", "error": "no password"}
                            ok_all = False
                            continue
                        ssh_vm = SimpleSSH(host=ip, username="u", password=pwd)
                        r = ssh_vm.run_command("cat /etc/astra/build_version || true")
                        version = (r.get("stdout") or "").strip()
                        tmp[name] = {"ip": ip, "version": version}
                        if expected_rc and version != expected_rc:
                            ok_all = False
                    except Exception as e:
                        tmp[name] = {"ip": ip, "version": "", "error": str(e)}
                        ok_all = False
                last_versions = tmp
                return ok_all

            if not await _retry_for(_versions_ok, timeout_s=60, interval_s=2):
                extra = {"versions": last_versions}
                if expected_rc:
                    extra["expected"] = expected_rc
                _fail(self, envelope, "post-update check failed (build_version)", stage="post-check",
                      extra=extra)

            out = _std_ok(envelope)
            out["data"] = {"updated": updated, "affected": names, "versions": last_versions,
                           **({"expected": expected_rc} if expected_rc else {})}
            return out

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_delete", bind=True, base=BaseTask)
def task_vm_delete(self, envelope: dict) -> dict:
    """Удаляем ВМ -> ждём, пока их нет в `virsh list --all --name` -> чистим БД."""
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server = envelope["server"]
    vm_names: List[str] = envelope["vm_names"] or []

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db, db.begin():
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(vm_names)))
            rows = res.scalars().all()
            if not rows:
                _fail(self, envelope, "no VMs found to delete", stage="db-precheck")
            sids = {vm.server_id for vm in rows}
            if len(sids) != 1 or int(server.get("id")) not in sids:
                _fail(self, envelope, f"selected VMs belong to server_ids={sorted(sids)}, expected={server.get('id')}",
                      stage="db-precheck")

            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
            cmd = "sudo allta-vm vm delete " + " ".join([f"--vms {n}" for n in vm_names])
            run_ssh(self, envelope, ssh, cmd, stage="allta-vm-delete")

            last_list: List[str] = []

            async def _all_absent_in_virsh() -> bool:
                nonlocal last_list
                r = ssh.run_command("sudo virsh -c qemu:///system list --all --name || true")
                names = [x.strip() for x in (r.get("stdout") or "").splitlines() if x.strip()]
                last_list = names
                return all(n not in names for n in vm_names)

            if not await _retry_for(_all_absent_in_virsh, timeout_s=60, interval_s=2):
                _fail(self, envelope, "post-delete check failed (virsh list)", stage="post-check",
                      extra={"still_in_virsh": [n for n in vm_names if n in last_list]})

            vm_ids = [vm.id for vm in rows]
            await db.execute(sqla_delete(VMSnapshot).where(VMSnapshot.vm_id.in_(vm_ids)))  # type: ignore
            for vm in rows:
                await db.delete(vm)

            return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_start", bind=True, base=BaseTask)
def task_vm_start(self, envelope: dict) -> dict:
    """Старт ВМ -> проверка ping с ретраями (90с/2с)."""
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server = envelope["server"]
    vm_names: List[str] = envelope["vm_names"] or []

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db:
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(vm_names)))
            rows = res.scalars().all()
            if not rows:
                _fail(self, envelope, "no VMs found to start", stage="db-precheck")
            sids = {vm.server_id for vm in rows}
            if len(sids) != 1 or int(server.get("id")) not in sids:
                _fail(self, envelope, f"selected VMs belong to server_ids={sorted(sids)}, expected={server.get('id')}",
                      stage="db-precheck")

        ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
        cmd = "sudo allta-vm vm start " + " ".join([f"--vms {n}" for n in vm_names])
        run_ssh(self, envelope, ssh, cmd, stage="allta-vm-start")

        ip_map = {vm.name: str(vm.ip_address) for vm in rows}
        last_ping: dict[str, dict] = {}

        async def _check_all_ping() -> bool:
            nonlocal last_ping
            tmp = {}
            for name, ip in ip_map.items():
                alive = await _ping(str(ip))
                tmp[name] = {"ip": str(ip), "ping": alive}
            last_ping = tmp
            # Пинг больше не блокирует выполнение; просто логируем состояние.
            return True

        await _retry_for(_check_all_ping, timeout_s=90, interval_s=2)

        return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_stop", bind=True, base=BaseTask)
def task_vm_stop(self, envelope: dict) -> dict:
    """Стоп ВМ -> проверка domstate (не работает/not running) с ретраями 60/2."""
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server = envelope["server"]
    vm_names: List[str] = envelope["vm_names"] or []

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as db:
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(vm_names)))
            rows = res.scalars().all()
            if not rows:
                _fail(self, envelope, "no VMs found to stop", stage="db-precheck")
            sids = {vm.server_id for vm in rows}
            if len(sids) != 1 or int(server.get("id")) not in sids:
                _fail(self, envelope, f"selected VMs belong to server_ids={sorted(sids)}, expected={server.get('id')}",
                      stage="db-precheck")

        ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
        cmd = "sudo allta-vm vm stop " + " ".join([f"--vms {n}" for n in vm_names])
        run_ssh(self, envelope, ssh, cmd, stage="allta-vm-stop")

        last_states: dict[str, str] = {}

        async def _check_stopped() -> bool:
            nonlocal last_states
            ok = True
            tmp: dict[str, str] = {}
            for name in vm_names:
                st = _ssh_domstate(ssh, name)
                tmp[name] = st
                if ("running" in st) or ("работает" in st):
                    ok = False
            last_states = tmp
            return ok

        if not await _retry_for(_check_stopped, timeout_s=60, interval_s=2):
            still_running = [{"name": n, "state": s} for n, s in last_states.items()
                             if ("running" in s) or ("работает" in s)]
            _fail(self, envelope, "post-stop check failed (domstate)", stage="post-check",
                  extra={"still_running": still_running})

        return _std_ok(envelope)

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}", stage="unexpected",
              stdout=None, stderr=traceback.format_exc())


@shared_task(name="task_vm_astra_update", bind=True, base=BaseTask)
def task_vm_astra_update(self, envelope: dict) -> dict:
    """
    Astra-update с группировкой ВМ по паролям (пароли из БД, расшифровка).
    Для каждой группы:
      - формируем subset vms_full -> group_json в remote_dir;
      - sudo bash -lc "cd remote_dir && allta-vm vm astra-update --info-path group_json --rc rc --new-password 'pwd'".
    Параллельность групп — max_parallel_groups (по умолчанию 3).

    После успешного CLI: создаём по записи VMSnapshot(name=rc) для каждой ВМ.
    Если у ХОТЯ БЫ ОДНОЙ ВМ уже есть такой снапшот — ошибка (precheck).
    """
    _require(envelope, ["task_id", "operation", "server", "vm_names", "rc", "vms_full", "json_remote_path"])
    server = envelope["server"]
    task_id: str = envelope["task_id"]
    vm_names: List[str] = envelope["vm_names"] or []
    rc: str = envelope["rc"]
    vms_full: Dict[str, Dict] = envelope["vms_full"] or {}
    json_remote_path: str = envelope["json_remote_path"]
    max_parallel = int(envelope.get("max_parallel_groups", 3) or 3)
    if max_parallel < 1:
        max_parallel = 1

    ok, reason = _ensure_same_server_in_payload(vms_full, expected=server.get("id"))
    if not ok:
        _fail(self, envelope, reason or "invalid payload", stage="payload-validate")

    async def _run() -> dict:
        SessionLocal = get_async_sessionmaker()

        # ---------- СЕССИЯ 1: предчеки + запуск CLI ----------
        async with SessionLocal() as db:
            # ВМ в БД и принадлежность серверу
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(vm_names)))
            rows = res.scalars().all()
            if len(rows) != len(vm_names):
                found = {vm.name for vm in rows}
                missing = [n for n in vm_names if n not in found]
                _fail(self, envelope, f"some VMs not found in DB: {missing}",
                      stage="db-precheck", extra={"missing": missing})
            sids = {vm.server_id for vm in rows}
            if len(sids) != 1 or int(server.get("id")) not in sids:
                _fail(self, envelope,
                      f"selected VMs belong to server_ids={sorted(sids)}, expected={server.get('id')}",
                      stage="db-precheck")

            # Precheck: снапшот rc не должен существовать ни у одной из целевых ВМ
            target_vm_ids = [vm.id for vm in rows]
            res_exist = await db.execute(
                select(VMSnapshot.vm_id).where(
                    VMSnapshot.name == rc,
                    VMSnapshot.vm_id.in_(target_vm_ids)
                )
            )
            existing_ids = set(res_exist.scalars().all())
            if existing_ids:
                by_id = {vm.id: vm.name for vm in rows}
                conflict_vms = [by_id[vid] for vid in existing_ids if vid in by_id]
                _fail(self, envelope,
                      f"snapshot '{rc}' already exists for some VMs",
                      stage="snapshot-precheck",
                      extra={"rc": rc, "conflict_vms": conflict_vms})

            # Группировка по паролям
            groups: Dict[str, List[VirtualMachine]] = {}
            pw_errors: List[str] = []
            for vm in rows:
                try:
                    pwd = _CRYPTO.decrypt(vm.password_enc) if vm.password_enc else ""
                except Exception as e:
                    pw_errors.append(f"{vm.name}: decrypt_err={e}")
                    pwd = ""
                if not pwd:
                    pw_errors.append(f"{vm.name}: empty password")
                    continue
                groups.setdefault(pwd, []).append(vm)
            if pw_errors:
                _fail(self, envelope, "password decrypt/empty errors",
                      stage="payload-validate", extra={"details": pw_errors})

            # SSH + директория
            ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])
            remote_dir = os.path.dirname(json_remote_path) or f"/opt/allta_vm/jobs/{task_id}"
            run_ssh(self, envelope, ssh,
                    f"sudo mkdir -p '{remote_dir}' && sudo chmod 0755 '{remote_dir}'",
                    stage="mkdir-json-dir")

            # Параллельный запуск по группам
            loop = asyncio.get_running_loop()
            executor = ThreadPoolExecutor(max_workers=max_parallel)
            sem = asyncio.Semaphore(max_parallel)

            async def _export_and_update(pwd: str, group_idx: int, vms: List[VirtualMachine]) -> dict:
                async with sem:
                    sub_names = [vm.name for vm in vms]
                    sub_full = {n: vms_full[n] for n in sub_names}
                    group_json = os.path.join(remote_dir, f"{task_id}__g{group_idx}.json")
                    payload = json.dumps(sub_full)

                    def _do_export_and_update():
                        export_cmd = f"echo '{payload}' | sudo tee '{group_json}'"
                        run_ssh(self, envelope, ssh, export_cmd, stage=f"export-json[g{group_idx}]")
                        upd_cmd = (
                            "sudo bash -lc "
                            f"\"cd '{remote_dir}' && allta-vm vm astra-update "
                            f"--info-path '{group_json}' --rc {rc} --new-password '{pwd}'\""
                        )
                        run_ssh(self, envelope, ssh, upd_cmd, stage=f"astra-update[g{group_idx}]")
                        return {"ok": True, "group_index": group_idx, "vms": sub_names, "json": group_json}

                    try:
                        return await loop.run_in_executor(executor, _do_export_and_update)
                    except TaskFailure as tf:
                        return {"ok": False, "group_index": group_idx, "vms": sub_names, "error_meta": tf.meta}
                    except Exception as e:
                        return {"ok": False, "group_index": group_idx, "vms": sub_names, "error": str(e)}

            tasks = [_export_and_update(pwd, idx, vms)
                     for idx, (pwd, vms) in enumerate(groups.items(), start=1)]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=False)
            finally:
                executor.shutdown(wait=True)

            errors, ok_groups = [], []
            for r in results:
                if r.get("ok"):
                    ok_groups.append({"group_index": r["group_index"], "vms": r["vms"], "json": r["json"]})
                else:
                    errors.append(r)

            if errors:
                _fail(self, envelope, "astra-update failed for some password groups",
                      stage="allta-vm-astra-update",
                      extra={"errors": errors, "ok_groups": ok_groups, "max_parallel_groups": max_parallel})

            # Сохраняем список имён ВМ для записи снапшотов во 2-й сессии
            vm_names_ok = [vm.name for vm in rows]

        # ---------- СЕССИЯ 2: запись снапшотов с ТРАНЗАКЦИЕЙ ----------
        async with SessionLocal() as db, db.begin():
            # Берём свежие VM из БД (на случай изменений) по именам
            res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(vm_names_ok)))
            rows2 = res.scalars().all()
            ids2 = [vm.id for vm in rows2]

            # Повторный быстрый precheck (защита от гонки)
            re_exist = await db.execute(
                select(VMSnapshot.vm_id).where(
                    VMSnapshot.name == rc,
                    VMSnapshot.vm_id.in_(ids2)
                )
            )
            again = set(re_exist.scalars().all())
            if again:
                by_id2 = {vm.id: vm.name for vm in rows2}
                conflict2 = [by_id2[i] for i in again if i in by_id2]
                _fail(self, envelope,
                      f"snapshot '{rc}' already exists for some VMs (race)",
                      stage="snapshot-precheck-2",
                      extra={"rc": rc, "conflict_vms": conflict2})

            # Вставляем и КОММИТИМ
            added = []
            for vm in rows2:
                db.add(VMSnapshot(vm_id=vm.id, name=rc))
                added.append(vm.name)
            # db.begin() обеспечит commit при выходе

        out = _std_ok(envelope)
        out["data"] = {
            "total_groups": len(groups),
            "max_parallel_groups": max_parallel,
            "groups_ok": ok_groups,
            "snapshots_db": {"name": rc, "added_for": added},
        }
        return out

    try:
        return asyncio.run(_run())
    except TaskFailure:
        raise
    except Exception as e:
        _fail(self, envelope, f"unexpected error: {e.__class__.__name__}",
              stage="unexpected", stdout=None, stderr=traceback.format_exc())
