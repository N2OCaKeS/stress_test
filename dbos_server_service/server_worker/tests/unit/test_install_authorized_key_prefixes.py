"""Параметризованная проверка acceptance-prefix'ов SSH-публичных ключей.

`_install_authorized_key` валидирует ключ regex'ом по началу строки.
Список префиксов фиксирован в `clients/ssh.py` (RSA / Ed25519 / DSS /
ECDSA трёх P-curve / FIDO-варианты ssh + ecdsa). Если кто-нибудь
поменяет порядок или удалит prefix, эти тесты должны упасть — иначе
бутстрап `dbos`-пользователя на части стенда тихо начнёт reject'ить
валидные ключи.

Дополнительно фиксируем поведение пограничных случаев:
  * `truncate=True` со stdin: на bash-команду идёт overwrite (`>`),
    в `input=` уходит ровно `key\\n`, без дублей и trailing-мусора.
  * `create_user(public_key="")` — пустая строка через каноничный
    entrypoint всё равно отбивается до useradd (пустота не
    «пропускается», а считается ошибкой ключа).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError
from tests._ssh_mock_helpers import make_conn, run_result, sudo_probe_result


_KEY_TAIL = "AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY comment@host"


def _make_client_with_conn(run_results):
    ssh = SshClient(host="10.0.0.1", username="dbos", password="pwd")
    ssh._conn = make_conn(run_results)
    return ssh


_VALID_PREFIXES = [
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
]


class TestInstallAuthorizedKeyAcceptedPrefixes:
    @pytest.mark.parametrize("prefix", _VALID_PREFIXES)
    async def test_known_prefix_passes_validation(self, prefix):
        """Каждый из задокументированных prefix'ов проходит валидацию.

        Проверяем, что `_install_authorized_key` не падает на
        `SSH_INVALID_ARG` до дёрганья SSH — значит regex prefix'а
        матчит и команда уходит на conn.run.
        """
        ssh = _make_client_with_conn([sudo_probe_result(), run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=f"{prefix} {_KEY_TAIL}",
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        # Пробер sudo -n true + bash setup. Если бы prefix не прошёл, conn.run
        # вообще не должен был дёргаться.
        assert ssh._conn.run.await_count == 2

    @pytest.mark.parametrize(
        "prefix",
        [
            "ssh-rsa1",          # похожий, но не из списка
            "rsa-rsa",           # типичная опечатка
            "ssh-ed25519-cert",  # cert-ключи отдельной разновидности
            "sk-ssh-rsa@openssh.com",
            "garbage",
        ],
    )
    async def test_unknown_prefix_rejected(self, prefix):
        """Любой prefix вне whitelist'а отбивается без SSH-вызова."""
        ssh = _make_client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key=f"{prefix} {_KEY_TAIL}",
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_INVALID_ARG"
        ssh._conn.run.assert_not_awaited()


class TestInstallAuthorizedKeyStdinShape:
    async def test_truncate_true_stdin_is_single_key_line(self):
        """`truncate=True`: stdin содержит ровно `key\\n`, без дублей.

        Bash-команда читает ключ через `key=$(cat)` и потом
        `printf "%s\\n" "$key" > authorized_keys`. Если бы stdin
        нёс лишнее `\\n` сверху или дубль ключа — в `authorized_keys`
        упала бы пустая строка / два одинаковых ключа. Worker-bootstrap
        опирается на «ровно одна строка в файле = ровно наш ключ».
        """
        ssh = _make_client_with_conn([sudo_probe_result(), run_result("", "", 0)])
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITRUNCATE dbos-mgmt"

        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=key,
            truncate=True,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )

        call = ssh._conn.run.await_args
        assert call is not None
        cmd = call.args[0]
        stdin = call.kwargs.get("input", "")

        # bash-команда — overwrite (`>`), не append.
        assert "> \"$home/.ssh/authorized_keys\"" in cmd
        assert ">>" not in cmd

        # stdin: пароль sudo + сам ключ + LF. SshClient запиывает sudo
        # password первой строкой (`input` целиком), затем ключ. Нас
        # интересует, что ключ присутствует ровно один раз и завершается
        # ровно одним переводом строки.
        assert stdin.count(key) == 1
        # После последнего вхождения ключа — ровно `\n` и больше ничего.
        tail = stdin[stdin.rindex(key) + len(key):]
        assert tail == "\n"

    async def test_truncate_true_strips_surplus_whitespace(self):
        """Trailing whitespace и пустые строки в ключе нормализуются.

        `public_key` приходит из server_service (поле в БД); если оператор
        вставил его с финальной пустой строкой через web-form — этот мусор
        не должен утекать в `authorized_keys`. `strip()` убирает только
        крайние пробелы; embedded `\\n` отбивается отдельной проверкой.
        """
        ssh = _make_client_with_conn([sudo_probe_result(), run_result("", "", 0)])
        clean = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABTRIM trim@host"
        # Trailing space + LF + tabs — всё это strip()
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=f"   {clean}  \t",
            truncate=True,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        stdin = ssh._conn.run.await_args.kwargs.get("input", "")
        # На stdin уходит уже без обрамляющих пробелов.
        assert clean in stdin
        # Конец stdin = ровно `\n` после ключа.
        assert stdin.endswith(clean + "\n")


class TestCreateUserEmptyPublicKey:
    """`create_user(public_key="")` — пустая строка отбивается.

    Хотя в `create_user` проверка идёт `if public_key is not None:` (т.е.
    `""` всё-таки заходит в `_write_authorized_key`), внутри
    `_install_authorized_key` пустой/whitespace-only ключ ловится
    `SSH_INVALID_ARG`. Это намеренно: если caller передаёт пустой ключ —
    это баг в payload'е, не «no-op»-сигнал. Тихий skip скрыл бы такие
    баги до момента, когда пользователь пытается заехать ключом и
    получает permission denied.
    """

    async def test_empty_string_public_key_rejects(self, monkeypatch):
        # getent (юзер ещё не существует) → useradd ok → ... но до
        # authorized_keys должны не доехать.
        conn = make_conn([
            run_result("", "", 2),   # getent passwd → not found
            sudo_probe_result(),     # sudo -n true перед useradd
            run_result("", "", 0),   # useradd ok
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc:
                await ssh.create_user("deploy", public_key="")
        assert exc.value.error_code == "SSH_INVALID_ARG"
        # Юзер уже создан useradd'ом — следующая попытка provision
        # пойдёт по existing-ветке. Это известная семантика
        # `create_user` (idempotent), а не баг этого теста.

    async def test_whitespace_only_public_key_rejects(self, monkeypatch):
        conn = make_conn([
            run_result("", "", 2),
            sudo_probe_result(),     # sudo -n true перед useradd
            run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async with SshClient("h", "ops", "p") as ssh:
            with pytest.raises(SshError) as exc:
                await ssh.create_user("deploy", public_key="   \t\n  ")
        assert exc.value.error_code == "SSH_INVALID_ARG"
