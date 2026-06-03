"""Coverage W31 — точечные пробелы вокруг `_write_authorized_key`,
`_install_authorized_key`, `_sudo_group_membership` и audit-маски
`installed_packages.pattern`.

GAP-12  `_write_authorized_key` через `create_user(force_replace=True)`:
        existing user, force_replace=True → bash-команда содержит overwrite
        (`>` без `>>`), ключ уходит на stdin.
GAP-13  `_write_authorized_key` напрямую (existing user path): stdin
        содержит ровно ключ, не команду — закрывает GAP-13 из W6 cov-report'а.
GAP-14  `_install_authorized_key` принимает `target_home` через
        `create_user(home_dir=...)`: системный home отбивается до SSH.
GAP-15  `_sudo_group_membership` `id -nG` rc!=0 → пустое множество (юзер
        исчез между user_exists и id -nG, либо сломанная NSS).

Дополнительно — audit-mask для `installed_packages.pattern`: дефолтный
whitelist не содержит `pattern`; включение `AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG=true`
расширяет whitelist на `pattern`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError
from tests._ssh_mock_helpers import make_conn, run_result


_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIW31KeyForCoverage "
    "w31@test"
)


def _client_with_conn(run_results):
    ssh = SshClient(host="10.0.0.31", username="dbos", password="pwd")
    ssh._conn = make_conn(run_results)
    return ssh


# ══════════════════════════════════════════════════════════════════════════════
# GAP-12  create_user(force_replace=True) на существующем юзере → bash с `>`,
#         ключ на stdin (не в команде)
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateUserForceReplaceExistingUser:
    """`create_user` на уже существующем юзере с `force_replace=True`.

    Прежние тесты гоняли `force_replace` либо изолированно через
    `_install_authorized_key`, либо через create_user на новом юзере.
    Здесь идёт `create_user` → existing-user branch → `modify_user`
    (no-op без groups/shell) → `_write_authorized_key(force_replace=True)`.
    Закрывает branch ssh.py:429-434 (existing-user authorized_keys с
    force_replace).
    """

    async def test_existing_user_force_replace_overwrites_file(self, monkeypatch):
        # getent passwd: юзер есть (rc=0) → modify_user (no-op, opts=[])
        # → _write_authorized_key(force_replace=True) → _install_authorized_key
        # → один bash-вызов с overwrite-команды.
        conn = make_conn([
            run_result("deploy:x:1001:1001::/home/deploy:/bin/bash", "", 0),  # user_exists
            run_result("", "", 0),  # bash auth_keys overwrite
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            await ssh.create_user(
                "deploy",
                public_key=_PUBKEY,
                force_replace=True,
            )

        # Должно быть ровно 2 вызова: user_exists + bash auth_keys.
        # modify_user без groups/shell/sudo не дёргает usermod.
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert any("getent passwd" in c for c in cmds)
        assert not any("usermod" in c for c in cmds)
        # bash-команда содержит overwrite-redirect, не append.
        bash_cmd = next(c for c in cmds if "authorized_keys" in c)
        assert "> \"$home/.ssh/authorized_keys\"" in bash_cmd
        assert ">>" not in bash_cmd
        # Ключ — на stdin (input=), не в команде.
        assert _PUBKEY not in bash_cmd
        bash_call = next(
            c for c in conn.run.await_args_list
            if "authorized_keys" in (c.args[0] if c.args else "")
        )
        assert _PUBKEY in bash_call.kwargs.get("input", "")


# ══════════════════════════════════════════════════════════════════════════════
# GAP-13  _write_authorized_key (truncate=False через caller-shape):
#         stdin содержит ровно ключ, не «другой текст»
# ══════════════════════════════════════════════════════════════════════════════


class TestWriteAuthorizedKeyStdinShape:
    """`_write_authorized_key(force_replace=False)` через wrapper —
    invariant: ключ уходит на stdin, в bash-команде его нет.

    Существующий test_install_authorized_key.py проверяет
    `_install_authorized_key` напрямую. Здесь дополнительно фиксируем
    что вызов через `_write_authorized_key` (caller-wrapper) тоже не
    подставляет ключ в командную строку — invariant держится на обоих
    уровнях API.
    """

    async def test_write_authorized_key_keeps_key_off_argv(self):
        ssh = _client_with_conn([run_result("", "", 0)])
        await ssh._write_authorized_key(
            login="deploy",
            public_key=_PUBKEY,
            force_replace=False,
        )
        call = ssh._conn.run.await_args
        cmd = call.args[0]
        # Ключ не в команде — только на stdin (после sudo-пароля).
        assert _PUBKEY not in cmd
        stdin = call.kwargs.get("input", "")
        assert _PUBKEY in stdin
        # truncate=False (force_replace=False) → idempotent append.
        assert "grep -qxF" in cmd
        assert ">>" in cmd


# ══════════════════════════════════════════════════════════════════════════════
# GAP-14  create_user(home_dir=системный) → Python-guard в
#         _install_authorized_key срабатывает до SSH-команды auth_keys
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateUserHomeDirForbidden:
    """`create_user(home_dir=...)` пробрасывает home в `_write_authorized_key`
    → Python-guard `_FORBIDDEN_HOMES` отбивает до bash-команды.

    Прежние тесты гоняли `_install_authorized_key(target_home=...)`
    напрямую. Здесь проверяем end-to-end: caller `create_user` с
    `home_dir=/var/empty` (нелегальный home) ловится guard'ом.
    """

    async def test_existing_user_with_forbidden_home_dir_rejected(self, monkeypatch):
        # Юзер есть, modify_user пустой (no opts), идём в
        # _write_authorized_key с target_home=/var/empty → SSH_INVALID_HOME.
        conn = make_conn([
            run_result("nobody:x:99:99::/var/empty:/sbin/nologin", "", 0),  # user_exists
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc:
                await ssh.create_user(
                    "nobody",
                    public_key=_PUBKEY,
                    home_dir="/var/empty",
                )

        assert exc.value.error_code == "SSH_INVALID_HOME"
        # bash-команда auth_keys не должна была дёрнуться — guard сработал
        # до SSH. Видим только user_exists.
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert not any("authorized_keys" in c for c in cmds)


# ══════════════════════════════════════════════════════════════════════════════
# GAP-15  _sudo_group_membership: id -nG rc!=0 → пустое множество
# ══════════════════════════════════════════════════════════════════════════════


class TestSudoGroupMembershipErrorPath:
    """`_sudo_group_membership` при rc!=0 возвращает пустое множество.

    Сценарий: пользователь исчез между `user_exists` и `id -nG`, либо
    NSS моментно отдала ошибку (sssd рестартует). Без guard'а функция
    падала бы на пустом stdout. Покрывает ветку ssh.py:879-880 (rc!=0
    early return).
    """

    async def test_id_ng_nonzero_returns_empty_set(self):
        ssh = _client_with_conn([
            run_result("", "id: 'dbos': no such user", 1),  # id -nG rc=1
        ])
        result = await ssh._sudo_group_membership("dbos")
        assert result == set()

    async def test_id_ng_zero_filters_to_sudo_wheel_only(self):
        # Happy path: rc=0, в выводе несколько групп — возвращаем
        # пересечение с {sudo, wheel}. Кросс-проверка к негативному
        # кейсу выше — общий контракт функции.
        ssh = _client_with_conn([
            run_result("dbos wheel docker adm\n", "", 0),
        ])
        result = await ssh._sudo_group_membership("dbos")
        assert result == {"wheel"}

    async def test_invalid_login_rejected_before_run(self):
        # _validate_login отбивает до conn.run — побочная защита от
        # подстановки в `id -nG <login>`.
        ssh = _client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._sudo_group_membership("bad login")
        assert exc.value.error_code == "SSH_INVALID_LOGIN"
        ssh._conn.run.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════════
# installed_packages.pattern audit-mask:
#   default — pattern не в whitelist; env=true — pattern в whitelist.
# ══════════════════════════════════════════════════════════════════════════════


class TestInstalledPackagesPatternAuditMask:
    """`installed_packages._audit_safe_fields()` уважает env-флаг.

    По умолчанию `pattern` в audit-whitelist'е НЕТ — оператор, запросивший
    `linux-image*`, не оставляет CVE-recon след в loging. Включение
    `AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG=true` расширяет whitelist
    осознанно — для корреляции с server_service-логом.
    """

    def test_default_whitelist_excludes_pattern(self, monkeypatch):
        from src.core.config import get_settings
        from src.tasks import installed_packages

        # lru_cache на get_settings: сбрасываем, чтобы env-перетряска подействовала.
        get_settings.cache_clear()
        monkeypatch.delenv("AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG", raising=False)

        fields = installed_packages._audit_safe_fields()
        assert "pattern" not in fields
        assert {"server_id", "count", "package_manager"} <= fields
        get_settings.cache_clear()

    def test_debug_env_flag_includes_pattern(self, monkeypatch):
        from src.core.config import get_settings
        from src.tasks import installed_packages

        get_settings.cache_clear()
        monkeypatch.setenv("AUDIT_INSTALLED_PACKAGES_PATTERN_DEBUG", "true")

        fields = installed_packages._audit_safe_fields()
        assert "pattern" in fields
        assert {"server_id", "count", "package_manager"} <= fields
        get_settings.cache_clear()

    def test_module_level_constant_stays_masked(self):
        """`AUDIT_SAFE_FIELDS` (backward-compat alias) — без `pattern`."""
        from src.tasks import installed_packages
        assert "pattern" not in installed_packages.AUDIT_SAFE_FIELDS
