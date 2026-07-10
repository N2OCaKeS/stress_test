"""Тесты worker-тасок VM-снимков: snapshot create/delete/revert, astra_update,
allta_update/passwd (reroll).

SSH мокается `_FakeSshClient` (дефолт rc=0, точечные ответы по подстроке
команды) — как в `test_vms_tasks`. Ассертим последовательности `ssh.run`,
пропуск `_build`-снимков в reroll и snapshot-callback'и в server_service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vm_prepare_helpers, _vms_helpers, vms_snapshots

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


def _assert_root_libvirt(calls: list[tuple[str, bool]], needle: str) -> None:
    """Команда с подстрокой `needle` идёт в system-libvirt под root (sudo)."""
    matched = [(cmd, sudo) for cmd, sudo in calls if needle in cmd]
    assert matched, f"команда {needle!r} не найдена"
    for cmd, sudo in matched:
        assert "LC_ALL=C LIBGUESTFS_BACKEND=direct" in cmd, cmd
        assert sudo is True, cmd


@pytest.fixture(autouse=True)
def stub_session_and_callbacks(monkeypatch):
    """Замокать open_hub_session и три VM-callback'а; обнулить reboot-задержки."""
    holder: dict = {"ssh": None}
    calls: dict = {"snapshots": [], "vm_state": []}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms_snapshots, "open_hub_session", _open)

    async def _snapshots(vm_id, snapshots, target_department_id=None, **kw):
        calls["snapshots"].append(
            {"vm_id": vm_id, "snapshots": snapshots,
             "target_department_id": target_department_id, **kw},
        )
        return {"ok": True}

    async def _vm_state(vm_id, target_department_id=None, **kw):
        calls["vm_state"].append(
            {"vm_id": vm_id, "target_department_id": target_department_id, **kw},
        )
        return {"ok": True}

    monkeypatch.setattr(
        vms_snapshots.server_service_client, "submit_vm_snapshots", _snapshots,
    )
    monkeypatch.setattr(
        vms_snapshots.server_service_client, "submit_vm_state", _vm_state,
    )
    # реальная перезагрузка гостя идёт минуты — в тестах обнуляем окна ожидания.
    monkeypatch.setattr(vms_snapshots, "_GUEST_REBOOT_SETTLE_S", 0)
    monkeypatch.setattr(vms_snapshots, "_GUEST_REBOOT_POLL_DELAY_S", 0)
    # окна ожидания смены режима на Смоленск (общий хелпер) — тоже 0.
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_SETTLE_S", 0)
    monkeypatch.setattr(_vms_helpers, "GUEST_MODE_REBOOT_POLL_DELAY_S", 0)
    return {"holder": holder, "calls": calls}


async def _set_single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1),
        )
        await session.commit()


def _hub_block(**extra):
    base = {
        "vm_id": "vm1", "hub_host": "10.0.0.7", "vm_name": "station-a",
        "is_managed": True, "target_department_id": "dep1",
    }
    base.update(extra)
    return base


# ── snapshot create ──────────────────────────────────────────────────────────


