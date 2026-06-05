"""Plaintext password + ssh_private_key больше не попадают в task-payload.

Раньше `_dispatch_account_on_host` (`worker_dispatch.py`) при
`inject_provision_creds=True` клал `password_plaintext` и
`ssh_private_key_plaintext` прямо в payload taskiq-задачи →
`dev_server_worker.tasks.payload` (JSONB) хранил plaintext до retention
cleanup'а. Любой с read к worker-БД видел пароль.

Фикс: server_service кладёт креды в Redis под одноразовый
`dbos:dispatch_creds:<dcd_id>` с TTL, в payload едет только
`creds_stash_key`. Воркер читает по ссылке, DEL'ит. Симметрия с
`server.prepare`.

Тесты:
* payload не содержит plaintext-полей; присутствует `creds_stash_key`;
* при failure dispatch'а (`ConflictError`/`ServiceUnavailableError`)
  stash в Redis удаляется (явный DEL, не ждём TTL);
* при failure `store_dispatch_creds` (Redis недоступен) на dispatch
  даже не идём, audit-failure эмитим, БД-creds откачены через savepoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.services import worker_client

BASE = "/api/server/v1/server-accounts"


from tests._helpers import assert_error, auth_hdr as _hdr, make_emit_capture  # noqa: E402


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    )


@pytest.fixture
def stub_redis(monkeypatch):
    """In-memory Redis-stub: pooled client, поддерживает set/get/delete."""
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


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехватчик `worker_client.dispatch_task` — сохраняет kwargs первого вызова."""
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return_hit = kwargs.get("return_hit", False)
        new_id = "tsk_w18_test"
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


@pytest.mark.asyncio
class TestProvisionCredsRedisStash:
    """`account.provision` payload не содержит plaintext, едет `creds_stash_key`."""

    async def test_payload_contains_stash_key_not_plaintext(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        captured_dispatch,
        stub_redis,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        assert len(captured_dispatch) == 1
        kw = captured_dispatch[0]
        payload = kw["payload"]
        assert "password_plaintext" not in payload, payload
        assert "ssh_private_key_plaintext" not in payload, payload
        # ssh_public_key — не секрет, остаётся в payload
        assert payload.get("ssh_public_key")
        # ссылка на stash
        stash_key = payload.get("creds_stash_key")
        assert isinstance(stash_key, str)
        assert stash_key.startswith("dbos:dispatch_creds:")

    async def test_stash_in_redis_has_creds_and_ttl(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        captured_dispatch,
        stub_redis,
    ):
        """Redis-stash содержит password+private_key с TTL=dispatch_creds_ttl_seconds."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        stash_key = captured_dispatch[0]["payload"]["creds_stash_key"]
        # Successful dispatch — stash остаётся в Redis для воркера
        assert stash_key in stub_redis
        raw, ttl = stub_redis[stash_key]
        assert ttl == 900
        import json
        data = json.loads(raw)
        assert data.get("password_plaintext")
        assert data.get("ssh_private_key_plaintext")

    async def test_stash_deleted_on_dispatch_conflict(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        stub_redis,
        monkeypatch,
    ):
        """ConflictError в dispatch_task → stash удалён, plaintext не висит до TTL."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)

        async def boom_dispatch(**kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT",
                message="conflict",
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
        # Stash подчищен — в storage не должно быть ключей с этим префиксом
        leftover = [k for k in stub_redis if k.startswith("dbos:dispatch_creds:")]
        assert leftover == [], leftover

    async def test_stash_deleted_on_worker_unreachable(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        stub_redis,
        monkeypatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)

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
        assert resp.status_code == 503, resp.text
        leftover = [k for k in stub_redis if k.startswith("dbos:dispatch_creds:")]
        assert leftover == [], leftover

    async def test_store_creds_failure_emits_audit_and_returns_503(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        captured_dispatch,
        monkeypatch,
    ):
        """Если Redis-pool недоступен на этапе stash'а — 503, dispatch не зовётся."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)

        # Pool отсутствует, URL пустой → store_dispatch_creds поднимет
        # WORKER_REDIS_NOT_CONFIGURED.
        monkeypatch.setattr(worker_client, "_prepare_redis_client", None)

        class _Settings:
            server_worker_redis_url = ""
            prepare_creds_ttl_seconds = 900
            dispatch_creds_ttl_seconds = 900

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503, resp.text
        # dispatch_task не должен был дёрнуться
        assert captured_dispatch == []
        # failure audit с reason=creds_store_unavailable
        creds_failures = [
            e for e in captured_emits
            if e["action"] == "server_account.provision"
            and e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "creds_store_unavailable"
        ]
        assert len(creds_failures) == 1, captured_emits


@pytest.mark.asyncio
class TestDispatchCredsHelpers:
    """Тонкие helpers `dispatch_creds_key` / `store_dispatch_creds` / `delete_dispatch_creds`."""

    async def test_dispatch_creds_key_format(self):
        key = worker_client.dispatch_creds_key("dcd_abc123")
        assert key == "dbos:dispatch_creds:dcd_abc123"

    async def test_store_dispatch_creds_no_url_raises(self, monkeypatch):
        monkeypatch.setattr(worker_client, "_prepare_redis_client", None)

        class _Settings:
            server_worker_redis_url = ""
            dispatch_creds_ttl_seconds = 900
            prepare_creds_ttl_seconds = 900

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        with pytest.raises(ServiceUnavailableError) as exc:
            await worker_client.store_dispatch_creds(
                "dbos:dispatch_creds:dcd_x",
                {"password_plaintext": "p", "ssh_private_key_plaintext": "k"},
            )
        assert exc.value.error_code == "WORKER_REDIS_NOT_CONFIGURED"

    async def test_store_dispatch_creds_pooled_uses_ttl(self, monkeypatch):
        pooled = MagicMock()
        pooled.set = AsyncMock()
        monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

        class _Settings:
            server_worker_redis_url = "redis://test:6379/0"
            dispatch_creds_ttl_seconds = 1234
            prepare_creds_ttl_seconds = 900

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        await worker_client.store_dispatch_creds(
            "dbos:dispatch_creds:dcd_ttl",
            {"password_plaintext": "p", "ssh_private_key_plaintext": "k"},
        )
        # ex= должно быть dispatch_creds_ttl_seconds, не prepare_creds_ttl_seconds
        pooled.set.assert_awaited_once()
        call_kwargs = pooled.set.call_args
        assert call_kwargs.kwargs.get("ex") == 1234 or call_kwargs.args[2:] == (1234,)

    async def test_delete_dispatch_creds_swallows_errors(self, monkeypatch):
        """Best-effort: ошибки DEL не пропускаются наружу."""
        pooled = MagicMock()
        pooled.delete = AsyncMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

        class _Settings:
            server_worker_redis_url = "redis://test:6379/0"
            dispatch_creds_ttl_seconds = 900
            prepare_creds_ttl_seconds = 900

        monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())

        # Не должно бросить
        await worker_client.delete_dispatch_creds("dbos:dispatch_creds:dcd_err")

    async def test_redaction_masks_creds_stash_key(self):
        """`creds_stash_key` маскируется как `<SECRET>` defense-in-depth."""
        from src.services.redaction import redact
        result = redact({
            "creds_stash_key": "dbos:dispatch_creds:dcd_abc",
            "bootstrap_creds_key": "dbos:prepare_creds:pcd_def",
            "other": "ok",
        })
        assert result["creds_stash_key"] == "<SECRET>"
        assert result["bootstrap_creds_key"] == "<SECRET>"
        assert result["other"] == "ok"
