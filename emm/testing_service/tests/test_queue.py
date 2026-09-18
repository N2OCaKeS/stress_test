"""Тесты очереди (§2.4, §5, §5.5 плана миграции).

`enqueue()` вызывается напрямую из сервисного слоя (нет публичного HTTP-входа
в этой волне — см. отчёт волны), остальное — через internal-эндпоинты
(callback prepare-for-test, claim/completed для testing_worker).

`server_client`-вызовы в server_service мокаются тем же MockTransport-приёмом,
что и в `test_test_stands_crud.py`. Redis для Redis-стэша кред — настоящий
(поднят `tests/docker-compose.test.yml`), отдельно мокать незачем.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from src.core.constants import QueueItemState
from src.dependencies.auth import Identity
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import department_test_settings as dts_repo
from src.repositories import queue_item as queue_repo
from src.services import queue as queue_svc
from src.services import secret_client, server_client
from src.utils.ids import department_integration_settings_id, department_test_settings_id
from tests.conftest import auth_hdr as _hdr

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"
STANDS_BASE = "/api/testing/v1/test-stands"
CALLBACK_BASE = "/internal/prepare-for-test"
QUEUE_BASE = "/internal/queue"

SERVER_SECRET = "test-server-service-callback-secret"
WORKER_SECRET = "test-testing-worker-secret"


class _StubSettings:
    server_service_url = "http://server-service"
    server_service_api_key = "dbos_bot_test"
    server_service_internal_api_key = "internal-test-key"
    server_request_timeout_seconds = 2.0


@pytest.fixture
def recorded_calls():
    return []


@pytest.fixture
def mock_server_service(monkeypatch, recorded_calls):
    """Подменяет транспорт исходящих вызовов в server_service.

    `overrides` — dict `{"acquire": 200|409, "service_status": 200|409,
    "prepare": 202, "release": 200, "connection": 200}`, по умолчанию всё
    успешно. `department_id`/`host` настраиваются отдельно.
    """
    monkeypatch.setattr(server_client, "get_settings", lambda: _StubSettings())

    def _install(*, department_id="dep_a", host="10.0.0.5", overrides=None, acquire_fail_times=0):
        overrides = overrides or {}
        acquire_calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_calls.append((request.method, request.url.path))
            path = request.url.path
            if request.method == "GET" and "/internal/" not in path and path.startswith("/api/server/v1/servers/"):
                server_id = path.rsplit("/", 1)[-1]
                return httpx.Response(200, json={
                    "id": server_id, "hostname": f"host-{server_id}", "department_id": department_id,
                })
            if request.method == "POST" and path.endswith("/acquire-for-service"):
                acquire_calls["count"] += 1
                status = overrides.get("acquire", 200)
                if status == 409 or acquire_calls["count"] <= acquire_fail_times:
                    return httpx.Response(409, json={
                        "error_code": "SERVER_ALREADY_BUSY", "message": "server already busy",
                    })
                return httpx.Response(status, json={
                    "server_id": "srv_x", "busy_state": "acs", "busy_actor_type": "service",
                    "busy_service_name": "testing_service", "busy_note": None, "busy_since": None,
                })
            if request.method == "POST" and path.endswith("/service-status"):
                status = overrides.get("service_status", 200)
                if status == 409:
                    return httpx.Response(409, json={
                        "error_code": overrides.get("service_status_error", "SERVER_NOT_BUSY"),
                        "message": "conflict",
                    })
                return httpx.Response(status, json={
                    "server_id": "srv_x", "busy_state": "acs", "busy_actor_type": "service",
                    "busy_service_name": "testing_service", "busy_note": None, "busy_since": None,
                })
            if request.method == "POST" and path.endswith("/release-for-service"):
                status = overrides.get("release", 200)
                return httpx.Response(status, json={
                    "server_id": "srv_x", "busy_state": "free", "busy_actor_type": "user",
                    "busy_service_name": None, "busy_note": None, "busy_since": None,
                })
            if request.method == "POST" and path.endswith("/prepare-for-test"):
                status = overrides.get("prepare", 202)
                return httpx.Response(status, json={
                    "prepare_request_id": f"prep_{uuid.uuid4().hex[:10]}", "status": "in_progress",
                })
            if request.method == "GET" and path.endswith("/connection-info"):
                status = overrides.get("connection", 200)
                return httpx.Response(status, json={"server_id": "srv_x", "host": host})
            return httpx.Response(404, json={})

        def _build(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(server_client, "build_client", _build)

    return _install


@pytest.fixture
def configure_internal_keys(monkeypatch):
    from src.core.config import get_settings as _get_settings

    monkeypatch.setenv(
        "SERVICE_API_KEYS",
        f"server_service:{SERVER_SECRET},testing_worker:{WORKER_SECRET}",
    )
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    _get_settings.cache_clear()  # type: ignore[attr-defined]


def _server_hdr(identity: str, secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "X-Service-Identity": identity}


async def _create_stand(
    client, admin_token, department_id="dep_a", *, legacy_token: str | None = None,
) -> tuple[str, str]:
    server_id = f"srv_{uuid.uuid4().hex[:10]}"
    payload: dict = {"server_id": server_id}
    if legacy_token is not None:
        payload["legacy_token"] = legacy_token
    resp = await client.post(STANDS_BASE, headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], server_id


async def _create_test_def(
    client, admin_token, pinned_stand_id: str | None, *,
    with_sensitive_arg: bool = False, mode: str = "orel", starter_suffix: str | None = None,
) -> str:
    payload = {
        "code": f"queue.test.{uuid.uuid4().hex[:8]}",
        "full_name": "Тест очереди",
        "readiness": "ready",
        "mode": mode,
    }
    if pinned_stand_id is not None:
        payload["pinned_stand_id"] = pinned_stand_id
    if starter_suffix is not None:
        payload["starter_suffix"] = starter_suffix
    resp = await client.post(TESTS_BASE, headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    test_id = resp.json()["id"]
    arg = await client.post(
        f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
        json={"kind": "literal", "literal_value": "--run"},
    )
    assert arg.status_code == 201, arg.text

    if with_sensitive_arg:
        var_resp = await client.get(
            f"{VARS_BASE}/by-code/TEST_PASSWORD", headers=_hdr(admin_token),
        )
        assert var_resp.status_code == 200, var_resp.text
        password_arg = await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": var_resp.json()["id"], "override_value": "s3cr3t"},
        )
        assert password_arg.status_code == 201, password_arg.text

    return test_id


def _identity(department_id="dep_a", user_id="usr_queue_test") -> Identity:
    return Identity(
        user_id=user_id, username="tester", actor_type="user",
        department_id=department_id, allowed_services=["testing_service"],
        service_roles={"testing_service": ["admin"]}, is_banned=False, platform_role=None,
    )


LAUNCH_CTX = {"RC": "1.8.5", "KERNEL": "6.1.0", "MODE": "orel"}


async def _get_item(item_id: str):
    async with AsyncSessionLocal() as db:
        return await queue_repo.get_by_id(db, item_id)


@pytest.fixture
def mock_git_token(monkeypatch):
    """Настраивает git-credential отдела + мокает `secret_client.reveal_credential`.

    `claim_next` собирает значение заголовка `Authorization` для `starter.sh` из
    `department_integration_settings.git_credential_id` (фолбэк —
    `bitbucket_credential_id`); без него item проваливается ещё до того, как
    воркер увидит команду (см. `services/queue.py::_resolve_git_token`).
    """
    async def fake_reveal(cred_id: str):
        return ("git-bot", "git-token-value")

    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)

    async def _install(department_id: str = "dep_a", **fields) -> None:
        changes = fields or {"git_credential_id": "cred_git_header"}
        async with AsyncSessionLocal() as db:
            existing = await dis_repo.get_by_department(db, department_id)
            if existing is None:
                await dis_repo.create(db, {
                    "id": department_integration_settings_id(),
                    "department_id": department_id,
                    **changes,
                })
            else:
                await dis_repo.update(db, existing, changes)
            await db.commit()

    return _install


class TestEnqueue:
    async def test_first_item_triggers_prepare_cycle(self, client, admin_token, mock_server_service, recorded_calls):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX,
            )

        assert item.state == QueueItemState.PREPARING
        assert item.position == 0
        assert item.prepare_request_id is not None
        methods_paths = [p for _, p in recorded_calls]
        assert any(p.endswith("/acquire-for-service") for p in methods_paths)
        assert any(p.endswith("/prepare-for-test") for p in methods_paths)

    async def test_second_item_queued_without_triggering_cycle(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            first = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        recorded_calls.clear()
        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert second.state == QueueItemState.QUEUED
        assert second.position == 1
        assert recorded_calls == []
        assert first.id != second.id

    async def test_prepare_only_flag_is_persisted(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True,
            )

        assert item.prepare_only is True
        stored = await _get_item(item.id)
        assert stored.prepare_only is True

    async def test_prepare_only_defaults_to_false(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.prepare_only is False

    async def test_missing_launch_context_key_rejected(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        from src.core.exceptions import DomainValidationError

        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError):
                await queue_svc.enqueue(db, _identity(), test_id, launch_context={"RC": "1.8.5"})

    async def test_debug_mode_requires_stand_id(self, client, admin_token, mock_server_service):
        mock_server_service()
        test_id = await _create_test_def(client, admin_token, None)
        from src.core.exceptions import DomainValidationError

        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError):
                await queue_svc.enqueue(
                    db, _identity(), test_id, launch_context=LAUNCH_CTX, debug_mode=True,
                )

    async def test_non_debug_requires_pinned_stand(self, client, admin_token, mock_server_service):
        mock_server_service()
        test_id = await _create_test_def(client, admin_token, None)
        from src.core.exceptions import DomainValidationError

        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError):
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

    async def test_acquire_failure_creates_retry_that_recovers(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        # Первый acquire-for-service падает, второй (уже для retry-item'а)
        # проходит — показывает, что retry реально переигрывает цикл, а не
        # просто заводит вторую строку.
        mock_server_service(acquire_fail_times=1)
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.FAILED
        assert item.is_retry is False

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            stmt = select(QueueItem).where(QueueItem.retry_of_id == item.id)
            retry = (await db.execute(stmt)).scalar_one()
        assert retry.is_retry is True
        assert retry.state == QueueItemState.PREPARING
        assert retry.prepare_request_id is not None

    async def test_acquire_failure_retry_keeps_prepare_only(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        # Автосозданный retry обязан унаследовать prepare_only от исходного
        # item'а — иначе провалившийся testenv-прогон незаметно превратится
        # в обычный запуск теста после автоматического retry.
        mock_server_service(acquire_fail_times=1)
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True,
            )

        assert item.state == QueueItemState.FAILED
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            retry = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == item.id)
            )).scalar_one()
        assert retry.prepare_only is True

    async def test_acquire_failure_terminal_when_retry_also_fails(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service(overrides={"acquire": 409})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.FAILED
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            stmt = select(QueueItem).where(QueueItem.retry_of_id == item.id)
            retry = (await db.execute(stmt)).scalar_one()
        assert retry.is_retry is True
        assert retry.state == QueueItemState.FAILED
        # Второй провал (уже retry) не создаёт внука.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            grandchild = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == retry.id)
            )).scalar_one_or_none()
        assert grandchild is None

    async def test_acquire_failure_no_retry_when_disabled(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service(overrides={"acquire": 409})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await dts_repo.create(db, {
                "id": department_test_settings_id(),
                "department_id": "dep_a",
                "retry_enabled": False,
                "test_username": "u",
                "activity_report_auto_generate": False,
            })
            await db.commit()

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.FAILED
        async with AsyncSessionLocal() as db:
            retry = await queue_repo.get_next_queued_for_stand(db, stand_id)
        assert retry is None
        # Бронь никогда не бралась (acquire упал первым же вызовом) — release
        # не должен был вызываться.
        assert not any(p.endswith("/release-for-service") for _, p in recorded_calls)


class TestPrepareCallback:
    async def test_wrong_identity_rejected(self, client, configure_internal_keys):
        resp = await client.post(
            f"{CALLBACK_BASE}/prep_whatever/completed",
            headers=_server_hdr("acs", SERVER_SECRET),
            json={"correlation_id": "qi_x", "succeeded": True},
        )
        assert resp.status_code == 401, resp.text

    async def test_unknown_prepare_request_id_404(self, client, configure_internal_keys):
        resp = await client.post(
            f"{CALLBACK_BASE}/prep_does_not_exist/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": "qi_x", "succeeded": True},
        )
        assert resp.status_code == 404, resp.text

    async def test_success_stashes_creds_and_marks_ready(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.PREPARING

        resp = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----BEGIN KEY-----",
            },
        )
        assert resp.status_code == 200, resp.text

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.READY
        assert updated.creds_stash_key is not None

        # Повторный (дублирующийся) callback — идемпотентный no-op.
        resp2 = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True},
        )
        assert resp2.status_code == 200, resp2.text
        still = await _get_item(item.id)
        assert still.state == QueueItemState.READY

    async def test_failure_creates_retry_then_terminal_on_second_failure(
        self, client, admin_token, mock_server_service, configure_internal_keys, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        resp = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": False,
                "failed_step": "restore", "error": "disk full",
            },
        )
        assert resp.status_code == 200, resp.text

        original = await _get_item(item.id)
        assert original.state == QueueItemState.FAILED
        assert original.failed_step == "restore"

        # Достаём retry по retry_of_id напрямую.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            stmt = select(QueueItem).where(QueueItem.retry_of_id == item.id)
            retry = (await db.execute(stmt)).scalar_one()
        assert retry.is_retry is True
        assert retry.state == QueueItemState.PREPARING
        assert retry.prepare_request_id is not None
        assert retry.prepare_request_id != item.prepare_request_id

        # Второй провал (уже retry) — терминально, без ещё одного retry, и
        # бронь снимается (это был последний активный item очереди стенда).
        resp2 = await client.post(
            f"{CALLBACK_BASE}/{retry.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": retry.id, "succeeded": False, "error": "again"},
        )
        assert resp2.status_code == 200, resp2.text
        retry_after = await _get_item(retry.id)
        assert retry_after.state == QueueItemState.FAILED

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            stmt = select(QueueItem).where(QueueItem.retry_of_id == retry.id)
            grandchild = (await db.execute(stmt)).scalar_one_or_none()
        assert grandchild is None
        assert any(p.endswith("/release-for-service") for _, p in recorded_calls)


class TestClaim:
    async def test_wrong_identity_rejected(self, client, configure_internal_keys):
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("server_service", SERVER_SECRET))
        assert resp.status_code == 401, resp.text

    async def test_empty_queue_returns_null_item(self, client, configure_internal_keys):
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"] is None

    async def test_claims_ready_item_and_consumes_stash(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, server_id = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, with_sensitive_arg=True)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["item"]
        assert payload is not None
        assert payload["queue_item_id"] == item.id
        assert payload["host"] == "10.9.9.9"
        assert payload["test_username"] == "u"
        assert payload["test_password"] == "s3cr3t"
        assert payload["command"] == [
            "sudo", "bash", "/home/u/starter.sh", "", "git-token-value",
            f"dates_{item.id}.conf", "1.8.5", "",
        ]
        assert payload["command_masked"] == [
            "sudo", "bash", "/home/u/starter.sh", "", "***",
            f"dates_{item.id}.conf", "1.8.5", "",
        ]
        assert payload["dates_content"] == "--run s3cr3t"
        assert payload["dates_content_masked"] == "--run ***"
        assert payload["dates_filename"] == f"dates_{item.id}.conf"
        assert payload["command_timeout_seconds"] is None
        assert payload["debug_mode"] is False
        assert payload["is_retry"] is False

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.RUNNING

        # Очередь опустела для ready-строк — второй claim ничего не находит.
        resp2 = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp2.json()["item"] is None

    async def test_claim_passes_through_test_timeout_override(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.post(
            TESTS_BASE, headers=_hdr(admin_token),
            json={
                "code": f"queue.timeout.{uuid.uuid4().hex[:8]}", "full_name": "Тест с таймаутом",
                "readiness": "ready", "pinned_stand_id": stand_id, "timeout_seconds": 120,
            },
        )
        assert resp.status_code == 201, resp.text
        test_id = resp.json()["id"]

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["command_timeout_seconds"] == 120

    async def test_claim_computes_confluence_new_page(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        """`CONFLUENCE_NEW_PAGE` не приходит от вызывающего — claim_next
        считает её сам из full_name/RC/MODE/KERNEL/stand_id (backup_image.py:297)."""
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        var_resp = await client.get(f"{VARS_BASE}/by-code/CONFLUENCE_NEW_PAGE", headers=_hdr(admin_token))
        assert var_resp.status_code == 200, var_resp.text
        arg = await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": var_resp.json()["id"]},
        )
        assert arg.status_code == 201, arg.text

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["item"]
        assert payload["dates_content"] == f"--run Тест очереди_1.8.5_orel_6.1.0_{stand_id}"

    async def test_claim_passes_through_prepare_only_and_starter_suffix(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, starter_suffix="kernel")

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True,
            )
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["item"]
        assert payload["prepare_only"] is True
        assert payload["starter_suffix"] == "kernel"

    async def test_claim_defaults_starter_suffix_to_empty_string(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["item"]
        assert payload["prepare_only"] is False
        assert payload["starter_suffix"] == ""


class TestGitCredentialResolution:
    """Один секрет не может служить и заголовком `Authorization`, и паролем basic-auth.

    Легаси держало их врозь: заголовок — `tokens['git_token']`
    (`backup_image.py:270` → `starter.sh:56`), а Bitbucket REST в HR-отчёте —
    `auth=(tokens['username'], passwd)` (`monthly_report.py:23`, вообще другая
    пара). Поэтому у заголовка своя ссылка `git_credential_id`, а
    `bitbucket_credential_id` остался фолбэком для уже настроенных отделов.
    """

    async def _claim_with(self, client, admin_token, mock_server_service, mock_git_token, **fields):
        mock_server_service(host="10.9.9.9")
        await mock_git_token("dep_a", **fields)
        stand_id, _server_id = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        return item, resp.json()["item"]

    async def test_uses_dedicated_git_credential(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, monkeypatch,
    ):
        seen: list[str] = []

        async def fake_reveal(cred_id: str):
            seen.append(cred_id)
            return ("git-bot", "Bearer PAT123")

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)

        _item, payload = await self._claim_with(
            client, admin_token, mock_server_service, mock_git_token,
            git_credential_id="cred_git_header", bitbucket_credential_id="cred_bitbucket_basic",
        )
        # Заголовок берётся из своей записи, а не из bitbucket-пары.
        assert seen == ["cred_git_header"]
        assert payload["command"][4] == "Bearer PAT123"
        assert payload["command_masked"][4] == "***"

    async def test_falls_back_to_bitbucket_credential(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, monkeypatch,
    ):
        """Отдел, настроенный до разделения, продолжает работать как раньше."""
        seen: list[str] = []

        async def fake_reveal(cred_id: str):
            seen.append(cred_id)
            return ("git-bot", "legacy-token")

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)

        _item, payload = await self._claim_with(
            client, admin_token, mock_server_service, mock_git_token,
            git_credential_id=None, bitbucket_credential_id="cred_bitbucket_basic",
        )
        assert seen == ["cred_bitbucket_basic"]
        assert payload["command"][4] == "legacy-token"

    async def test_neither_configured_fails_item(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token("dep_a", git_credential_id=None, bitbucket_credential_id=None)
        stand_id, _server_id = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"] is None

        row = await _get_item(item.id)
        assert "git token resolution failed" in (row.error or "")
        assert "GIT_CREDENTIAL_NOT_CONFIGURED" not in (row.error or "")
        assert "git_credential_id" in (row.error or "")


class TestImportedCatalogNormalLaunch:
    """Обычный (не debug) запуск теста из настоящего каталога allta_app.

    Регрессия сразу на два P0: тест не был привязан ни к какому стенду
    (`TEST_NOT_PINNED_TO_STAND` на постановке) и не имел значений для пяти
    переменных команды (`LAUNCH_CONTEXT_VARIABLE_MISSING` на claim'е).
    Синтетический слот из соседних тестов ни того, ни другого не ловит.
    """

    async def test_reaches_worker_with_legacy_shaped_command(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        from pathlib import Path

        from scripts.import_catalog import run as import_run
        from src.repositories import test_definition as test_definition_repo
        from src.repositories import test_stand as stand_repo

        catalog = Path(__file__).resolve().parents[1] / "scripts" / "import_catalog.allta.yaml"

        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        server_id = f"srv_{uuid.uuid4().hex[:10]}"
        exit_code = await import_run(
            catalog, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
            override_stand_server_id=server_id, stand_legacy_token="stand3",
        )
        assert exit_code == 0

        ctx = {"RC": "1.8.5.46", "KERNEL": "6.1.0", "MODE": "orel"}
        async with AsyncSessionLocal() as db:
            stand = await stand_repo.get_by_server_id(db, server_id)
            test = await test_definition_repo.get_by_code(db, "file_systems.xfs")
            assert test.pinned_stand_id == stand.id
            # Стенд не передаётся — берётся из привязки, это и есть обычный запуск.
            item = await queue_svc.enqueue(db, _identity(), test.id, launch_context=ctx)

        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        dates = resp.json()["item"]["dates_content"]
        assert "-sn 3" in dates
        assert "-tcyc 1.8.5.46_orel_6.1.0_stand3" in dates
        assert '-tcas "file system benchmark. XFS"' not in dates  # кавычек резолвер не ставит
        assert "file system benchmark. XFS" in dates
        assert "STRESS_report 1.8.5.46 ⬝ Файловые системы" in dates
        assert "-fti none" in dates


class TestCompleted:
    async def test_wrong_identity_rejected(self, client, configure_internal_keys):
        resp = await client.post(
            f"{QUEUE_BASE}/qi_whatever/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"succeeded": True},
        )
        assert resp.status_code == 401, resp.text

    async def test_success_releases_reservation_when_queue_empty(
        self, client, admin_token, mock_server_service, configure_internal_keys, recorded_calls, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        recorded_calls.clear()

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text
        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.SUCCEEDED
        assert any(p.endswith("/release-for-service") for _, p in recorded_calls)

    async def test_prepare_only_success_becomes_prepared_not_succeeded(
        self, client, admin_token, mock_server_service, configure_internal_keys, recorded_calls, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True,
            )
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        recorded_calls.clear()

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text
        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.PREPARED
        # Стенд всё равно освобождается — исход теста не блокирует очередь.
        assert any(p.endswith("/release-for-service") for _, p in recorded_calls)

    async def test_completion_does_not_republish_stp_matrix(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        monkeypatch,
    ):
        """СТП-матрица публикуется только по кнопке — как и у легаси.

        У легаси единственный вызов `ZefirResultTable` из кода завершения теста
        (`backup_image.py:496-507` `jira_send_status`) закомментирован в обоих
        местах (`backup_image.py:1013,1022`), живыми оставались только ручные
        точки. Тест держит это поведение: `POST /queue/{id}/completed` не должен
        тянуть публикацию за собой.
        """
        from src.services import stp_matrix as stp_matrix_svc

        published: list[str] = []

        async def fake_publish(*args, **kwargs):
            published.append("called")

        monkeypatch.setattr(stp_matrix_svc, "publish_stp_matrix", fake_publish)

        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text
        assert published == []

    async def test_failure_creates_retry(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={
                "correlation_id": item.id, "succeeded": True,
                "test_username": "u", "test_password": "s3cr3t",
                "test_ssh_private_key": "-----KEY-----",
            },
        )
        await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": 1, "error": "ssh timeout"},
        )
        assert resp.status_code == 200, resp.text
        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.FAILED

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            stmt = select(QueueItem).where(QueueItem.retry_of_id == item.id)
            retry = (await db.execute(stmt)).scalar_one()
        assert retry.is_retry is True
        assert retry.state == QueueItemState.PREPARING
