"""Тесты worker-тасок VM-менеджера волны 2: диски + update + box_url-фолбэк.

SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по подстроке
команды) — как в `test_vms_tasks.py`. Ассертим последовательности `ssh.run`,
выбор свободного virtio-таргета и internal-callback'и в server_service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.clients.ssh import SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vm_prepare_helpers, _vms_helpers, vms, vms_disks

_MGMT = {
    "management_user": "dbos",
    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc123 dbos@vm",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
    "password": "S3cretPass",
}


@pytest.fixture
def stub_mgmt(monkeypatch):
    """Замокать чтение mgmt-материала из stash'а (managed-путь по ключу)."""
    async def _read_mgmt(stash_key):  # noqa: ARG001
        return dict(_MGMT)
    monkeypatch.setattr(_vm_prepare_helpers, "_read_mgmt_install", _read_mgmt)


# ── SSH mock ─────────────────────────────────────────────────────────────────


class _FakeSshClient:
    """Мок SshClient: пишет (command, stdin, sudo), отдаёт заданный ответ по
    подстроке команды. Дефолт — (0, "", "") (команда «прошла»)."""

    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []
        self.stdins: list[str | None] = []
        self.calls: list[tuple[str, bool]] = []

    def set_response(self, pat: str, rc: int, stdout: str = "", stderr: str = ""):
        self._responses.append((pat, (rc, stdout, stderr)))

    async def connect(self):
        return None

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.commands.append(command)
        self.stdins.append(stdin_payload)
        self.calls.append((command, sudo))
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")

    def idx(self, needle: str) -> int:
        """Индекс первой команды, содержащей подстроку (или -1)."""
        for i, c in enumerate(self.commands):
            if needle in c:
                return i
        return -1


def _assert_root_libvirt(calls: list[tuple[str, bool]], needle: str) -> None:
    """Команда с подстрокой `needle` идёт в system-libvirt под root (sudo)."""
    matched = [(cmd, sudo) for cmd, sudo in calls if needle in cmd]
    assert matched, f"команда {needle!r} не найдена"
    for cmd, sudo in matched:
        assert "LC_ALL=C LIBGUESTFS_BACKEND=direct" in cmd, cmd
        assert sudo is True, cmd


async def _set_single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()


