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


class TestApkParser:
    def test_simple_name_version_rel(self):
        out = installed_packages._parse_packages("bash-5.2.15-r0\n", "apk")
        assert out == [{"name": "bash", "version": "5.2.15-r0"}]

    def test_name_with_dashes(self):
        """Имя с дефисами сохраняется — rsplit с maxsplit=2 режет только версию."""
        out = installed_packages._parse_packages("py3-pip-23.1-r0\n", "apk")
        assert out == [{"name": "py3-pip", "version": "23.1-r0"}]

    def test_fallback_no_revision(self):
        """Строка без `-r<rel>` — fallback на rsplit один раз."""
        out = installed_packages._parse_packages("musl-1.2.4\n", "apk")
        assert out == [{"name": "musl", "version": "1.2.4"}]

    def test_fallback_single_token(self):
        """Строка без дефисов — вся уходит в name с пустой version."""
        out = installed_packages._parse_packages("busybox\n", "apk")
        assert out == [{"name": "busybox", "version": ""}]

    def test_multiline_and_skips_empty(self):
        stdout = "\nbash-5.2.15-r0\n  \npy3-pip-23.1-r0\n"
        out = installed_packages._parse_packages(stdout, "apk")
        assert out == [
            {"name": "bash", "version": "5.2.15-r0"},
            {"name": "py3-pip", "version": "23.1-r0"},
        ]


class TestApkPatternFilter:
    def test_filters_by_glob(self):
        pkgs = [
            {"name": "bash", "version": "5.2.15-r0"},
            {"name": "busybox", "version": "1.36.1-r0"},
            {"name": "py3-pip", "version": "23.1-r0"},
        ]
        out = installed_packages._filter_by_pattern(pkgs, "py3*")
        assert out == [{"name": "py3-pip", "version": "23.1-r0"}]

    def test_star_returns_all(self):
        pkgs = [{"name": "bash", "version": "5.2.15-r0"}]
        assert installed_packages._filter_by_pattern(pkgs, "*") == pkgs

    def test_empty_pattern_returns_all(self):
        pkgs = [{"name": "bash", "version": "5.2.15-r0"}]
        assert installed_packages._filter_by_pattern(pkgs, "") == pkgs

    def test_case_sensitive(self):
        pkgs = [{"name": "bash", "version": "5.2.15-r0"}]
        assert installed_packages._filter_by_pattern(pkgs, "BASH") == []


class TestPacmanParser:
    def test_name_version_columns(self):
        """pacman -Q отдаёт `name version`, разбирается как dpkg."""
        stdout = "bash 5.2.015-1\nlinux 6.6.1.arch1-1\n"
        out = installed_packages._parse_packages(stdout, "pacman")
        assert out == [
            {"name": "bash", "version": "5.2.015-1"},
            {"name": "linux", "version": "6.6.1.arch1-1"},
        ]

    def test_name_with_dashes(self):
        stdout = "python-pip 23.1-1\n"
        out = installed_packages._parse_packages(stdout, "pacman")
        assert out == [{"name": "python-pip", "version": "23.1-1"}]

    def test_skips_malformed(self):
        stdout = "\nbash 5.2.015-1\nbadline\n"
        out = installed_packages._parse_packages(stdout, "pacman")
        assert out == [{"name": "bash", "version": "5.2.015-1"}]


class TestPortageParser:
    def test_strips_category_and_splits_version(self):
        out = installed_packages._parse_packages("app-shells/bash-5.2_p15\n", "portage")
        assert out == [{"name": "bash", "version": "5.2_p15"}]

    def test_revision_suffix(self):
        out = installed_packages._parse_packages("dev-python/pip-23.1-r1\n", "portage")
        assert out == [{"name": "pip", "version": "23.1-r1"}]

    def test_name_with_dashes(self):
        """Имя с дефисами — non-greedy match режет по первому дефису перед цифрой."""
        out = installed_packages._parse_packages("dev-libs/libfoo-bar-1.2\n", "portage")
        assert out == [{"name": "libfoo-bar", "version": "1.2"}]

    def test_no_version_keeps_basename(self):
        out = installed_packages._parse_packages("virtual/jdk\n", "portage")
        assert out == [{"name": "jdk", "version": ""}]


