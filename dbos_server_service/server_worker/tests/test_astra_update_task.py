"""Тесты worker-таски `server.astra_update`.

Покрытие:

* success — перезапись `/etc/apt/sources.list` из payload.repositories +
  `apt update && astra-update`, success-callback (`succeeded=True`), task
  SUCCEEDED, содержимое sources.list ушло на stdin `tee`.
* apt/astra-update rc!=0 → `SshError(ASTRA_UPDATE_FAILED)`, failed-callback
  (`succeeded=False`), task FAILED (max_attempts=1).
* sources.list write rc!=0 → `SshError(ASTRA_UPDATE_SOURCES_WRITE_FAILED)`,
  failed-callback, task FAILED.
* `_sanitize_repositories` — пустой список / перевод строки внутри записи →
  SshError.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.clients.ssh import SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import astra_update


@pytest.fixture(autouse=True)
def stub_management_and_callback(monkeypatch):
    """Замокать attach_management_creds (no-op) и submit-callback.

    Возвращает список вызовов submit_astra_update_result — тесты сверяют
    `succeeded` и переданный `os_version_id`.
    """
    async def _attach(credentials, server_id):  # noqa: ARG001
        return credentials

    monkeypatch.setattr(
        "src.tasks.astra_update.ssh_client.attach_management_creds", _attach,
    )

    calls: list[dict] = []

    async def _submit(server_id, os_version_id, *, succeeded, target_department_id=None):
        calls.append({
            "server_id": server_id,
            "os_version_id": os_version_id,
            "succeeded": succeeded,
            "target_department_id": target_department_id,
        })
        return {"ok": True}

    monkeypatch.setattr(
        "src.tasks.astra_update.server_service_client.submit_astra_update_result",
        _submit,
    )
    return calls


class _FakeSshClient:
    """Mock SshClient — записывает (command, stdin_payload) и отдаёт заданные
    `(rc, stdout, stderr)` по подстроке команды. Async context manager.
    """

    def __init__(self, *args, **kwargs):
        self.host = kwargs.get("host") or (args[0] if args else "")
        self._responses: dict[str, tuple[int, str, str]] = {}
        self.commands: list[str] = []
        self.stdins: list[str | None] = []

    def set_response(self, cmd_pattern: str, rc: int, stdout: str = "", stderr: str = "") -> None:
        self._responses[cmd_pattern] = (rc, stdout, stderr)

    async def connect(self):
        return None

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        self.stdins.append(stdin_payload)
        for pat, resp in self._responses.items():
            if pat in command:
                return resp
        return (127, "", f"sh: {command}: not found")


def _patch_build_session(monkeypatch, prepared: _FakeSshClient) -> None:
    def _fake_build(creds: dict, server_id: str):  # noqa: ARG001
        return prepared

    monkeypatch.setattr(
        "src.tasks.astra_update.ssh_client.build_session", _fake_build,
    )


async def _set_single_attempt(tid: str) -> None:
    """Astra_update в проде диспатчится max_attempts=1; воспроизводим это, чтобы
    падение было терминальным (FAILED), а не ушло в retry."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


class TestSanitizeRepositories:
    def test_empty_list_rejected(self):
        with pytest.raises(SshError) as exc:
            astra_update._sanitize_repositories([], "h")
        assert exc.value.error_code == "ASTRA_UPDATE_NO_REPOSITORIES"

    def test_newline_inside_entry_rejected(self):
        with pytest.raises(SshError) as exc:
            astra_update._sanitize_repositories(["deb a\ndeb b"], "h")
        assert exc.value.error_code == "ASTRA_UPDATE_INVALID_REPOSITORY"

    def test_joins_with_trailing_newline(self):
        out = astra_update._sanitize_repositories(
            ["deb http://repo/a stable main", "deb http://repo/b stable main"], "h",
        )
        assert out == "deb http://repo/a stable main\ndeb http://repo/b stable main\n"


class TestAstraUpdateTask:
    async def test_success_writes_sources_and_runs_update(
        self, make_task, fetch_task, captured_audit, monkeypatch, stub_management_and_callback,
    ):
        fake = _FakeSshClient(host="10.0.0.7")
        fake.set_response("tee /etc/apt/sources.list", 0)
        fake.set_response("astra-update", 0, "done\n")
        _patch_build_session(monkeypatch, fake)

        repos = ["deb http://repo/orel stable main", "deb http://repo/extra stable main"]
        tid = await make_task(
            task_kind="server.astra_update",
            target_server_id="srv1",
            payload={
                "server_id": "srv1",
                "os_version_id": "osv_target",
                "repositories": repos,
                "host": "10.0.0.7",
                "is_managed": True,
                "management_user": "dbos",
                "target_department_id": "dep1",
            },
        )
        await astra_update.server_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["os_version_id"] == "osv_target"
        assert t.result["succeeded"] is True
        # sources.list переписан именно репозиториями payload'а (на stdin tee).
        tee_idx = next(
            i for i, c in enumerate(fake.commands) if "tee /etc/apt/sources.list" in c
        )
        assert fake.stdins[tee_idx] == "\n".join(repos) + "\n"
        # apt update && astra-update действительно выполнялись.
        assert any("apt-get update" in c and "astra-update" in c for c in fake.commands)
        # success-callback (succeeded=True) с правильным os_version_id.
        assert stub_management_and_callback == [{
            "server_id": "srv1",
            "os_version_id": "osv_target",
            "succeeded": True,
            "target_department_id": "dep1",
        }]

    async def test_update_command_failure_reports_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch, stub_management_and_callback,
    ):
        fake = _FakeSshClient(host="10.0.0.7")
        fake.set_response("tee /etc/apt/sources.list", 0)
        fake.set_response("astra-update", 100, "", "astra-update: broken repo\n")
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="server.astra_update",
            target_server_id="srv1",
            payload={
                "server_id": "srv1",
                "os_version_id": "osv_target",
                "repositories": ["deb http://repo/orel stable main"],
                "host": "10.0.0.7",
                "is_managed": True,
                "management_user": "dbos",
                "target_department_id": "dep1",
            },
        )
        await _set_single_attempt(tid)
        await astra_update.server_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "ASTRA_UPDATE_FAILED" in t.last_error
        # failed-callback снял блокировку (succeeded=False).
        assert stub_management_and_callback == [{
            "server_id": "srv1",
            "os_version_id": "osv_target",
            "succeeded": False,
            "target_department_id": "dep1",
        }]

    async def test_sources_write_failure_reports_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch, stub_management_and_callback,
    ):
        fake = _FakeSshClient(host="10.0.0.7")
        fake.set_response("tee /etc/apt/sources.list", 1, "", "permission denied\n")
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="server.astra_update",
            target_server_id="srv1",
            payload={
                "server_id": "srv1",
                "os_version_id": "osv_target",
                "repositories": ["deb http://repo/orel stable main"],
                "host": "10.0.0.7",
                "is_managed": True,
                "management_user": "dbos",
            },
        )
        await _set_single_attempt(tid)
        await astra_update.server_astra_update.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "ASTRA_UPDATE_SOURCES_WRITE_FAILED" in t.last_error
        # astra-update не запускался — упали на записи sources.list.
        assert not any("astra-update" in c for c in fake.commands)
        assert stub_management_and_callback[0]["succeeded"] is False
