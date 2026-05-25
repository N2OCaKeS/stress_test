"""Тесты worker-таски `installed_packages.list`.

Покрытие (6 тестов):

* success deb — `command -v dpkg-query` rc=0 → `dpkg-query -W ...`
  возвращает строки `name version` → парсинг в `{packages: [{name, version}]}`.
* success rpm — нет dpkg-query, есть rpm → `rpm -qa --queryformat ...` путь.
* пустой результат (rc=1 без stderr) — dpkg-query так возвращает «ничего не
  подошло» под pattern; должен трактоваться как empty list, task SUCCEEDED.
* no package manager — ни dpkg, ни rpm → `SshError(NO_PACKAGE_MANAGER)` →
  task FAILED через runner.
* dpkg query fails (rc!=0 + stderr) → `SshError(PACKAGE_QUERY_FAILED)` → FAILED.
* parser устойчив к мусору — пустые строки, строки без пробела пропускаются.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import installed_packages


# ── Парсер — без сети, чистый юнит ──────────────────────────────────────────


class TestParser:
    def test_simple_two_columns(self):
        stdout = "htop 3.0.5-7\nvim 9.0\n"
        out = installed_packages._parse_packages(stdout)
        assert out == [
            {"name": "htop", "version": "3.0.5-7"},
            {"name": "vim", "version": "9.0"},
        ]

    def test_skips_empty_and_malformed_lines(self):
        stdout = "\nhtop 3.0.5-7\n\n   \nbadline_no_space\nfoo 1.0\n"
        out = installed_packages._parse_packages(stdout)
        assert out == [
            {"name": "htop", "version": "3.0.5-7"},
            {"name": "foo", "version": "1.0"},
        ]

    def test_multiple_versions_keep_all(self):
        """`linux-image-*` обычно возвращает несколько строк — разные kernel'ы."""
        stdout = "linux-image-5.10 5.10.0-1\nlinux-image-5.10 5.10.0-2\n"
        out = installed_packages._parse_packages(stdout)
        assert len(out) == 2
        assert {p["version"] for p in out} == {"5.10.0-1", "5.10.0-2"}


# ── _build_command ──────────────────────────────────────────────────────────


class TestBuildCommand:
    def test_dpkg_command(self):
        cmd = installed_packages._build_command("dpkg", "htop")
        assert "dpkg-query -W" in cmd
        assert "'htop'" in cmd

    def test_rpm_command(self):
        cmd = installed_packages._build_command("rpm", "htop")
        assert "rpm -qa" in cmd
        assert "'htop'" in cmd

    def test_unknown_manager_raises(self):
        from src.clients.ssh import SshError
        with pytest.raises(SshError) as exc:
            installed_packages._build_command("apk", "htop")
        assert exc.value.error_code == "NO_PACKAGE_MANAGER"


# ── Task-handler через run_task ─────────────────────────────────────────────


class _FakeSshClient:
    """Mock SshClient — записывает run-вызовы, возвращает заранее заданные
    `(rc, stdout, stderr)` для каждой команды. Используется как async
    context manager.
    """

    def __init__(self, *args, **kwargs):
        self.host = kwargs.get("host") or (args[0] if args else "")
        self._responses: dict[str, tuple[int, str, str]] = {}
        self.commands: list[str] = []

    def set_response(self, cmd_pattern: str, rc: int, stdout: str, stderr: str = "") -> None:
        self._responses[cmd_pattern] = (rc, stdout, stderr)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        for pat, resp in self._responses.items():
            if pat in command:
                return resp
        # дефолт — rc=127, "command not found"
        return (127, "", f"sh: {command}: not found")


def _make_fake_client_class(prepared: _FakeSshClient):
    """Возвращает class-like callable, который при __init__ отдаёт prepared
    инстанс. Через monkeypatch заменяет реальный SshClient.
    """
    class _Factory:
        def __new__(cls, *args, **kwargs):  # noqa: ARG003
            return prepared
    return _Factory


class TestInstalledPackagesTask:
    async def test_success_deb(self, make_task, fetch_task, captured_audit, monkeypatch):
        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\nvim 9.0\n")
        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _make_fake_client_class(fake),
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv1",
            payload={
                "server_id": "srv1",
                "pattern": "*",
                "ssh_host": "srv1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "dpkg"
        assert t.result["count"] == 2
        assert t.result["packages"] == [
            {"name": "htop", "version": "3.0.5-7"},
            {"name": "vim", "version": "9.0"},
        ]
        # Audit пишет только safe-fields, packages внутрь не утекают.
        details = captured_audit[0]["details"].get("result", {})
        assert "packages" not in details
        assert details.get("count") == 2
        assert details.get("package_manager") == "dpkg"

    async def test_success_rpm(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Нет dpkg-query (rc=1) — falls back на rpm."""
        fake = _FakeSshClient(host="rhel1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 0, "/usr/bin/rpm\n")
        fake.set_response("rpm -qa", 0, "openssl 3.0.2\nbash 5.1.16\n")
        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _make_fake_client_class(fake),
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_rhel",
            payload={
                "server_id": "srv_rhel",
                "pattern": "*",
                "ssh_host": "rhel1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "rpm"
        assert t.result["count"] == 2
        assert {p["name"] for p in t.result["packages"]} == {"openssl", "bash"}

    async def test_empty_result_treated_as_zero_packages(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """dpkg-query c rc=1 без stderr — «ничего не подошло»; task SUCCEEDED, packages=[]."""
        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        # rc=1, stdout пустой, stderr пустой — empty result.
        fake.set_response("dpkg-query -W", 1, "", "")
        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _make_fake_client_class(fake),
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv1",
            payload={"server_id": "srv1", "pattern": "nothing-matches*"},
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["count"] == 0
        assert t.result["packages"] == []

    async def test_no_package_manager_fails_task(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Ни dpkg, ни rpm — SshError(NO_PACKAGE_MANAGER) → FAILED через runner."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _make_fake_client_class(fake),
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_alpine",
            payload={"server_id": "srv_alpine", "pattern": "*"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "NO_PACKAGE_MANAGER" in t.last_error

    async def test_query_failure_with_stderr_fails_task(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """dpkg-query rc!=0 со stderr (например, повреждена БД пакетов) → FAILED."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        fake.set_response(
            "dpkg-query -W", 2, "",
            "dpkg: error: failed to open package info file",
        )
        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _make_fake_client_class(fake),
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_broken",
            payload={"server_id": "srv_broken", "pattern": "*"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "PACKAGE_QUERY_FAILED" in t.last_error

    async def test_ssh_connect_failure_propagates_to_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`SshClient.__aenter__` raise'ит `SshError` (auth/connect) → FAILED."""
        from sqlalchemy import update
        from src.clients.ssh import SshError
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        class _BoomClient:
            def __init__(self, *args, **kwargs):
                self.host = kwargs.get("host", "")

            async def __aenter__(self):
                raise SshError(
                    error_code="SSH_AUTH_FAILED",
                    host=self.host,
                    message="authentication failed",
                )

            async def __aexit__(self, *args):
                return None

        monkeypatch.setattr(
            "src.tasks.installed_packages.SshClient",
            _BoomClient,
        )
        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_locked",
            payload={"server_id": "srv_locked", "pattern": "*"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "SSH_AUTH_FAILED" in t.last_error