class TestXbpsParser:
    def test_state_token_stripped(self):
        out = installed_packages._parse_packages(
            "ii bash-5.2.015_1   GNU Bourne Again Shell\n", "xbps",
        )
        assert out == [{"name": "bash", "version": "5.2.015_1"}]

    def test_name_with_dashes(self):
        out = installed_packages._parse_packages(
            "ii python3-pip-23.1_1   pip for python3\n", "xbps",
        )
        assert out == [{"name": "python3-pip", "version": "23.1_1"}]

    def test_skips_short_lines(self):
        stdout = "ii\nii bash-5.2.015_1  desc\n"
        out = installed_packages._parse_packages(stdout, "xbps")
        assert out == [{"name": "bash", "version": "5.2.015_1"}]


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

    def test_apk_command_no_pattern_in_shell(self):
        """apk не глоббит — команда листит всё, pattern в shell не уходит."""
        cmd = installed_packages._build_command("apk", "py3*")
        assert "apk info -v" in cmd
        # Pattern не подставляется в shell-команду для apk.
        assert "py3" not in cmd
        assert "'" not in cmd

    def test_unknown_manager_raises(self):
        from src.clients.ssh import SshError
        with pytest.raises(SshError) as exc:
            installed_packages._build_command("nix", "htop")
        assert exc.value.error_code == "NO_PACKAGE_MANAGER"

    def test_pacman_command_no_pattern_in_shell(self):
        """pacman не глоббит — листим всё, pattern в shell не уходит."""
        cmd = installed_packages._build_command("pacman", "py3*")
        assert "pacman -Q" in cmd
        assert "py3" not in cmd
        assert "'" not in cmd

    def test_portage_command_no_pattern_in_shell(self):
        cmd = installed_packages._build_command("portage", "bash*")
        assert "qlist -Iv" in cmd
        assert "bash" not in cmd
        assert "'" not in cmd

    def test_xbps_command_no_pattern_in_shell(self):
        cmd = installed_packages._build_command("xbps", "bash*")
        assert "xbps-query -l" in cmd
        assert "bash" not in cmd
        assert "'" not in cmd


class TestPatternValidation:
    @pytest.mark.parametrize("pattern", [
        "htop", "linux-image*", "lib*.dev", "foo_bar", "a.b.c", "pkg+extra",
        "py3[0-9]", "*",
    ])
    def test_accepts_legit_globs(self, pattern):
        assert installed_packages._PATTERN_RE.match(pattern)

    @pytest.mark.parametrize("pattern", [
        "a'; id >/tmp/pwned; echo '",  # quote break-out
        "$(reboot)",                    # command substitution
        "`id`",                         # backtick
        "foo; rm -rf /",               # statement separator
        "foo bar",                      # whitespace
        "foo\nbar",                     # newline
        "foo|cat",                      # pipe
        "foo>out",                      # redirect
        "foo&bar",                      # background / and
        "foo$VAR",                      # var expansion
        "",                             # empty
    ])
    def test_rejects_injection(self, pattern):
        assert not installed_packages._PATTERN_RE.match(pattern)


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


def _patch_build_session(monkeypatch, prepared: _FakeSshClient) -> list:
    """Подменяет `ssh_client.build_session` так, чтобы он отдавал prepared
    fake-client и записывал, под какие creds его звали.

    Возвращает список захваченных (creds, server_id) — тесты сверяют выбор
    сессии (self vs management) через содержимое payload.
    """
    calls: list[tuple[dict, str]] = []

    def _fake_build(creds: dict, server_id: str):
        calls.append((dict(creds), server_id))
        return prepared

    monkeypatch.setattr(
        "src.tasks.installed_packages.ssh_client.build_session", _fake_build
    )
    return calls


