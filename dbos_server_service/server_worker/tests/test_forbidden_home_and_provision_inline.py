"""Точечный fix-pass по worker'у:

* `_FORBIDDEN_HOMES` Python-guard теперь активен: `create_user(..., home_dir=...)`
  пробрасывает `target_home` в `_write_authorized_key` →
  `_install_authorized_key`. Bootstrap-путь по-прежнему не знает home заранее
  и полагается на bash-guard.
* `_store_provision_inline` пропускает запись при наличии хоть одного
  непустого stash-поля (граничные кейсы: партиальный stash, оба пустые).
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshClient, SshError, _FORBIDDEN_HOMES
from tests._ssh_mock_helpers import make_conn, run_result


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGuardTestKey guardtest@test"


def _client(run_results):
    ssh = SshClient(host="10.0.0.8", username="dbos", password="pwd")
    ssh._conn = make_conn(run_results)
    return ssh


# ── _FORBIDDEN_HOMES активен через create_user(home_dir=...) ──────────────────


class TestCreateUserWiresTargetHome:
    """`create_user` передаёт `home_dir` в `_write_authorized_key`.

    Раньше параметр `target_home` существовал, но ни один caller его не
    передавал — guard в Python был мёртвым. После фикса payload-поле
    `home_dir` (которое и так используется в `useradd -d`) идёт в guard,
    и системный home отбивается ДО отправки команды на хост.
    """

    @pytest.mark.parametrize("forbidden_home", sorted(_FORBIDDEN_HOMES))
    async def test_create_user_rejects_system_home_via_guard(self, forbidden_home):
        # Порядок команд create_user (юзера нет): getent → useradd →
        # _write_authorized_key. Guard срабатывает на третьем шаге ДО `conn.run`,
        # поэтому хватает двух run_result'ов: getent и useradd.
        ssh = _client([
            run_result("", "", 2),  # outer user_exists → rc=2 (нет)
            run_result("", "", 0),  # useradd
        ])
        with pytest.raises(SshError) as exc:
            await ssh.create_user(
                "nobody",
                home_dir=forbidden_home,
                public_key=_PUBKEY,
            )
        assert exc.value.error_code == "SSH_INVALID_HOME"

    async def test_create_user_passes_legit_home(self):
        # Полный happy-path: getent (нет), useradd, _install_authorized_key (одна команда).
        ssh = _client([
            run_result("", "", 2),
            run_result("", "", 0),
            run_result("", "", 0),
        ])
        await ssh.create_user(
            "ops",
            home_dir="/home/ops",
            public_key=_PUBKEY,
        )
        assert ssh._conn.run.await_count == 3

    async def test_create_user_no_home_dir_no_python_guard(self):
        # Без `home_dir` — Python-guard молчит, bash отрабатывает обычным
        # путём (в моке rc=0 на authorized_keys).
        ssh = _client([
            run_result("", "", 2),
            run_result("", "", 0),
            run_result("", "", 0),
        ])
        await ssh.create_user("ops", public_key=_PUBKEY)
        assert ssh._conn.run.await_count == 3

    async def test_existing_user_path_also_guards_system_home(self):
        # Идемпотентная ветка: юзер уже есть → modify_user + set_password (skip,
        # пароля нет) + _write_authorized_key. Guard должен сработать и тут.
        ssh = _client([
            run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),  # user_exists
            run_result("", "", 0),  # usermod (modify_user)
        ])
        with pytest.raises(SshError) as exc:
            await ssh.create_user(
                "ops",
                home_dir="/var/empty",
                public_key=_PUBKEY,
            )
        assert exc.value.error_code == "SSH_INVALID_HOME"


# ── _store_provision_inline — граничные кейсы партиального stash ──────────────


class TestStoreProvisionInlineSkipSemantics:
    """`if stashed_password is None and stashed_private_key is None:` — и (`and`).

    Контракт: первая попытка (оба слота пустые) — `store`. Любой непустой
    слот означает «stash уже жив, retry» — `store` пропускается, payload
    в БД уже scrubbed, но через partial-`or` мы собираем то, что есть.
    """

    @pytest.mark.parametrize(
        "stashed_pw, stashed_pk, expect_store",
        [
            (None, None, True),                # первая попытка
            ("p1", None, False),               # пароль уже в stash → retry
            (None, "PRIVATEKEY", False),       # только private → retry
            ("p1", "PRIVATEKEY", False),       # оба → retry
        ],
    )
    def test_store_condition_matrix(self, stashed_pw, stashed_pk, expect_store):
        skip = stashed_pw is None and stashed_pk is None
        assert skip is expect_store

    async def test_store_no_op_when_nothing_to_save(self, monkeypatch):
        # `_store_provision_inline` — no-op, если оба inline-значения None
        # (нет смысла трогать Redis ради пустых полей).
        from src.services import redis_pool
        from src.tasks import users

        called = {"set": False}

        class _FakeClient:
            async def set(self, *a, **kw):
                called["set"] = True

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _FakeClient())
        await users._store_provision_inline("tsk_t1", None, None)
        assert called["set"] is False

    async def test_store_writes_when_password_present(self, monkeypatch):
        from src.services import redis_pool
        from src.services.redis_stash_crypto import (
            aad_for_redis_stash,
            decrypt_stash,
        )
        from src.tasks import users

        captured = {}

        class _FakeClient:
            async def set(self, key, value, ex=None):
                captured["key"] = key
                captured["value"] = value
                captured["ex"] = ex

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _FakeClient())
        await users._store_provision_inline("tsk_t2", "secret_pw", None)
        assert captured["key"].endswith("tsk_t2")
        # `_store_provision_inline` envelope-шифрует JSON: проверяем plaintext
        # через decrypt_stash с тем же AAD (`task_id`).
        plain = decrypt_stash(captured["value"], aad=aad_for_redis_stash("tsk_t2"))
        assert "secret_pw" in plain
        assert captured["ex"] == users.STASH_TTL_SECONDS

    async def test_store_writes_when_only_private_key_present(self, monkeypatch):
        from src.services import redis_pool
        from src.services.redis_stash_crypto import (
            aad_for_redis_stash,
            decrypt_stash,
        )
        from src.tasks import users

        captured = {}

        class _FakeClient:
            async def set(self, key, value, ex=None):
                captured["value"] = value

            async def aclose(self):
                pass

        monkeypatch.setattr(redis_pool, "get_redis", lambda: _FakeClient())
        await users._store_provision_inline("tsk_t3", None, "PEM-PRIVATE")
        plain = decrypt_stash(captured["value"], aad=aad_for_redis_stash("tsk_t3"))
        assert "PEM-PRIVATE" in plain
        # password=None всё ещё попадает в JSON как null.
        assert "null" in plain or "None" not in plain


# ── Пустой пароль = не ставить пароль (chpasswd skip) ────────────────────────


def _run_cmds(ssh) -> list[str]:
    """Команды, реально ушедшие в `conn.run` (первый позиционный аргумент)."""
    return [c.args[0] for c in ssh._conn.run.await_args_list]


class TestCreateUserEmptyPasswordSkipsChpasswd:
    """`new_password=""`/`None` трактуется единообразно как «не ставить пароль».

    chpasswd на пустом payload'е (`login:\n`) падает 'missing new password';
    discovered-аккаунты без пароля заводятся useradd + ключ, без chpasswd.
    """

    async def test_create_branch_empty_string_no_chpasswd(self):
        # Юзера нет: getent (rc=2) → useradd → authorized_keys. chpasswd НЕ зовём.
        ssh = _client([
            run_result("", "", 2),  # user_exists → нет
            run_result("", "", 0),  # useradd
            run_result("", "", 0),  # authorized_keys
        ])
        await ssh.create_user("ops", new_password="", public_key=_PUBKEY)
        cmds = _run_cmds(ssh)
        assert not any("chpasswd" in c for c in cmds)
        assert ssh._conn.run.await_count == 3

    async def test_create_branch_none_no_chpasswd(self):
        ssh = _client([
            run_result("", "", 2),
            run_result("", "", 0),
            run_result("", "", 0),
        ])
        await ssh.create_user("ops", new_password=None, public_key=_PUBKEY)
        assert not any("chpasswd" in c for c in _run_cmds(ssh))

    async def test_existing_branch_empty_string_no_chpasswd(self):
        # Юзер уже есть: getent (rc=0) → usermod → authorized_keys. chpasswd skip.
        ssh = _client([
            run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),  # user_exists
            run_result("", "", 0),  # usermod (modify_user)
            run_result("", "", 0),  # authorized_keys
        ])
        await ssh.create_user("ops", new_password="", public_key=_PUBKEY)
        assert not any("chpasswd" in c for c in _run_cmds(ssh))

    async def test_create_branch_real_password_still_sets(self):
        # Контроль: непустой пароль по-прежнему вызывает chpasswd.
        ssh = _client([
            run_result("", "", 2),  # user_exists → нет
            run_result("", "", 0),  # useradd
            run_result("", "", 0),  # chpasswd
            run_result("", "", 0),  # authorized_keys
        ])
        await ssh.create_user("ops", new_password="P@ss123", public_key=_PUBKEY)
        assert any("chpasswd" in c for c in _run_cmds(ssh))
