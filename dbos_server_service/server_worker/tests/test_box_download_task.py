"""Тесты worker-таски `box.download` — скачивание/импорт бокса реестра на hub.

SSH мокается `_FakeSshClient` (ответы по подстроке команды), `open_hub_session`
и `submit_box_download_state` — monkeypatch'ем. Ассертим: куда качается артефакт
(storage-pool боксов hub'а), обработку формата (tar → распаковка, qcow/raw →
файл), whitelist схемы/формата, smb-фолбэк и статус-callback (ready/error).
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import box_download


class _FakeSshClient:
    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []

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

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")


@pytest.fixture
def stub_session(monkeypatch):
    holder: dict = {"ssh": None}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(box_download, "open_hub_session", _open)
    return holder


@pytest.fixture
def captured_state(monkeypatch):
    calls: list[dict] = []

    async def _submit(box_id, status, target_department_id=None, *, error=None):
        calls.append({
            "box_id": box_id, "status": status,
            "target_department_id": target_department_id, "error": error,
        })
        return {}

    monkeypatch.setattr(
        box_download.server_service_client, "submit_box_download_state", _submit,
    )
    return calls


def _payload(**extra) -> dict:
    payload = {
        "box_id": "box_1",
        "box_name": "vm_station",
        "download_url": "https://images.example.com/vm_station.tar.gz",
        "format": "tar.gz",
        "hub_server_id": "hub1",
        "server_id": "hub1",
        "host": "10.0.0.7",
        "is_managed": True,
        "management_user": "dbos",
        "target_department_id": "dep1",
    }
    payload.update(extra)
    return payload


async def _force_single_attempt(tid: str) -> None:
    from sqlalchemy import update

    from src.db.session import AsyncSessionLocal
    from src.models import Task
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


class TestBoxDownloadHappyPath:
    async def test_tar_downloads_and_extracts_into_pool(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(),
        )
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["status"] == "ready"
        assert t.result["format"] == "tar.gz"
        # curl тянет во временный файл в пуле боксов hub'а (там же vm.create ищет образ).
        assert any(
            "curl -fSL" in c and "/vms/.vm_station.box-download" in c
            and "https://images.example.com/vm_station.tar.gz" in c
            for c in fake.commands
        )
        # tar распакован в /vms (артефакт несёт готовый vm_station.qcow2).
        assert any("tar xf" in c and "-C /vms" in c for c in fake.commands)
        # ready-callback ушёл в server_service.
        assert captured_state == [{
            "box_id": "box_1", "status": "ready",
            "target_department_id": "dep1", "error": None,
        }]

    async def test_qcow_places_file_as_qcow2(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(
                box_name="single7", format="qcow2",
                download_url="ftp://10.0.0.1/single7.qcow2",
            ),
        )
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["target_path"] == "/vms/single7.qcow2"
        assert any("mv -f" in c and "/vms/single7.qcow2" in c for c in fake.commands)
        assert not any("tar xf" in c for c in fake.commands)

    async def test_raw_places_file_as_raw(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(
                box_name="rawbox", format="raw",
                download_url="https://x/y.raw",
            ),
        )
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["target_path"] == "/vms/rawbox.raw"


class TestBoxDownloadSmb:
    async def test_smb_uses_smbget_when_present(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        fake.set_response("command -v smbget", 0, "/usr/bin/smbget")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(
                box_name="smbbox", format="qcow2",
                download_url="smb://share/host/smbbox.qcow2",
            ),
        )
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("smbget -q -O" in c for c in fake.commands)

    async def test_smb_without_smbget_fails_clearly(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        fake.set_response("command -v smbget", 1, "")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(
                box_name="smbbox", format="qcow2",
                download_url="smb://share/host/smbbox.qcow2",
            ),
        )
        await _force_single_attempt(tid)
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert not any("smbget" in c and "-O" in c for c in fake.commands)
        # error-callback с текстом кода.
        assert captured_state[-1]["status"] == "error"
        assert captured_state[-1]["error"] == "BOX_DOWNLOAD_SMB_UNSUPPORTED"


class TestBoxDownloadValidation:
    async def test_bad_scheme_fails_before_ssh(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(download_url="file:///etc/passwd", format="raw"),
        )
        await _force_single_attempt(tid)
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        # До hub'а не дошли — команд нет.
        assert fake.commands == []
        assert captured_state[-1]["error"] == "BOX_DOWNLOAD_INVALID_URL"

    async def test_unsupported_format_fails(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(format="vmdk", download_url="https://x/y.vmdk"),
        )
        await _force_single_attempt(tid)
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert captured_state[-1]["error"] == "BOX_IMPORT_UNSUPPORTED_FORMAT"

    async def test_injection_in_box_name_rejected(
        self, make_task, fetch_task, stub_session, captured_state,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="box.download", target_server_id="hub1",
            payload=_payload(box_name="a; rm -rf /", format="qcow2"),
        )
        await _force_single_attempt(tid)
        await box_download.box_download.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert not any("rm -rf" in c for c in fake.commands)