class TestInstalledPackagesTask:
    async def test_success_deb(self, make_task, fetch_task, captured_audit, monkeypatch):
        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\nvim 9.0\n")
        _patch_build_session(monkeypatch, fake)

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
        _patch_build_session(monkeypatch, fake)

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
        _patch_build_session(monkeypatch, fake)

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

    async def test_detect_apk_when_no_dpkg_rpm(self, monkeypatch):
        """dpkg-query и rpm нет, `command -v apk` rc=0 → detect возвращает 'apk'."""
        fake = _FakeSshClient(host="alpine1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 0, "/sbin/apk\n")
        pm = await installed_packages._detect_package_manager(fake)
        assert pm == "apk"

    async def test_success_apk(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Alpine: нет dpkg/rpm, есть apk → `apk info -v`, парсинг + фильтр py3*."""
        fake = _FakeSshClient(host="alpine1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 0, "/sbin/apk\n")
        fake.set_response(
            "apk info -v", 0, "bash-5.2.15-r0\nbusybox-1.36.1-r0\npy3-pip-23.1-r0\n",
        )
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_alpine",
            payload={
                "server_id": "srv_alpine",
                "pattern": "py3*",
                "ssh_host": "alpine1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "apk"
        # Фильтр py3* оставил один пакет из трёх.
        assert t.result["count"] == 1
        assert t.result["packages"] == [{"name": "py3-pip", "version": "23.1-r0"}]
        # apk info -v выполнялся без pattern в shell.
        assert any("apk info -v" in c for c in fake.commands)
        assert not any("py3" in c for c in fake.commands)
        # Audit-счётчик package_manager корректно несёт 'apk'.
        details = captured_audit[0]["details"].get("result", {})
        assert details.get("package_manager") == "apk"
        assert "packages" not in details

    async def test_detect_pacman_when_no_dpkg_rpm_apk(self):
        fake = _FakeSshClient(host="arch1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 0, "/usr/bin/pacman\n")
        pm = await installed_packages._detect_package_manager(fake)
        assert pm == "pacman"

    async def test_detect_portage_via_qlist(self):
        fake = _FakeSshClient(host="gentoo1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 1, "")
        fake.set_response("command -v qlist", 0, "/usr/bin/qlist\n")
        pm = await installed_packages._detect_package_manager(fake)
        assert pm == "portage"

    async def test_detect_xbps(self):
        fake = _FakeSshClient(host="void1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 1, "")
        fake.set_response("command -v qlist", 1, "")
        fake.set_response("command -v xbps-query", 0, "/usr/bin/xbps-query\n")
        pm = await installed_packages._detect_package_manager(fake)
        assert pm == "xbps"

    async def test_success_pacman(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Arch: detect pacman → `pacman -Q`, парсинг + фильтр py3*."""
        fake = _FakeSshClient(host="arch1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 0, "/usr/bin/pacman\n")
        fake.set_response(
            "pacman -Q", 0, "bash 5.2.015-1\npython-pip 23.1-1\nlinux 6.6.1-1\n",
        )
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_arch",
            payload={
                "server_id": "srv_arch",
                "pattern": "python*",
                "ssh_host": "arch1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "pacman"
        assert t.result["count"] == 1
        assert t.result["packages"] == [{"name": "python-pip", "version": "23.1-1"}]
        assert any("pacman -Q" in c for c in fake.commands)
        assert not any("python" in c for c in fake.commands)

    async def test_success_portage(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Gentoo: detect portage → `qlist -Iv`, парсинг + фильтр."""
        fake = _FakeSshClient(host="gentoo1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 1, "")
        fake.set_response("command -v qlist", 0, "/usr/bin/qlist\n")
        fake.set_response(
            "qlist -Iv", 0,
            "app-shells/bash-5.2_p15\ndev-python/pip-23.1-r1\ndev-libs/libfoo-bar-1.2\n",
        )
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_gentoo",
            payload={
                "server_id": "srv_gentoo",
                "pattern": "bash",
                "ssh_host": "gentoo1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "portage"
        assert t.result["count"] == 1
        assert t.result["packages"] == [{"name": "bash", "version": "5.2_p15"}]
        assert any("qlist -Iv" in c for c in fake.commands)

    async def test_success_xbps(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Void: detect xbps → `xbps-query -l`, парсинг + фильтр."""
        fake = _FakeSshClient(host="void1.example")
        fake.set_response("command -v dpkg-query", 1, "")
        fake.set_response("command -v rpm", 1, "")
        fake.set_response("command -v apk", 1, "")
        fake.set_response("command -v pacman", 1, "")
        fake.set_response("command -v qlist", 1, "")
        fake.set_response("command -v xbps-query", 0, "/usr/bin/xbps-query\n")
        fake.set_response(
            "xbps-query -l", 0,
            "ii bash-5.2.015_1   GNU Bourne Again Shell\n"
            "ii python3-pip-23.1_1   pip for python3\n",
        )
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_void",
            payload={
                "server_id": "srv_void",
                "pattern": "python3*",
                "ssh_host": "void1.example",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["package_manager"] == "xbps"
        assert t.result["count"] == 1
        assert t.result["packages"] == [{"name": "python3-pip", "version": "23.1_1"}]
        assert any("xbps-query -l" in c for c in fake.commands)
        details = captured_audit[0]["details"].get("result", {})
        assert details.get("package_manager") == "xbps"
        assert "packages" not in details

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
        _patch_build_session(monkeypatch, fake)

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
        _patch_build_session(monkeypatch, fake)

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

    async def test_injection_pattern_rejected_before_ssh(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """pattern с shell-метасимволами → task FAILED, SSH не открывается."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        fake = _FakeSshClient(host="srv1.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        _patch_build_session(monkeypatch, fake)

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv1",
            payload={
                "server_id": "srv1",
                "pattern": "a'; touch /tmp/pwned; echo '",
                "ssh_host": "srv1.example",
            },
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "INVALID_PATTERN" in t.last_error
        # SSH-команды не выполнялись — валидация до подключения.
        assert fake.commands == []

    async def test_ssh_connect_failure_propagates_to_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`SshClient.__aenter__` raise'ит `SshError` (auth/connect) → FAILED."""
        from sqlalchemy import update
        from src.clients.ssh import SshError
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        class _BoomClient:
            host = ""

            async def __aenter__(self):
                raise SshError(
                    error_code="SSH_AUTH_FAILED",
                    host=self.host,
                    message="authentication failed",
                )

            async def __aexit__(self, *args):
                return None

        def _boom_build(creds, server_id):
            return _BoomClient()

        monkeypatch.setattr(
            "src.tasks.installed_packages.ssh_client.build_session", _boom_build,
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

    async def test_managed_server_uses_session_hints_and_skips_password_fetch(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """На managed-сервере handler не должен дёргать fetch_account_password
        ради пароля (его может вовсе не быть у discovered-аккаунта).
        Сессия собирается через ssh_client.build_session с is_managed/
        management_user в creds — управляющая ключевая сессия.
        """
        fake = _FakeSshClient(host="srv_m.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        fake.set_response("dpkg-query -W", 0, "htop 3.0.5-7\n")
        captured_creds = _patch_build_session(monkeypatch, fake)

        fetch_calls: list = []

        async def _spy(server_id, account_id, target_department_id=None):
            fetch_calls.append((server_id, account_id, target_department_id))
            return {"login": "ops", "password": "p"}

        monkeypatch.setattr(
            "src.tasks._account_helpers.server_service_client.fetch_account_password",
            _spy,
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_m",
            payload={
                "server_id": "srv_m",
                "pattern": "htop",
                "ssh_host": "srv_m.example",
                "account_id": "acc_discovered",
                "is_managed": True,
                "management_user": "dbos",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # На managed fetch не дёргается — пароль не нужен для управляющей сессии.
        assert fetch_calls == []
        # build_session получил creds с is_managed/management_user hints.
        assert captured_creds, "build_session must be called"
        creds, server_id = captured_creds[0]
        assert server_id == "srv_m"
        assert creds.get("is_managed") is True
        assert creds.get("management_user") == "dbos"

    async def test_managed_without_account_id_uses_default_login(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Managed без account_id в payload — fallback на ssh_login,
        fetch_account_password не зовётся. Сессия по-прежнему ключевая."""
        fake = _FakeSshClient(host="srv_m2.example")
        fake.set_response("command -v dpkg-query", 0, "/usr/bin/dpkg-query\n")
        fake.set_response("dpkg-query -W", 0, "vim 9.0\n")
        captured_creds = _patch_build_session(monkeypatch, fake)

        fetch_calls: list = []

        async def _spy(server_id, account_id, target_department_id=None):
            fetch_calls.append((server_id, account_id, target_department_id))
            return {"login": "ops", "password": "p"}

        monkeypatch.setattr(
            "src.tasks._account_helpers.server_service_client.fetch_account_password",
            _spy,
        )

        tid = await make_task(
            task_kind="installed_packages.list",
            target_server_id="srv_m2",
            payload={
                "server_id": "srv_m2",
                "pattern": "vim",
                "ssh_host": "srv_m2.example",
                "is_managed": True,
                "management_user": "dbos",
            },
        )
        await installed_packages.installed_packages_list.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Нет account_id → fetch не дёргался.
        assert fetch_calls == []
        creds, _ = captured_creds[0]
        assert creds.get("is_managed") is True
        assert creds.get("management_user") == "dbos"