@pytest.fixture
def stub_disks(monkeypatch):
    """Замокать open_hub_session и disk/vm callback'и в `vms_disks`."""
    holder: dict = {"ssh": None}
    calls: dict = {"disk_state": [], "vm_state": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    async def _disk_state(vm_id, disk_id, state, target_department_id=None, **kw):
        calls["disk_state"].append(
            {"vm_id": vm_id, "disk_id": disk_id, "state": state,
             "target_department_id": target_department_id, **kw},
        )
        return {"ok": True}

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append(
            {"vm_id": vm_id, "target_department_id": target_department_id, **kw},
        )
        return {"ok": True}

    monkeypatch.setattr(vms_disks, "open_hub_session", _open)
    monkeypatch.setattr(vms_disks.server_service_client, "submit_vm_disk_state", _disk_state)
    monkeypatch.setattr(vms_disks.server_service_client, "submit_vm_state", _vm_state)
    return {"holder": holder, "calls": calls}


@pytest.fixture
def stub_vms(monkeypatch):
    """Замокать open_hub_session и vm callback в `vms` (для create-фолбэка)."""
    holder: dict = {"ssh": None}
    calls: dict = {"vm_state": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append({"vm_id": vm_id, **kw})
        return {"ok": True}

    async def _snapshots(vm_id, snapshots, target_department_id=None, **kw):
        calls.setdefault("snapshots", []).append({"vm_id": vm_id, "snapshots": snapshots, **kw})
        return {"ok": True}

    monkeypatch.setattr(vms, "open_hub_session", _open)
    monkeypatch.setattr(vms.server_service_client, "submit_vm_state", _vm_state)
    monkeypatch.setattr(vms.server_service_client, "submit_vm_snapshots", _snapshots)
    return {"holder": holder, "calls": calls}


# ── чистые хелперы ───────────────────────────────────────────────────────────


class TestDiskHelpers:
    def test_next_target_dev_picks_free_slot(self):
        blk = (
            " Type   Device   Target   Source\n"
            "-----------------------------------\n"
            " file   disk     vda      /vms/station-a.qcow2\n"
            " file   disk     vdb      /vms/additional_disk/x.qcow2\n"
        )
        assert _vms_helpers.next_target_dev(blk, "h") == "vdc"
        assert _vms_helpers.next_target_dev("no disks", "h") == "vda"

    def test_validate_target_dev(self):
        assert _vms_helpers.validate_target_dev("vdb", "h") == "vdb"
        for bad in ("sda", "vda1", "vd", "vd;rm", "hda"):
            with pytest.raises(SshError) as e:
                _vms_helpers.validate_target_dev(bad, "h")
            assert e.value.error_code == "VM_INVALID_ARG"

    def test_box_url_from_catalog_shapes(self):
        section = '{"libvirt_box": {"xfs.box": "ftp://x/boxes/xfs.box.tar.gz"}}'
        assert _vms_helpers.box_url_from_catalog(section, "xfs.box") == "ftp://x/boxes/xfs.box.tar.gz"
        obj = '{"libvirt_box": {"vm_station": {"url": "ftp://x/vm_station.tar.gz"}}}'
        assert _vms_helpers.box_url_from_catalog(obj, "vm_station") == "ftp://x/vm_station.tar.gz"
        top = '{"foo": "ftp://x/foo.tar.gz"}'
        assert _vms_helpers.box_url_from_catalog(top, "foo") == "ftp://x/foo.tar.gz"
        assert _vms_helpers.box_url_from_catalog("not json", "foo") is None
        assert _vms_helpers.box_url_from_catalog(section, "missing") is None

    async def test_resolve_box_url_wgets_catalog(self):
        fake = _FakeSshClient()
        fake.set_response(
            "wget -qO-", 0,
            '{"libvirt_box": {"xfs.box": "ftp://10.177.103.10/boxes/xfs.box.tar.gz"}}',
        )
        url = await _vms_helpers.resolve_box_url(fake, "xfs.box")
        assert url == "ftp://10.177.103.10/boxes/xfs.box.tar.gz"
        assert any("wget -qO-" in c and "test-box-config.json" in c for c in fake.commands)


# ── vm.disk_attach ───────────────────────────────────────────────────────────


class TestDiskAttach:
    async def test_attach_with_mkfs_and_mount(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh pool-info additional", 1)  # пула нет → создаём
        fake.set_response("virsh domblklist", 0, " file disk vda /vms/station-a.qcow2")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk1", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "disk_name": "data", "size_gb": 20,
            "fs": "ext4", "mount": "/data", "ip_address": "10.177.103.101",
            "storage_pool_path": "/vms", "is_managed": True,
            "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.disk_attach", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_attach.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("pool-define-as additional dir --target /vms/additional_disk" in c for c in cmds)
        assert any("qemu-img create -f qcow2 /vms/additional_disk/station-a_data.qcow2 20G" in c for c in cmds)
        # qemu-img/domblklist/attach-disk — system-libvirt, под root
        _assert_root_libvirt(fake.calls, "qemu-img create -f qcow2 /vms/additional_disk/station-a_data.qcow2")
        _assert_root_libvirt(fake.calls, "virsh domblklist station-a --details")
        _assert_root_libvirt(fake.calls, "virsh attach-disk station-a")
        attach = next(c for c in cmds if "virsh attach-disk station-a" in c)
        assert "/vms/additional_disk/station-a_data.qcow2 vdb" in attach
        assert "--persistent --targetbus virtio --serial station-a_data --subdriver qcow2" in attach
        assert any("mkfs.ext4 -F /dev/disk/by-id/virtio-station-a_data" in c for c in cmds)
        assert any("/etc/fstab" in c and "/data" in c for c in cmds)
        # callback ready + атрибуты диска
        state = stub_disks["calls"]["disk_state"][0]
        assert state == {
            "vm_id": "vm1", "disk_id": "dsk1", "state": "ready",
            "target_department_id": "dep1", "target_dev": "vdb",
            "path": "/vms/additional_disk/station-a_data.qcow2",
            "serial": "station-a_data", "size_gb": 20,
        }

    async def test_attach_without_fs_skips_guest(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh pool-info additional", 0)  # пул уже есть
        fake.set_response("virsh domblklist", 0, " file disk vda /vms/x.qcow2")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk2", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "disk_name": "extra", "size_gb": 5,
            "is_managed": True,
        }
        tid = await make_task(task_kind="vm.disk_attach", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_attach.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert not any("mkfs" in c for c in fake.commands)
        assert not any("pool-define-as additional" in c for c in fake.commands)  # пул был
        assert stub_disks["calls"]["disk_state"][0]["state"] == "ready"

    async def test_attach_failure_reports_error(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh pool-info additional", 0)
        fake.set_response("virsh domblklist", 0, " file disk vda /vms/x.qcow2")
        fake.set_response("virsh attach-disk", 1, stderr="boom")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk3", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "disk_name": "d", "size_gb": 5, "is_managed": True,
        }
        tid = await make_task(task_kind="vm.disk_attach", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_disks.vm_disk_attach.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_DISK_ATTACH_FAILED" in t.last_error
        assert stub_disks["calls"]["disk_state"][0]["state"] == "error"


class TestDiskAttachManagedKey:
    """Managed-ВМ (`creds_stash_key`): mkfs/монтирование в госте идут по ключу
    управляющего пользователя, временный ключ пишется и затирается на hub'е."""

    async def test_mkfs_and_mount_use_key(
        self, make_task, fetch_task, captured_audit, stub_disks, stub_mgmt,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh pool-info additional", 0)
        fake.set_response("virsh domblklist", 0, " file disk vda /vms/station-a.qcow2")
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk1", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "disk_name": "data", "size_gb": 20,
            "fs": "ext4", "mount": "/data", "ip_address": "10.177.103.101",
            "storage_pool_path": "/vms", "is_managed": True,
            "target_department_id": "dep1",
            "creds_stash_key": "dbos:dispatch_creds:abc",
        }
        tid = await make_task(task_kind="vm.disk_attach", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_attach.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any(
            "mkfs.ext4 -F /dev/disk/by-id/virtio-station-a_data" in c
            and "ssh -i /tmp/dbos-key" in c and "dbos@10.177.103.101" in c
            for c in cmds
        )
        assert any("/etc/fstab" in c and "ssh -i /tmp/dbos-key" in c for c in cmds)
        assert not any("sshpass" in c for c in cmds)
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)


# ── vm.disk_delete ───────────────────────────────────────────────────────────


class TestDiskDelete:
    async def test_detach_and_rm(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk1", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "target_dev": "vdb",
            "path": "/vms/additional_disk/station-a_data.qcow2", "is_managed": True,
            "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.disk_delete", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_delete.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any("virsh detach-disk station-a vdb --persistent" in c for c in cmds)
        assert any("rm -f /vms/additional_disk/station-a_data.qcow2" in c for c in cmds)
        # detach — под root; rm qcow2 — root-owned пул, под sudo
        _assert_root_libvirt(fake.calls, "virsh detach-disk station-a vdb --persistent")
        assert ("rm -f /vms/additional_disk/station-a_data.qcow2", True) in fake.calls
        state = stub_disks["calls"]["disk_state"][0]
        assert state == {
            "vm_id": "vm1", "disk_id": "dsk1", "state": "deleted",
            "target_department_id": "dep1",
        }


# ── vm.disk_resize ───────────────────────────────────────────────────────────


class TestDiskResize:
    async def test_resize_stop_grow_start(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk1", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "path": "/vms/station-a.qcow2",
            "size_gb": 40, "target_dev": "vda", "ip_address": "10.177.103.101",
            "is_managed": True,
        }
        tid = await make_task(task_kind="vm.disk_resize", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_resize.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # порядок: destroy → qemu-img resize → start → growpart/resize2fs (в госте)
        i_stop = fake.idx("virsh destroy station-a")
        i_resize = fake.idx("qemu-img resize /vms/station-a.qcow2 40G")
        i_start = fake.idx("virsh start station-a")
        i_grow = fake.idx("growpart /dev/vda 1")
        assert -1 < i_stop < i_resize < i_start < i_grow
        assert any("resize2fs /dev/vda1" in c for c in fake.commands)
        # power/qemu-img операции ресайза — под root
        _assert_root_libvirt(fake.calls, "virsh destroy station-a")
        _assert_root_libvirt(fake.calls, "qemu-img resize /vms/station-a.qcow2 40G")
        _assert_root_libvirt(fake.calls, "virsh start station-a")
        _assert_root_libvirt(fake.calls, "virsh domstate station-a")
        state = stub_disks["calls"]["disk_state"][0]
        assert state["state"] == "ready" and state["size_gb"] == 40


class TestDiskResizeManagedKey:
    """Managed-ВМ: growpart/resize2fs идут в госте по управляющему ключу."""

    async def test_growpart_uses_key(
        self, make_task, fetch_task, captured_audit, stub_disks, stub_mgmt,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "disk_id": "dsk1", "hub_host": "10.0.0.7",
            "vm_name": "station-a", "path": "/vms/station-a.qcow2",
            "size_gb": 40, "target_dev": "vda", "ip_address": "10.177.103.101",
            "is_managed": True, "creds_stash_key": "dbos:dispatch_creds:abc",
        }
        tid = await make_task(task_kind="vm.disk_resize", target_server_id="hub1", payload=payload)
        await vms_disks.vm_disk_resize.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any(
            "growpart /dev/vda 1" in c and "ssh -i /tmp/dbos-key" in c
            and "dbos@10.177.103.101" in c for c in cmds
        )
        assert not any("sshpass" in c for c in cmds)
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)


# ── vm.update ────────────────────────────────────────────────────────────────


class TestVmUpdate:
    async def test_update_cpu_and_ram(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "cpu": 8, "ram_mb": 16384, "is_managed": True,
            "target_department_id": "dep1",
        }
        tid = await make_task(task_kind="vm.update", target_server_id="hub1", payload=payload)
        await vms_disks.vm_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # running → stop перед правкой XML
        assert any("virsh destroy station-a" in c for c in cmds)
        assert any("virsh dumpxml station-a > /tmp/station-a.xml" in c for c in cmds)
        assert any("<vcpu>8</vcpu>" in c for c in cmds)
        assert any("<memory>16777216</memory>" in c for c in cmds)  # 16384*1024
        assert any("<currentMemory>16777216</currentMemory>" in c for c in cmds)
        assert any("virsh define /tmp/station-a.xml" in c for c in cmds)
        assert any("virsh start station-a" in c for c in cmds)
        # dumpxml→define→start и domstate — под root (system-libvirt)
        _assert_root_libvirt(fake.calls, "virsh dumpxml station-a > /tmp/station-a.xml")
        _assert_root_libvirt(fake.calls, "virsh define /tmp/station-a.xml")
        _assert_root_libvirt(fake.calls, "virsh domstate station-a")
        state = stub_disks["calls"]["vm_state"][0]
        assert state["power_state"] == "on"
        assert state["clear_busy_state"] is True

    async def test_update_cpu_only_skips_memory(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_disks["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
            "cpu": 4, "is_managed": True,
        }
        tid = await make_task(task_kind="vm.update", target_server_id="hub1", payload=payload)
        await vms_disks.vm_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("<vcpu>4</vcpu>" in c for c in fake.commands)
        assert not any("<memory>" in c for c in fake.commands)
        assert stub_disks["calls"]["vm_state"][0]["clear_busy_state"] is True

    async def test_update_requires_cpu_or_ram(self, make_task, fetch_task, captured_audit, stub_disks):
        fake = _FakeSshClient()
        stub_disks["holder"]["ssh"] = fake
        payload = {"vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "s", "is_managed": True}
        tid = await make_task(task_kind="vm.update", target_server_id="hub1", payload=payload)
        await _set_single_attempt(tid)
        await vms_disks.vm_update.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error


# ── box_url-фолбэк в vm.create ───────────────────────────────────────────────


class TestCreateBoxUrlFallback:
    async def test_missing_box_url_resolved_from_catalog(self, make_task, fetch_task, captured_audit, stub_vms):
        fake = _FakeSshClient()
        fake.set_response("test -f", 1)  # бокса нет в пуле
        fake.set_response(
            "wget -qO-", 0,
            '{"libvirt_box": {"xfs.box": "ftp://10.177.103.10/boxes/xfs.box.tar.gz"}}',
        )
        fake.set_response("virsh domifaddr", 0, " vnet0 52:54:00:aa:bb:cc ipv4 192.168.100.24/24")
        fake.set_response("virsh domstate", 0, "running")
        # nat-статику льём в диск offline — мокаем virt-customize и mktemp
        fake.set_response("command -v virt-customize", 0)
        fake.set_response("mktemp", 0, "/tmp/dbos-if")
        stub_vms["holder"]["ssh"] = fake
        payload = {
            "vm_id": "vm5", "hub_host": "10.0.0.7", "name": "xfs-1",
            "cpu": 4, "ram_mb": 4096, "disk_gb": 0, "box": "xfs.box",
            "network_mode": "nat", "ip_address": None, "os_versions": [],
            "storage_pool_path": "/vms", "is_managed": True,
        }
        tid = await make_task(task_kind="vm.create", target_server_id="hub1", payload=payload)
        await vms.vm_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # каталог дёрнут, url резолвлен и скачан
        assert any("wget -qO-" in c and "test-box-config.json" in c for c in cmds)
        assert any("wget -q -O /vms/xfs.box.tar.gz ftp://10.177.103.10/boxes/xfs.box.tar.gz" in c for c in cmds)
        assert any("tar xzf /vms/xfs.box.tar.gz" in c for c in cmds)
