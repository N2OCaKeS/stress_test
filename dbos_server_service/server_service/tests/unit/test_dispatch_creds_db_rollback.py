"""Race: commit creds в БД до успешного dispatch'а → drift со штатом коробки.

Раньше `_dispatch_account_on_host` (`worker_dispatch.py`) шёл:
  1. generate P_new (`reset_provision_credentials` + `ensure_provision_credentials`);
  2. `await db.commit()` ← creds зафиксированы в server-БД;
  3. `worker_client.dispatch_task(...)` ← мог упасть с
     `TASK_IDEMPOTENT_CONFLICT`, `WORKER_UNREACHABLE`, network drop.

Retry без `force_password=true` видел уже-сохранённый `P_new`,
`had_password_before=True` → `force_replace=False` → worker НЕ chpasswd'ил
коробку. БД с `P_new`, коробка с `P_old`, out-of-band доступ потерян.

Фикс: `creds_sp = await db.begin_nested()`. Reset/ensure
выполняются внутри savepoint'а, `dispatch_task` тоже под ним; на любом
исключении из dispatch'а — `creds_sp.rollback()` (через `finally` гарантия
покрытия и `Conflict/ServiceUnavailable`, и любых других исключений).
`creds_sp.commit()` + `db.commit()` только если `dispatch_ok=True`.

Также: на rollback'е стираем dispatch-stash из Redis, чтобы plaintext
не висел до TTL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.services import worker_client

BASE = "/api/server/v1/server-accounts"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def stub_redis(monkeypatch):
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


@pytest.mark.asyncio
class TestDispatchRollbackKeepsDbCredsIntact:
    async def test_worker_unreachable_rollbacks_db_creds(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        stub_redis,
        monkeypatch,
        db,
    ):
        """`WorkerUnreachable` после store_dispatch_creds → creds в БД не изменились,
        stash в Redis удалён."""
        srv = await make_server(department_id="dep_a")
        # Discovered-аккаунт без пароля — `ensure_provision_credentials`
        # сгенерит password+ssh-keypair и попробует залить в БД.
        from src.core.constants import AccountSource
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        # Сделаем DISCOVERED, чтобы force_password сработал
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()
        await db.refresh(acc)

        # Snapshot ДО запроса
        password_before = acc.password_encrypted
        ssh_pub_before = acc.ssh_public_key
        ssh_priv_before = acc.ssh_private_key_encrypted

        async def boom_dispatch(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="broker down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom_dispatch)
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom_dispatch)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom_dispatch,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            boom_dispatch,
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503

        # Перечитываем из БД через свежий SELECT — savepoint должен был
        # откатить mutations в acc.
        from src.models import ServerAccount
        await db.refresh(acc)
        fresh = acc
        _ = ServerAccount  # noqa: F841 — keep import for symmetry
        assert fresh.password_encrypted == password_before
        assert fresh.ssh_public_key == ssh_pub_before
        assert fresh.ssh_private_key_encrypted == ssh_priv_before

        # Stash тоже удалён
        leftover = [k for k in stub_redis if k.startswith("dbos:dispatch_creds:")]
        assert leftover == []

    async def test_conflict_rollbacks_db_creds(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        stub_redis,
        monkeypatch,
        db,
    ):
        """`TASK_IDEMPOTENT_CONFLICT` → creds в БД не изменились."""
        srv = await make_server(department_id="dep_a")
        from src.core.constants import AccountSource
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()
        await db.refresh(acc)

        password_before = acc.password_encrypted

        async def boom_dispatch(**kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT",
                message="duplicate task",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom_dispatch)
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom_dispatch)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom_dispatch,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            boom_dispatch,
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "TASK_IDEMPOTENT_CONFLICT")

        from src.models import ServerAccount
        await db.refresh(acc)
        fresh = acc
        _ = ServerAccount  # noqa: F841 — keep import for symmetry
        assert fresh.password_encrypted == password_before

        leftover = [k for k in stub_redis if k.startswith("dbos:dispatch_creds:")]
        assert leftover == []

    async def test_success_commits_db_creds(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        stub_redis,
        monkeypatch,
        db,
    ):
        """Happy path: dispatch_ok=True → creds в БД сохранены."""
        srv = await make_server(department_id="dep_a")
        from src.core.constants import AccountSource
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()
        await db.refresh(acc)

        async def fake_dispatch(**kwargs):
            return "tsk_happy"

        async def fake_dispatch_with_hit(**kwargs):
            return ("tsk_happy", False)

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

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202

        from src.models import ServerAccount
        await db.refresh(acc)
        fresh = acc
        _ = ServerAccount  # noqa: F841 — keep import for symmetry
        # Password сгенерирован и сохранён
        assert fresh.password_encrypted is not None
        assert fresh.ssh_public_key is not None
        assert fresh.ssh_private_key_encrypted is not None