class TestSnapshotCreate:
    @pytest.mark.parametrize("snapshot_type,flag", [
        ("disk_only", "--disk-only --atomic"),
        ("full", "--live"),
        (None, None),
    ])
    async def test_create_kind_flags(
        self, snapshot_type, flag, make_task, fetch_task, captured_audit,
        stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            snapshot_id="snp1", snapshot_name="daily-1",
            snapshot_type=snapshot_type,
        )
        tid = await make_task(
            task_kind="vm.snapshot_create", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_snapshot_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmd = next(
            c for c in fake.commands if "snapshot-create-as" in c
        )
        assert "--domain station-a --name daily-1" in cmd
        if flag:
            assert flag in cmd
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert snap_cb["vm_id"] == "vm1"
        assert snap_cb["snapshots"][0]["name"] == "daily-1"
        assert snap_cb["snapshots"][0]["state"] == "ready"
        assert snap_cb["snapshots"][0]["is_current"] is True
        # снятый пользователем снимок — всегда категория user
        assert snap_cb["snapshots"][0]["kind"] == "user"
        if snapshot_type is not None:
            assert snap_cb["snapshots"][0]["snapshot_type"] == snapshot_type

    async def test_create_failure_reports_error(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("snapshot-create-as", 1, stderr="boom")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(snapshot_id="snp1", snapshot_name="daily-1")
        tid = await make_task(
            task_kind="vm.snapshot_create", target_server_id="hub1", payload=payload,
        )
        await _set_single_attempt(tid)
        await vms_snapshots.vm_snapshot_create.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_SNAPSHOT_FAILED" in t.last_error
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert snap_cb["snapshots"][0]["state"] == "error"


# ── snapshot delete ──────────────────────────────────────────────────────────


class TestSnapshotDelete:
    async def test_delete_command_and_callback(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(snapshot_id="snp1", snapshot_name="daily-1")
        tid = await make_task(
            task_kind="vm.snapshot_delete", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_snapshot_delete.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any(
            "snapshot-delete --domain station-a --snapshotname daily-1" in c
            for c in fake.commands
        )
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert snap_cb["snapshots"][0]["state"] == "deleted"


# ── snapshot revert ──────────────────────────────────────────────────────────


class TestSnapshotRevert:
    async def test_revert_command_and_state(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _FakeSshClient()
        fake.set_response("virsh domstate", 0, "running")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(snapshot_id="snp1", snapshot_name="daily-1")
        tid = await make_task(
            task_kind="vm.snapshot_revert", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_snapshot_revert.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any(
            "snapshot-revert --domain station-a --snapshotname daily-1" in c
            for c in fake.commands
        )
        # snapshot-revert и чтение domstate — под root
        _assert_root_libvirt(
            fake.calls, "snapshot-revert --domain station-a --snapshotname daily-1",
        )
        _assert_root_libvirt(fake.calls, "virsh domstate station-a")
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert snap_cb["snapshots"][0]["is_current"] is True
        state_cb = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state_cb["power_state"] == "on"


# ── astra_update ─────────────────────────────────────────────────────────────


def _astra_fake():
    fake = _FakeSshClient()
    fake.set_response("virsh domifaddr", 0, " vnet0 52:54:00:aa:bb:cc ipv4 10.177.103.101/24")
    fake.set_response("virsh domstate", 0, "running")
    return fake


class TestAstraUpdate:
    async def test_full_flow(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _astra_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            rc="1.8.1.6", base_snapshot="1.8_build", snapshot_id="snp-rc",
            repository_urls=["deb http://repo/astra 1.8 main"],
            password="newpass", ip_address="10.177.103.101", snapshot_type="full",
        )
        tid = await make_task(
            task_kind="vm.astra_update", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # 1. revert golden _build
        assert any(
            "snapshot-revert --domain station-a --snapshotname 1.8_build" in c
            for c in cmds
        )
        # 2. sources.list в госте + astra-update
        assert any("sources.list" in c and "deb http://repo/astra" in c for c in cmds)
        assert any("astra-update -A -T -r" in c for c in cmds)
        # 3. reboot гостя
        assert any("reboot" in c for c in cmds)
        # 4. chpasswd
        assert any("chpasswd" in c and "u:newpass" in c for c in cmds)
        # 5. снимок Орла <rc>
        assert any(
            "snapshot-create-as --domain station-a --name 1.8.1.6 " in c
            for c in cmds
        )
        # 6. перевод в Смоленск + снимок <rc>_smolensk
        assert any("astra-modeswitch set 2" in c for c in cmds)
        assert any("astra-mac-control enable" in c for c in cmds)
        assert any("astra-mic-control enable" in c for c in cmds)
        assert any(
            "snapshot-create-as --domain station-a --name 1.8.1.6_smolensk" in c
            for c in cmds
        )
        i_orel = next(i for i, c in enumerate(cmds) if "--name 1.8.1.6 " in c)
        i_switch = next(i for i, c in enumerate(cmds) if "astra-modeswitch set 2" in c)
        i_smol = next(i for i, c in enumerate(cmds) if "--name 1.8.1.6_smolensk" in c)
        assert i_orel < i_switch < i_smol
        # callback несёт оба deliverable'а с mode/kind/os_version
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        snaps = {s["name"]: s for s in snap_cb["snapshots"]}
        assert snaps["1.8.1.6"]["is_current"] is True
        assert snaps["1.8.1.6"]["mode"] == "oryol"
        assert snaps["1.8.1.6"]["kind"] == "os_baseline"
        assert snaps["1.8.1.6"]["snapshot_type"] == "full"
        assert snaps["1.8.1.6"]["os_version"] == "1.8.1.6"
        assert snaps["1.8.1.6_smolensk"]["mode"] == "smolensk"
        state_cb = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state_cb["power_state"] == "on"
        assert state_cb["ip_address"] == "10.177.103.101"
        assert state_cb["clear_busy_state"] is True
        assert t.result["password_changed"] is True

    async def test_empty_repositories_rejected(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _astra_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            rc="1.8.1.6", base_snapshot="1.8_build", repository_urls=[],
        )
        tid = await make_task(
            task_kind="vm.astra_update", target_server_id="hub1", payload=payload,
        )
        await _set_single_attempt(tid)
        await vms_snapshots.vm_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_ASTRA_UPDATE_NO_REPOSITORIES" in t.last_error
        # failed-callback снял lock
        state_cb = stub_session_and_callbacks["calls"]["vm_state"][0]
        assert state_cb["clear_busy_state"] is True
        assert "error" in state_cb


class TestAstraUpdateManagedKey:
    """Managed-ВМ (`creds_stash_key`): guest-шаги astra_update идут по ключу
    управляющего пользователя, временный ключ пишется и затирается."""

    async def test_guest_steps_use_key(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, stub_mgmt,
    ):
        fake = _astra_fake()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            rc="1.8.1.6", base_snapshot="1.8_build", snapshot_id="snp-rc",
            repository_urls=["deb http://repo/astra 1.8 main"],
            password="newpass", ip_address="10.177.103.101", snapshot_type="full",
            creds_stash_key="dbos:dispatch_creds:abc",
        )
        tid = await make_task(
            task_kind="vm.astra_update", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # guest-шаги (astra-update, chpasswd, смена режима) идут по ключу.
        assert any(
            "astra-update -A -T -r" in c and "ssh -i /tmp/dbos-key" in c
            and "dbos@10.177.103.101" in c for c in cmds
        )
        assert any(
            "chpasswd" in c and "u:newpass" in c and "ssh -i /tmp/dbos-key" in c
            for c in cmds
        )
        assert any(
            "astra-modeswitch set 2" in c and "ssh -i /tmp/dbos-key" in c for c in cmds
        )
        assert not any("sshpass" in c for c in cmds)
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)


# ── allta_update / passwd (reroll) ───────────────────────────────────────────


def _reroll_fake():
    fake = _FakeSshClient()
    fake.set_response("virsh domifaddr", 0, " vnet0 52:54:00:aa:bb:cc ipv4 10.177.103.101/24")
    return fake


class TestReroll:
    async def test_allta_update_skips_build_and_resnaps(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _reroll_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            ip_address="10.177.103.101",
            snapshots=[
                {"snapshot_id": "b", "name": "1.8.1.6_build", "snapshot_type": "full"},
                {"snapshot_id": "p", "name": "1.8.1.6", "snapshot_type": "full"},
            ],
        )
        tid = await make_task(
            task_kind="vm.allta_update", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_allta_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # _build пропущен: ни revert, ни resnap по нему
        assert not any("--snapshotname 1.8.1.6_build" in c for c in cmds)
        # plain снимок: revert → wget allta deb → delete → recreate
        assert any("snapshot-revert --domain station-a --snapshotname 1.8.1.6" in c for c in cmds)
        assert any("allta_" in c and "amd64.deb" in c for c in cmds)
        assert any("snapshot-delete --domain station-a --snapshotname 1.8.1.6" in c for c in cmds)
        assert any("snapshot-create-as --domain station-a --name 1.8.1.6" in c for c in cmds)
        # без пароля chpasswd не звался
        assert not any("chpasswd" in c for c in cmds)
        assert t.result["password_updated_vms"] == []
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert [s["name"] for s in snap_cb["snapshots"]] == ["1.8.1.6"]

    async def test_reroll_managed_uses_key(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks, stub_mgmt,
    ):
        fake = _reroll_fake()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            password="s3cret", ip_address="10.177.103.101",
            creds_stash_key="dbos:dispatch_creds:abc",
            snapshots=[{"snapshot_id": "p", "name": "1.8.1.6"}],
        )
        tid = await make_task(
            task_kind="vm.passwd", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_passwd.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any(
            "amd64.deb" in c and "ssh -i /tmp/dbos-key" in c
            and "dbos@10.177.103.101" in c for c in cmds
        )
        assert any(
            "chpasswd" in c and "u:s3cret" in c and "ssh -i /tmp/dbos-key" in c
            for c in cmds
        )
        assert not any("sshpass" in c for c in cmds)
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)

    async def test_passwd_requires_password(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _reroll_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            snapshots=[{"snapshot_id": "p", "name": "1.8.1.6"}],
        )
        tid = await make_task(
            task_kind="vm.passwd", target_server_id="hub1", payload=payload,
        )
        await _set_single_attempt(tid)
        await vms_snapshots.vm_passwd.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "VM_INVALID_ARG" in t.last_error

    async def test_passwd_changes_password_on_each_snapshot(
        self, make_task, fetch_task, captured_audit, stub_session_and_callbacks,
    ):
        fake = _reroll_fake()
        stub_session_and_callbacks["holder"]["ssh"] = fake
        payload = _hub_block(
            password="s3cret", ip_address="10.177.103.101",
            snapshots=[
                {"snapshot_id": "b", "name": "1.7.5.9_build"},
                {"snapshot_id": "p1", "name": "1.7.5.9"},
                {"snapshot_id": "p2", "name": "1.8.1.6"},
            ],
        )
        tid = await make_task(
            task_kind="vm.passwd", target_server_id="hub1", payload=payload,
        )
        await vms_snapshots.vm_passwd.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        chpw = [c for c in cmds if "chpasswd" in c]
        assert len(chpw) == 2  # оба plain-снимка, _build пропущен
        assert all("u:s3cret" in c for c in chpw)
        assert t.result["password_updated_vms"] == ["vm1"]
        snap_cb = stub_session_and_callbacks["calls"]["snapshots"][0]
        assert [s["name"] for s in snap_cb["snapshots"]] == ["1.7.5.9", "1.8.1.6"]
