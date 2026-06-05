"""Provision-dispatch генерит пароль + Ed25519-ключ, кладёт зашифрованным в БД
и отдаёт plaintext воркеру через Redis-stash.

Покрывает три кейса:
* NULL-аккаунт (discovered): генерим оба секрета, force_replace=True;
* существующий аккаунт с паролем и ключом: переиспользуем, force_replace=False;
* схема: ssh_public_key + ssh_private_key_encrypted колонки прочитаны после
  ensure_provision_credentials и читаются обратно через secrets_service.

Контракт W18-W1: plaintext password + ssh_private_key в payload больше НЕ
кладутся — server_service пишет их в Redis под `dbos:dispatch_creds:<id>`
с TTL, в payload едет только `creds_stash_key`. Тесты ниже читают
plaintext из stash'а через тот же ключ, что попал в payload.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.core.password_policy import is_strong
from src.models import ServerAccount
from src.services import secrets_service, worker_client

BASE = "/api/server/v1/server-accounts"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stub_redis(monkeypatch):
    """In-memory Redis-stub для pooled prepare/dispatch creds client."""
    storage: dict[str, tuple[str, int | None]] = {}
    pooled = MagicMock()

    async def fake_set(key, value, ex=None):
        storage[key] = (value, ex)

    async def fake_get(key):
        v = storage.get(key)
        return v[0].encode("utf-8") if v else None

    async def fake_delete(key):
        return 1 if storage.pop(key, None) is not None else 0

    pooled.set = AsyncMock(side_effect=fake_set)
    pooled.get = AsyncMock(side_effect=fake_get)
    pooled.delete = AsyncMock(side_effect=fake_delete)
    pooled.aclose = AsyncMock()
    monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

    class _Settings:
        server_worker_redis_url = "redis://test:6379/0"
        prepare_creds_ttl_seconds = 900
        dispatch_creds_ttl_seconds = 900

    monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())
    return storage


def _stash_creds(storage: dict, stash_key: str) -> dict:
    """Декодировать stash'ед creds для assertion-ов."""
    raw, _ttl = storage[stash_key]
    return json.loads(raw)


@pytest.fixture
def captured_dispatch(monkeypatch):
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def patch_dispatch(monkeypatch):
    """Подменяет `dispatch_task[_with_hit]` на бросок указанного исключения.

    Раньше четыре `TestRollback*` теста повторяли один и тот же блок из
    четырёх monkeypatch.setattr — теперь caller передаёт фабрику исключения,
    а патч идёт по всем точкам автоматически.

    Использование::

        patch_dispatch(lambda: ServiceUnavailableError(error_code="WORKER_UNREACHABLE",
                                                       message="redis down"))
    """

    def _apply(exc_factory):
        async def boom(*args, **kwargs):
            raise exc_factory()

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom)
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            boom,
        )

    return _apply


class TestProvisionGeneratesCredentials:
    async def test_null_account_generates_pwd_and_keypair(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        # Discovered-сценарий: аккаунт без пароля и без SSH-ключа.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        assert acc.password_encrypted is None
        assert acc.ssh_public_key is None
        assert acc.ssh_private_key_encrypted is None

        # Discovered-аккаунт без пароля требует явного force_password=true —
        # без него endpoint отбивает 422 (см. `TestProvisionForcePasswordGuard`).
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        # Plaintext-поля БОЛЬШЕ НЕ в payload — стэш в Redis.
        assert "password_plaintext" not in payload
        assert "ssh_private_key_plaintext" not in payload
        # ssh_public_key — не секрет, остаётся в payload.
        assert "ssh_public_key" in payload
        assert payload["force_replace"] is True
        stash_key = payload["creds_stash_key"]
        assert stash_key.startswith("dbos:dispatch_creds:")
        stash = _stash_creds(stub_redis, stash_key)
        # Пароль удовлетворяет усиленной политике.
        assert is_strong(stash["password_plaintext"])
        # Public-ключ — однострочный Ed25519.
        assert payload["ssh_public_key"].startswith("ssh-ed25519 ")
        assert "\n" not in payload["ssh_public_key"]
        # Private — OpenSSH PEM, лежит в Redis-stash.
        assert "OPENSSH PRIVATE KEY" in stash["ssh_private_key_plaintext"]

        # БД-строка обновлена ciphertext'ами, plaintext совпадает со stash'ем.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.password_encrypted is not None
        assert refreshed.ssh_public_key == payload["ssh_public_key"]
        assert refreshed.ssh_private_key_encrypted is not None
        decrypted_pwd = secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(acc.id),
        )
        assert decrypted_pwd == stash["password_plaintext"]
        decrypted_pk = secrets_service.decrypt(
            refreshed.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert decrypted_pk == stash["ssh_private_key_plaintext"]

    async def test_existing_credentials_reused_force_replace_false(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        # Симулируем предыдущий provision: ssh-ключ уже есть, password — тоже.
        from src.services import server_account as account_svc
        await account_svc.ensure_provision_credentials(db, acc)
        # ensure_provision_credentials выставляет pending_apply=True (creds
        # уехали на dispatch, callback ещё не пришёл). Симулируем приход
        # callback'а: на боксе всё применено, флаг снят — иначе следующий
        # provision увидит pending_apply=True и форснёт force_replace.
        acc.credentials_pending_apply = False
        await db.commit()
        await db.refresh(acc)
        stored_pubkey = acc.ssh_public_key
        stored_priv_cipher = acc.ssh_private_key_encrypted
        stored_pwd_cipher = acc.password_encrypted

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["force_replace"] is False
        assert payload["ssh_public_key"] == stored_pubkey
        stash = _stash_creds(stub_redis, payload["creds_stash_key"])
        # Plaintext-пароль декриптится из того же ciphertext'а.
        expected_pwd = secrets_service.decrypt(
            stored_pwd_cipher,
            aad=secrets_service.aad_for_server_account_password(acc.id),
        )
        assert stash["password_plaintext"] == expected_pwd

        # БД не перезаписана — те же ciphertext'ы.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.password_encrypted == stored_pwd_cipher
        assert refreshed.ssh_private_key_encrypted == stored_priv_cipher
        assert refreshed.ssh_public_key == stored_pubkey

    async def test_discovered_with_password_force_overwrites(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        """Discovered-аккаунт уже с сохранённым паролем + force_password=true →
        старые credentials сбрасываются, генерится свежая пара, force_replace=True.

        Раньше ensure_provision_credentials был sticky на каждый секрет в
        отдельности: discovered с паролем и без ключа всё равно получал
        force_replace=False (старый ciphertext оставался), и worker не делал
        chpasswd, хотя оператор просил overwrite. После фикса для discovered
        explicit force_password=true сбрасывает оба секрета и payload содержит
        свежий пароль + ключ + force_replace=True.
        """
        from src.core.constants import AccountSource
        from src.services import secrets_service

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="discovered_existing", password="legacy-pw")
        # Сделать discovered.
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        original_pwd_cipher = acc.password_encrypted
        assert original_pwd_cipher is not None
        original_plain = secrets_service.decrypt(
            original_pwd_cipher,
            aad=secrets_service.aad_for_server_account_password(acc.id),
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["force_replace"] is True
        assert "ssh_public_key" in payload
        # Plaintext в Redis-stash'е, не в payload.
        assert "password_plaintext" not in payload
        stash = _stash_creds(stub_redis, payload["creds_stash_key"])
        # Свежий пароль, не legacy.
        assert stash["password_plaintext"] != original_plain

        # БД переписана — новый ciphertext не совпадает со старым.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.password_encrypted != original_pwd_cipher

    async def test_managed_account_force_password_still_sticky(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        """Managed-аккаунт игнорирует force_password — credentials sticky, force_replace=False.

        Контракт force_password по-прежнему узкий: только discovered. Managed
        ходит через `/rotate` для смены пароля, через provision повторно — это
        переустановка ОС, и worker не должен трогать пароль управляемого
        аккаунта без отдельного rotate-вызова.
        """
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="managed_existing", password="managed-pw")
        original_cipher = acc.password_encrypted

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["force_replace"] is False

        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        # Managed-аккаунт не перезаписан.
        assert refreshed.password_encrypted == original_cipher

    async def test_update_on_host_does_not_inject_creds(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        # `update_on_host` — это usermod, без выдачи кред. Воркеру не нужны
        # ни пароль, ни ssh-ключ в payload'е.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/update_on_host?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert "password_plaintext" not in payload
        assert "ssh_public_key" not in payload
        assert "ssh_private_key_plaintext" not in payload
        assert "force_replace" not in payload


class TestProvisionDispatchFailureRollsBackCreds:
    """Креды генерятся ДО dispatch'а, но коммитятся только после успешной
    постановки task'а. Если worker недоступен или idempotent-конфликт —
    новые ciphertext'ы откатываются, БД остаётся в исходном состоянии.

    Без этого фикса аккаунт получал свежий ciphertext в server-БД, а worker
    задачу не получал → chpasswd на боксе не выполнялся, drift между
    server-БД и реальным сервером ломал SSH.
    """

    async def test_worker_unreachable_rolls_back_generated_creds(
        self, client, operator_token_a, make_server, make_account,
        stub_redis, db, patch_dispatch,
    ):
        from src.core.constants import AccountSource
        from src.core.exceptions import ServiceUnavailableError

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        assert acc.password_encrypted is None
        assert acc.ssh_public_key is None
        # Фиксируем id заранее — после rollback'а в endpoint'е объект expire'нут,
        # `acc.id` без greenlet-обёртки попадёт в sync-reload.
        acc_id = acc.id
        srv_id = srv.id

        patch_dispatch(lambda: ServiceUnavailableError(
            error_code="WORKER_UNREACHABLE", message="redis down",
        ))

        resp = await client.post(
            f"{BASE}/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503, resp.text

        # Свежий transactional read обязателен — текущая сессия откатилась,
        # внутри неё `acc` уже expire'нут. Берём фактическое состояние из БД.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one()
        assert refreshed.password_encrypted is None
        assert refreshed.ssh_public_key is None
        assert refreshed.ssh_private_key_encrypted is None

    async def test_idempotent_conflict_rolls_back_generated_creds(
        self, client, operator_token_a, make_server, make_account,
        stub_redis, db, patch_dispatch,
    ):
        from src.core.constants import AccountSource
        from src.core.exceptions import ConflictError

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        acc_id = acc.id
        srv_id = srv.id

        patch_dispatch(lambda: ConflictError(
            error_code="TASK_IDEMPOTENT_CONFLICT", message="duplicate",
        ))

        resp = await client.post(
            f"{BASE}/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409, resp.text

        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one()
        assert refreshed.password_encrypted is None
        assert refreshed.ssh_public_key is None
        assert refreshed.ssh_private_key_encrypted is None

    async def test_bare_exception_in_dispatch_rolls_back_creds(
        self, client, operator_token_a, make_server, make_account,
        stub_redis, db, patch_dispatch,
    ):
        """Любое исключение из dispatch_task (не только Conflict/ServiceUnavailable)
        должно откатывать creds-savepoint. Раньше try/except ловил только две
        конкретные ветки; неожиданная RuntimeError оставляла savepoint висеть
        до конца сессии.
        """
        from src.core.constants import AccountSource

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        assert acc.password_encrypted is None
        acc_id = acc.id
        srv_id = srv.id

        patch_dispatch(lambda: RuntimeError("unexpected wire-level failure"))

        with pytest.raises(RuntimeError):
            await client.post(
                f"{BASE}/{acc_id}/provision?server_id={srv_id}&force_password=true",
                headers=_hdr(operator_token_a),
            )

        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one()
        # Свежие creds, выкаченные ensure_provision_credentials, должны быть
        # откатаны savepoint.rollback() в finally.
        assert refreshed.password_encrypted is None
        assert refreshed.ssh_public_key is None
        assert refreshed.ssh_private_key_encrypted is None

    async def test_legacy_public_only_row_raises(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        """Legacy-row: public_key есть, private_key_encrypted — нет. ensure
        раньше отдавал worker'у пустой private_pem, который заливал
        authorized_keys без матчающего ключа. Теперь — DomainValidationError 422.
        """
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        # Симулируем legacy state вручную: pub есть, priv нет.
        acc.ssh_public_key = "ssh-ed25519 AAAA... legacy"
        acc.ssh_private_key_encrypted = None
        await db.flush()
        await db.commit()
        await db.refresh(acc)

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "ACCOUNT_SSH_KEY_INCONSISTENT"

    async def test_force_overwrite_rolls_back_on_dispatch_failure(
        self, client, operator_token_a, make_server, make_account,
        stub_redis, db, patch_dispatch,
    ):
        """Discovered + force_password=true сначала reset'ит существующие creds,
        потом ensure генерит свежие. Если dispatch падает — обе мутации откатываются,
        старый ciphertext возвращается в БД.
        """
        from src.core.constants import AccountSource
        from src.core.exceptions import ServiceUnavailableError

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="legacy-pw")
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        original_cipher = acc.password_encrypted
        assert original_cipher is not None
        acc_id = acc.id
        srv_id = srv.id

        patch_dispatch(lambda: ServiceUnavailableError(
            error_code="WORKER_UNREACHABLE", message="redis down",
        ))

        resp = await client.post(
            f"{BASE}/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503, resp.text

        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one()
        # Reset+ensure откатились — старый ciphertext на месте.
        assert refreshed.password_encrypted == original_cipher
