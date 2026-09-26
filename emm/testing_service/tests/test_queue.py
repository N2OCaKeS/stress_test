"""Тесты очереди (§2.4, §5, §5.5 плана миграции).

`enqueue()` вызывается напрямую из сервисного слоя (нет публичного HTTP-входа),
остальное — через internal-эндпоинты
(callback prepare-for-test, claim/completed для testing_worker).

`server_client`-вызовы в server_service мокаются тем же MockTransport-приёмом,
что и в `test_test_stands_crud.py`. Redis для Redis-стэша кред — настоящий
(поднят `tests/docker-compose.test.yml`), отдельно мокать незачем.
"""

from __future__ import annotations

import json
import shlex
import uuid
from types import SimpleNamespace

import httpx
import pytest

from src.core.constants import QueueItemState
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.dependencies.auth import Identity
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import department_test_settings as dts_repo
from src.repositories import queue_item as queue_repo
from src.repositories import test_stand as stand_repo
from src.services import queue as queue_svc
from src.services import secret_client, server_client
from src.utils.ids import department_integration_settings_id, department_test_settings_id, queue_item_id
from tests.conftest import auth_hdr as _hdr

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"
STANDS_BASE = "/api/testing/v1/test-stands"
CALLBACK_BASE = "/internal/prepare-for-test"
QUEUE_BASE = "/internal/queue"

SERVER_SECRET = "test-server-service-callback-secret"
WORKER_SECRET = "test-testing-worker-secret"


# `launch_context["RC"]` — id карточки версии ОС, как его кладут `test_run.py`/
# `public_queue.py`, а НЕ имя версии: иначе расхождение «id вместо имени» в
# команде (`-tcv`, `$4` у `starter.sh`) тестам не видно. Имя приходит
# из `server_client.resolve_os_version_info`, который `mock_server_service`
# подменяет этим каталогом.
OS_VERSION_ID = "osv_1f85a0c3"
OS_VERSION_NAME = "1.8.5"
OS_VERSIONS: dict[str, server_client.OsVersionInfo] = {
    OS_VERSION_ID: server_client.OsVersionInfo(name=OS_VERSION_NAME, is_urgent_update=False, rc_number="RC3"),
    "osv_5e46b2d1": server_client.OsVersionInfo(name="1.8.5.46", is_urgent_update=False, rc_number="RC5"),
    "osv_8a16c9e0": server_client.OsVersionInfo(name="1.8.1.6", is_urgent_update=False, rc_number="RC6"),
}


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
    "prepare": 202, "release": 200, "connection": 200, "batch_status": {...},
    "os_version_name": "1.8.5"|None, "acs_snapshots": [...]}`,
    по умолчанию всё успешно. `department_id`/`host` настраиваются отдельно.

    `batch_status` — состояние, которое видит `_ensure_stand_free_for_launch`
    (preflight-проверка занятости перед постановкой в очередь) через
    `POST /internal/servers/batch-status`. По умолчанию каждый запрошенный
    сервер отвечает `free`, чтобы существующие тесты, которые про эту
    проверку не знают, продолжали заводить первый item как раньше. Значение
    либо общее для всех id в запросе (`{"busy_state": "busy", ...}`), либо
    per-id (`{"srv_x": {"busy_state": "busy", ...}}`).

    `acquire-for-service` и `batch_status` — не два независимых рычага:
    реальный server_service делает CAS `WHERE busy_state='free'`, так что
    если preflight-снапшот (общая форма `batch_status`) видит стенд занятым
    и вызывающий явно не передал `overrides["acquire"]`, дефолтный ответ
    `/acquire-for-service` тоже 409 `SERVER_ALREADY_BUSY` — как у настоящего
    сервиса. Чтобы смоделировать узкое окно гонки (снапшот устарел, стенд
    освободился к моменту реального захвата), передайте `overrides["acquire"]`
    явно — это самый частый случай, который тестам и нужен.

    Контракт `takeover`: `acquire-for-service` с `takeover=true` на стенде,
    занятом как `busy`/`testing_done`, отвечает 200 и кладёт в ответ
    `previous_holder`; на `updating`/`acs` — 409, как у настоящего сервиса.
    Тела всех acquire-запросов складываются в `.acquire_bodies` возвращаемого
    хэндла `_install()`.
    """
    monkeypatch.setattr(server_client, "get_settings", lambda: _StubSettings())

    def _install(*, department_id="dep_a", host="10.0.0.5", overrides=None, acquire_fail_times=0):
        overrides = overrides or {}
        acquire_calls = {"count": 0}
        handle = SimpleNamespace(acquire_bodies=[])
        # ACS snapshot preflight (`queue._check_acs_snapshot_available`): по
        # умолчанию любая версия считается снятой — иначе пришлось бы чинить
        # каждый тест, который просто ставит что-то в очередь и не в курсе
        # ACS (в т.ч. те, что монкейтчат `server_client.get_os_version`
        # напрямую в обход этого HTTP-транспорта, см. `mock_os_version_
        # catalog` в `test_stp.py`, — раз резолв РЦ идёт мимо, синхронизировать
        # его с ответом `/acs-snapshots` через транспорт нечем). Патчим сам
        # `list_acs_snapshot_versions`, а не HTTP-ручку: `overrides["acs_
        # snapshots"]` (список снимков как их отдаёт server_service)
        # переопределяет дефолт для тестов самой ACS-проверки — тогда вызов
        # идёт в настоящий `list_acs_snapshot_versions` поверх транспорта ниже
        # (ручка `/acs-snapshots`, `overrides["acs_hostname"]` — hostname
        # стенда в ответе, по умолчанию `host`).
        class _AnyVersion(set):
            """`x in this` всегда `True` — «снимок для любой РЦ есть»."""

            def __contains__(self, item: object) -> bool:
                return True

        real_list_acs_snapshot_versions = server_client.list_acs_snapshot_versions

        async def _fake_list_acs_snapshot_versions(server_id: str, *, refresh: bool = False):
            if overrides.get("acs_snapshots") is not None:
                return await real_list_acs_snapshot_versions(server_id, refresh=True)
            return server_client.AcsSnapshotVersions(hostname=None, versions=_AnyVersion())

        monkeypatch.setattr(server_client, "list_acs_snapshot_versions", _fake_list_acs_snapshot_versions)

        # Карточка версии ОС: `overrides["os_version_name"]` подменяет имя
        # (`None` — версии нет в каталоге), иначе — каталог `OS_VERSIONS`.
        # Id не из каталога — настоящий `resolve_os_version_info` поверх
        # HTTP-ручки ниже (имя = id) или поверх `get_os_version`, если тест
        # подменил его сам (`mock_os_version_catalog` в `test_run_summary.py`):
        # тесты, которые кладут в `RC` готовое имя (`1.8.5.46`), работают как раньше.
        real_resolve_os_version_info = server_client.resolve_os_version_info

        async def _fake_resolve_os_version_info(os_version_id: str) -> server_client.OsVersionInfo:
            if "os_version_name" in overrides:
                name = overrides["os_version_name"]
                info = None if name is None else server_client.OsVersionInfo(
                    name=name, is_urgent_update=False, rc_number=None,
                )
            else:
                info = OS_VERSIONS.get(os_version_id)
                if info is None:
                    return await real_resolve_os_version_info(os_version_id)
            if info is None:
                raise NotFoundError(error_code="OS_VERSION_NOT_FOUND", message=f"no os_version {os_version_id}")
            return info

        monkeypatch.setattr(server_client, "resolve_os_version_info", _fake_resolve_os_version_info)

        def _preflight_busy_state() -> str | None:
            """Заявленное `batch_status` в его общей (не per-id) форме, если задано."""
            batch_override = overrides.get("batch_status")
            if isinstance(batch_override, dict) and "busy_state" in batch_override:
                return batch_override["busy_state"]
            return None

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_calls.append((request.method, request.url.path))
            path = request.url.path
            if request.method == "GET" and path.startswith("/api/server/v1/os-versions/"):
                version_id = path.rsplit("/", 1)[-1]
                name = overrides.get("os_version_name", version_id)
                if name is None:
                    return httpx.Response(404, json={})
                return httpx.Response(200, json={"id": version_id, "name": name})
            if request.method == "GET" and "/internal/" not in path and path.startswith("/api/server/v1/servers/"):
                server_id = path.rsplit("/", 1)[-1]
                return httpx.Response(200, json={
                    "id": server_id, "hostname": f"host-{server_id}", "department_id": department_id,
                })
            if request.method == "POST" and path.endswith("/acquire-for-service"):
                acquire_calls["count"] += 1
                acquire_body = json.loads(request.content or b"{}")
                handle.acquire_bodies.append(acquire_body)
                preflight_busy = _preflight_busy_state()
                previous_holder = None
                if "acquire" in overrides:
                    status = overrides["acquire"]
                elif preflight_busy in (None, "free"):
                    status = 200
                elif acquire_body.get("takeover") and preflight_busy in ("busy", "testing_done"):
                    # Без явного оверрайда акквайр согласован с preflight-снапшотом;
                    # takeover отбирает только `busy`/`testing_done`.
                    status = 200
                    previous_holder = {"busy_state": preflight_busy, **overrides["batch_status"]}
                else:
                    # Реально занятый стенд не может внезапно освободиться для CAS.
                    status = 409
                if status == 409 or acquire_calls["count"] <= acquire_fail_times:
                    return httpx.Response(409, json={
                        "error_code": "SERVER_ALREADY_BUSY", "message": "server already busy",
                    })
                return httpx.Response(status, json={
                    "server_id": "srv_x", "busy_state": "acs", "busy_actor_type": "service",
                    "busy_service_name": "testing_service", "busy_note": None, "busy_since": None,
                    "previous_holder": previous_holder,
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
            if request.method == "POST" and path.endswith("/release-for-service-as-done"):
                status = overrides.get("release", 200)
                return httpx.Response(status, json={
                    "server_id": "srv_x", "busy_state": "testing_done", "busy_actor_type": "service",
                    "busy_service_name": "testing_service", "busy_note": None, "busy_since": None,
                })
            if request.method == "POST" and path.endswith("/prepare-for-test"):
                status = overrides.get("prepare", 202)
                return httpx.Response(status, json={
                    "prepare_request_id": f"prep_{uuid.uuid4().hex[:10]}", "status": "in_progress",
                })
            if request.method == "GET" and path.endswith("/acs-snapshots"):
                return httpx.Response(200, json={
                    "hostname": overrides.get("acs_hostname", "host"),
                    "snapshots": overrides.get("acs_snapshots") or [],
                })
            if request.method == "GET" and path.endswith("/connection-info"):
                status = overrides.get("connection", 200)
                return httpx.Response(status, json={"server_id": "srv_x", "host": host})
            if request.method == "POST" and path.endswith("/batch-status"):
                requested = json.loads(request.content or b"{}").get("server_ids", [])
                batch_override = overrides.get("batch_status") or {}
                servers = []
                for server_id in requested:
                    entry = batch_override.get(server_id, batch_override) if isinstance(batch_override, dict) else {}
                    if not isinstance(entry, dict):
                        entry = {}
                    servers.append({
                        "server_id": server_id,
                        "found": True,
                        "busy_state": entry.get("busy_state", "free"),
                        "busy_service_name": entry.get("busy_service_name"),
                        "busy_actor_type": entry.get("busy_actor_type"),
                        "busy_user_id": entry.get("busy_user_id"),
                        "busy_note": entry.get("busy_note"),
                        "ping_reachable": None,
                        "ping_checked_at": None,
                    })
                return httpx.Response(200, json={"servers": servers})
            return httpx.Response(404, json={})

        def _build(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(server_client, "build_client", _build)
        return handle

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


def claim_file(payload: dict, name_prefix: str) -> dict:
    """Файл задания воркеру (C3) по началу имени файла."""
    return next(f for f in payload["files"] if f["path"].rsplit("/", 1)[1].startswith(name_prefix))


def claim_command(payload: dict) -> list[str]:
    """Команда запуска задания как argv."""
    return shlex.split(payload["launch_command"])


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
    timeout_seconds: int | None = None, department_id: str | None = None,
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
    if timeout_seconds is not None:
        payload["timeout_seconds"] = timeout_seconds
    if department_id is not None:
        payload["department_id"] = department_id
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


LAUNCH_CTX = {"RC": OS_VERSION_ID, "KERNEL": "6.1.0", "MODE": "orel"}


async def _seed_zephyr_folder(department_id: str, os_version_id: str, folder_tree_id: str) -> None:
    """Запись `zephyr_folders` — источник `FOLDER_TREE_ID` (`-fti`)."""
    from src.repositories import zephyr_folder as zephyr_folder_repo

    async with AsyncSessionLocal() as db:
        if await zephyr_folder_repo.get_by_department_and_os_version(db, department_id, os_version_id):
            return
        await zephyr_folder_repo.create(db, {
            "id": f"zfold_{uuid.uuid4().hex[:8]}", "department_id": department_id,
            "os_version_id": os_version_id, "folder_path": "/stress_test/test",
            "folder_tree_id": folder_tree_id,
        })
        await db.commit()


async def _get_item(item_id: str):
    async with AsyncSessionLocal() as db:
        return await queue_repo.get_by_id(db, item_id)


@pytest.fixture
def mock_git_token(monkeypatch):
    """Настраивает git-credential отдела + мокает `secret_client.reveal_credential`.

    `claim_next` собирает значение заголовка `Authorization` для `starter.sh` из
    `department_integration_settings.git_credential_id` (фолбэк —
    `bitbucket_credential_id`); без него item проваливается ещё до того, как
    воркер увидит команду (см. `services/queue.py::resolve_git_token`).
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
            second = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="append",
            )

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
                await queue_svc.enqueue(db, _identity(), test_id, launch_context={"RC": OS_VERSION_ID})

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

    async def test_cross_department_test_and_stand_rejected_inside_enqueue(
        self, client, admin_token, mock_server_service,
    ):
        """Инвариант «тест и стенд одного отдела» держится и внутри самого
        `enqueue()`, не только у вызывающих (`public_queue.launch/retry`) —
        так следующий новый launch-путь не сможет случайно его обойти."""
        mock_server_service(department_id="dep_b")
        stand_b_id, _ = await _create_stand(client, admin_token, department_id="dep_b")
        test_id = await _create_test_def(client, admin_token, stand_b_id, department_id="dep_a")

        async with AsyncSessionLocal() as db:
            with pytest.raises(AuthorizationError) as excinfo:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "PERMISSION_DENIED"

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
        assert not any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)

    async def test_reservation_released_when_prepare_fails_through_exhausted_retry(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        # Бронь взялась успешно (acquire прошёл), а вот сам prepare-for-test
        # падает — и у исходного item'а, и у его единственного retry. Раньше
        # это оставляло бронь висеть навсегда: _fail_and_advance получал
        # is_first_ever=True (значение, унаследованное с самого начала цикла)
        # и пропускал release, хотя acquire уже реально её взял.
        mock_server_service(overrides={"prepare": 500})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.FAILED
        assert any(p.endswith("/acquire-for-service") for _, p in recorded_calls)
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem
            retry = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == item.id)
            )).scalar_one()
        assert retry.state == QueueItemState.FAILED
        assert any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)


class TestAcsSnapshotPreflight:
    """Owner п.9: без снимка ACS restore не откатит стенд на выбранную РЦ —
    `enqueue()` обязан отказать синхронно, в любом режиме, до prepare-пайплайна.
    """

    async def test_missing_snapshot_rejects_enqueue(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        from src.core.exceptions import DomainValidationError

        mock_server_service(overrides={"acs_snapshots": []})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(DomainValidationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "ACS_SNAPSHOT_NOT_FOUND"
        assert excinfo.value.details["version_name"] == OS_VERSION_NAME
        # в ошибке — полное искомое имя снимка `{hostname}-{version}`.
        assert excinfo.value.details["snapshot_name"] == f"host-{OS_VERSION_NAME}"
        assert f"host-{OS_VERSION_NAME}" in excinfo.value.message
        # Отказ синхронный — до всякого похода в prepare-пайплайн.
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)
        assert not any(p.endswith("/prepare-for-test") for _, p in recorded_calls)

    async def test_missing_snapshot_rejects_debug_launch_too(
        self, client, admin_token, mock_server_service,
    ):
        """Owner: гейт общий для ВСЕХ режимов, не только обычного запуска."""
        from src.core.exceptions import DomainValidationError

        mock_server_service(overrides={"acs_snapshots": []})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)

        with pytest.raises(DomainValidationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(
                    db, _identity(), test_id, launch_context=LAUNCH_CTX,
                    debug_mode=True, stand_id=stand_id,
                )

        assert excinfo.value.error_code == "ACS_SNAPSHOT_NOT_FOUND"

    async def test_snapshot_present_allows_enqueue(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service(overrides={
            "acs_snapshots": [{"name": f"host-{OS_VERSION_NAME}", "version_name": OS_VERSION_NAME}],
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.PREPARING

    async def test_compact_snapshot_name_matches_normalized_version(
        self, client, admin_token, mock_server_service,
    ):
        """снимок `LowServer-1710rc52` — это версия каталога `1.7.10.52`
        (server_service отдаёт `normalized_version`), постановка проходит."""
        mock_server_service(overrides={
            "os_version_name": "1.7.10.52",
            "acs_hostname": "LowServer",
            "acs_snapshots": [{
                "name": "LowServer-1710rc52", "version_name": "1710rc52",
                "normalized_version": "1.7.10.52",
            }],
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.PREPARING

    async def test_other_version_snapshot_rejects_with_full_name(
        self, client, admin_token, mock_server_service,
    ):
        """Снимок есть, но другой РЦ — 422 с полным искомым именем снимка."""
        from src.core.exceptions import DomainValidationError

        mock_server_service(overrides={
            "os_version_name": "1.7.10.52",
            "acs_hostname": "LowServer",
            "acs_snapshots": [{
                "name": "LowServer-1710rc64", "version_name": "1710rc64",
                "normalized_version": "1.7.10.64",
            }],
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(DomainValidationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "ACS_SNAPSHOT_NOT_FOUND"
        assert excinfo.value.details["snapshot_name"] == "LowServer-1.7.10.52"
        assert "LowServer-1.7.10.52" in excinfo.value.message

    async def test_channel_unavailable_fails_open(
        self, client, admin_token, mock_server_service,
    ):
        """Если каталог версий/ACS недоступны — не блокируем постановку, тот же
        провал придёт асинхронно из restore-шага, как и раньше этой проверки."""
        mock_server_service(overrides={"os_version_name": None})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.PREPARING

    async def test_second_launch_of_same_stand_and_rc_is_cached(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Повторная постановка на тот же (стенд, РЦ) не бьёт по ACS снова."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        recorded_calls.clear()
        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="append",
            )

        assert second.state == QueueItemState.QUEUED
        assert not any(p.endswith("/acs-snapshots") for _, p in recorded_calls)
        assert not any(p.startswith("/api/server/v1/os-versions/") for _, p in recorded_calls)


class TestContinuationReacquireRace:
    """`_start_or_continue_cycle`, ветка `is_first_ever=False`: `set_service_status`
    отвечает `SERVER_NOT_BUSY` (бронь предыдущего item'а на самом деле уже не
    держится), а фолбэк `acquire_for_service` тоже проваливается. В этот момент
    бронь не наша вообще — `_fail_and_advance` не должен пытаться продвинуть/
    освободить её (чужую или несуществующую), как если бы она была нашей."""

    async def test_reacquire_failure_does_not_release_foreign_reservation(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service(overrides={
            "service_status": 409, "service_status_error": "SERVER_NOT_BUSY",
            "acquire": 409,
        })
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
            stand = await stand_repo.get_by_id(db, stand_id)
            item = await queue_repo.create(db, {
                "id": queue_item_id(),
                "stand_id": stand_id,
                "test_id": test_id,
                "launch_context": LAUNCH_CTX,
                "state": QueueItemState.QUEUED,
                "position": 0,
                "is_retry": False,
                "created_by": "usr_queue_test",
            })
            await db.commit()
            # Симулируем продолжение уже идущей очереди (is_first_ever=False)
            # без реального первого item'а — только это и важно для сценария.
            await queue_svc._start_or_continue_cycle(db, stand, item, is_first_ever=False)

        async with AsyncSessionLocal() as db:
            stored = await queue_repo.get_by_id(db, item.id)
        assert stored.state == QueueItemState.FAILED
        methods_paths = [p for _, p in recorded_calls]
        assert any(p.endswith("/service-status") for p in methods_paths)
        assert any(p.endswith("/acquire-for-service") for p in methods_paths)
        # Раньше здесь звался release-for-service-as-done — попытка отпустить
        # бронь, которую этот цикл ни разу не держал.
        assert not any(p.endswith("/release-for-service-as-done") for p in methods_paths)


class TestBusyPreflight:
    """`_ensure_stand_free_for_launch` — отказ на входе, до commit'а item'а (§1 плана 2026-09-18)."""

    async def test_busy_stand_rejected_without_force(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service(overrides={
            "batch_status": {"busy_state": "busy", "busy_service_name": "acs"},
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "STAND_BUSY"
        assert excinfo.value.details["busy_service_name"] == "acs"
        # Ничего не завелось — ни item'а в очереди, ни попытки взять бронь.
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)
        async with AsyncSessionLocal() as db:
            assert await queue_repo.count_active_for_stand(db, stand_id) == 0

    async def test_free_stand_is_not_blocked(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()  # default batch-status: free
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert item.state == QueueItemState.PREPARING
        assert any(p.endswith("/batch-status") for _, p in recorded_calls)

    async def test_second_item_on_active_queue_skips_the_check(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Очередь стенда уже активна — бронь наша, новый захват не идёт, проверять нечего."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        recorded_calls.clear()
        # Стенд стал бы "занят" по мнению server_service (наша же бронь), но
        # раз очередь уже не пуста — batch-status вообще не запрашивается.
        mock_server_service(overrides={"batch_status": {"busy_state": "acs"}})
        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="append",
            )

        assert second.state == QueueItemState.QUEUED
        assert recorded_calls == []

    async def test_force_allows_department_admin_when_stand_freed_up_by_acquire_time(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Preflight-снапшот занят, но к моменту настоящего захвата стенд уже
        освободился (узкое окно гонки) — единственный сценарий, где `force`
        реально доводит запуск до `PREPARING`. Оверрайд `acquire` здесь явный
        и намеренно расходится с `batch_status`, иначе мок отвечал бы 409 —
        см. `test_force_launch_fails_when_stand_still_busy_at_acquire` для
        случая, когда стенд остаётся занятым по-настоящему."""
        mock_server_service(overrides={
            "batch_status": {"busy_state": "busy"},
            "acquire": 200,
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True,
            )

        assert item.state == QueueItemState.PREPARING
        assert any(p.endswith("/acquire-for-service") for _, p in recorded_calls)

    async def test_force_launch_fails_when_stand_still_busy_at_acquire(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Takeover проиграл гонку: server_service отказал в захвате даже с
        `takeover=true` (стенд успел уйти в `updating`). `enqueue()` уже завёл
        item, тот сразу уходит в `FAILED` с понятной причиной, а не зависает и
        не даёт видимость успеха. Бронь при этом никогда не берётся: ни
        исходная попытка, ни авто-retry не проходят `acquire-for-service`,
        поэтому `release-for-service-as-done` не вызывается вовсе — двойной
        брони и утечки нет."""
        mock_server_service(overrides={"batch_status": {"busy_state": "busy"}, "acquire": 409})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True,
            )

        assert item.state == QueueItemState.FAILED
        assert item.error is not None and "SERVER_ALREADY_BUSY" in item.error
        assert not any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from src.models import QueueItem

            retry = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == item.id)
            )).scalar_one()
            assert retry.state == QueueItemState.FAILED
            assert retry.is_retry is True
            grandchild = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == retry.id)
            )).scalar_one_or_none()
            assert grandchild is None

    async def test_force_denied_for_non_admin_same_department(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Матрица прав уже не пускает не-admin'а до `POST /queue-items` (нужен `admin`
        на `test_run.create`), но `_ensure_stand_free_for_launch` не должна полагаться
        только на это — прямой вызов `enqueue()` с `force=True` от guest'а того же
        отдела тоже обязан получить отказ, а не тихо проскочить."""
        mock_server_service(overrides={"batch_status": {"busy_state": "busy"}})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        guest = Identity(
            user_id="usr_guest", username="guest", actor_type="user",
            department_id="dep_a", allowed_services=["testing_service"],
            service_roles={"testing_service": ["guest"]}, is_banned=False, platform_role=None,
        )

        with pytest.raises(AuthorizationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, guest, test_id, launch_context=LAUNCH_CTX, force=True)

        assert excinfo.value.error_code == "FORCE_LAUNCH_DENIED"
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)

    async def test_force_denied_across_departments(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """admin другого отдела не может форсировать запуск на чужом стенде —
        та же изоляция, что и у обычной постановки в очередь."""
        mock_server_service(overrides={"batch_status": {"busy_state": "busy"}})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        other_dept_admin = _identity(department_id="dep_b", user_id="usr_dep_b_admin")

        with pytest.raises(AuthorizationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(
                    db, other_dept_admin, test_id, launch_context=LAUNCH_CTX, force=True,
                )

        assert excinfo.value.error_code == "FORCE_LAUNCH_DENIED"
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)


def _guest_identity() -> Identity:
    return Identity(
        user_id="usr_guest", username="guest", actor_type="user",
        department_id="dep_a", allowed_services=["testing_service"],
        service_roles={"testing_service": ["guest"]}, is_banned=False, platform_role=None,
    )


class TestForceTakeover:
    """Force реально отнимает стенд: `busy`/`testing_done` — да, `updating`/чужой `acs` — нет."""

    @pytest.mark.parametrize("busy_state", ["busy", "testing_done"])
    async def test_force_takes_over_human_or_parked_stand(
        self, client, admin_token, mock_server_service, recorded_calls, busy_state,
    ):
        server = mock_server_service(overrides={
            "batch_status": {"busy_state": busy_state, "busy_user_id": "usr_petrov", "busy_note": "ручной прогон"},
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True,
            )

        assert item.state == QueueItemState.PREPARING
        assert server.acquire_bodies == [{
            "busy_state": "acs", "busy_note": "ACS|revert|1.8.5|6.1.0",
            "requested_by_department_id": "dep_a", "takeover": True,
        }]

    async def test_takeover_emits_audit_with_previous_holder(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        mock_server_service(overrides={
            "batch_status": {"busy_state": "busy", "busy_user_id": "usr_petrov"},
        })
        events: list[tuple[str, dict]] = []
        from src.services import audit_service
        monkeypatch.setattr(
            audit_service, "emit",
            lambda action, *args, **kwargs: events.append((action, kwargs.get("details") or {})),
        )
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True)

        takeovers = [details for action, details in events if action == "queue.force_takeover"]
        assert len(takeovers) == 1
        assert takeovers[0]["previous_holder"]["busy_user_id"] == "usr_petrov"
        assert takeovers[0]["stand_id"] == stand_id

    async def test_no_takeover_flag_without_force_on_free_stand(
        self, client, admin_token, mock_server_service,
    ):
        server = mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True)

        assert "takeover" not in server.acquire_bodies[0]

    async def test_busy_conflict_carries_holder_and_takeover_hint(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service(overrides={
            "batch_status": {
                "busy_state": "busy", "busy_actor_type": "user",
                "busy_user_id": "usr_petrov", "busy_note": "отладка",
            },
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        details = excinfo.value.details
        assert excinfo.value.error_code == "STAND_BUSY"
        assert details["busy_user_id"] == "usr_petrov"
        assert details["busy_note"] == "отладка"
        assert details["takeover_possible"] is True

    async def test_batch_status_rows_keep_holder_fields(self, mock_server_service):
        mock_server_service(overrides={
            "batch_status": {
                "busy_state": "busy", "busy_actor_type": "user",
                "busy_user_id": "usr_petrov", "busy_note": "отладка",
            },
        })
        rows = await server_client.get_servers_status_batch(["srv_a"])
        assert rows["srv_a"]["busy_user_id"] == "usr_petrov"
        assert rows["srv_a"]["busy_actor_type"] == "user"
        assert rows["srv_a"]["busy_note"] == "отладка"

    async def test_service_holder_details_reach_stand_busy(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service(overrides={
            "batch_status": {
                "busy_state": "testing_done", "busy_actor_type": "service",
                "busy_service_name": "testing_service", "busy_note": "ACS|revert",
            },
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        details = excinfo.value.details
        assert details["busy_actor_type"] == "service"
        assert details["busy_service_name"] == "testing_service"
        assert details["busy_user_id"] is None
        assert details["busy_note"] == "ACS|revert"

    @pytest.mark.parametrize("busy_state", ["updating", "acs", "testing"])
    async def test_force_does_not_take_over_updating_or_foreign_acs(
        self, client, admin_token, mock_server_service, recorded_calls, busy_state,
    ):
        mock_server_service(overrides={"batch_status": {"busy_state": busy_state}})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX, force=True)

        assert excinfo.value.error_code == "STAND_TAKEOVER_NOT_ALLOWED"
        assert excinfo.value.details["busy_state"] == busy_state
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)
        async with AsyncSessionLocal() as db:
            assert await queue_repo.count_active_for_stand(db, stand_id) == 0

    @pytest.mark.parametrize("busy_state", ["updating", "acs"])
    async def test_busy_conflict_marks_takeover_impossible(
        self, client, admin_token, mock_server_service, busy_state,
    ):
        mock_server_service(overrides={"batch_status": {"busy_state": busy_state}})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "STAND_BUSY"
        assert excinfo.value.details["takeover_possible"] is False

    async def test_replace_denied_for_non_admin(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        recorded_calls.clear()

        with pytest.raises(AuthorizationError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(
                    db, _guest_identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="replace",
                )

        assert excinfo.value.error_code == "FORCE_LAUNCH_DENIED"
        assert recorded_calls == []


class TestActiveQueueModes:
    """Очередь testing_service на стенде уже активна: не-админ — в конец, админ выбирает."""

    async def _stand_with_head(self, client, admin_token, mock_server_service):
        server = mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            head = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        return server, stand_id, test_id, head

    async def test_non_admin_is_appended_silently(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        _, _, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)
        recorded_calls.clear()

        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(db, _guest_identity(), test_id, launch_context=LAUNCH_CTX)

        assert second.state == QueueItemState.QUEUED
        assert second.position == head.position + 1
        assert recorded_calls == []

    async def test_admin_without_mode_gets_queue_active_conflict(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        _, stand_id, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)
        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _guest_identity(), test_id, launch_context=LAUNCH_CTX)
        recorded_calls.clear()

        with pytest.raises(ConflictError) as excinfo:
            async with AsyncSessionLocal() as db:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        assert excinfo.value.error_code == "STAND_QUEUE_ACTIVE"
        details = excinfo.value.details
        assert details["queued_count"] == 1
        assert details["current"]["queue_item_id"] == head.id
        assert details["current"]["state"] == QueueItemState.PREPARING
        assert details["current"]["test_id"] == test_id
        assert recorded_calls == []
        async with AsyncSessionLocal() as db:
            assert await queue_repo.count_active_for_stand(db, stand_id) == 2

    async def test_admin_append_goes_to_the_end(
        self, client, admin_token, mock_server_service,
    ):
        _, _, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)

        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="append",
            )

        assert second.state == QueueItemState.QUEUED
        assert second.position == head.position + 1
        assert (await _get_item(head.id)).state == QueueItemState.PREPARING

    async def test_admin_replace_clears_queue_skips_current_and_starts_new(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        _, stand_id, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)
        async with AsyncSessionLocal() as db:
            old_queued = await queue_svc.enqueue(db, _guest_identity(), test_id, launch_context=LAUNCH_CTX)
        recorded_calls.clear()

        async with AsyncSessionLocal() as db:
            new = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="replace",
            )

        assert new.state == QueueItemState.PREPARING
        assert (await _get_item(head.id)).state == QueueItemState.SKIPPED
        assert await _get_item(old_queued.id) is None
        paths = [p for _, p in recorded_calls]
        # Стенд не уходит в testing_done между «прервали» и «начали»: цикл
        # переключается на новый item через service-status.
        assert not any(p.endswith("/release-for-service-as-done") for p in paths)
        assert any(p.endswith("/prepare-for-test") for p in paths)
        async with AsyncSessionLocal() as db:
            assert await queue_repo.count_active_for_stand(db, stand_id) == 1

    async def test_replace_emits_audit(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        _, stand_id, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)
        events: list[tuple[str, dict]] = []
        from src.services import audit_service
        monkeypatch.setattr(
            audit_service, "emit",
            lambda action, *args, **kwargs: events.append((action, kwargs.get("details") or {})),
        )

        async with AsyncSessionLocal() as db:
            new = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="replace",
            )

        replaced = [details for action, details in events if action == "queue.replace"]
        assert len(replaced) == 1
        assert replaced[0]["new_queue_item_id"] == new.id
        assert replaced[0]["interrupted"] == [{"queue_item_id": head.id, "state": QueueItemState.PREPARING}]

    def _queue_emptied_before_lock(self, monkeypatch):
        """Пред-проверка видит активную очередь, а к локу она уже пуста."""
        real_count = queue_repo.count_active_for_stand
        calls = {"n": 0}

        async def flaky(db, stand_id):
            calls["n"] += 1
            return 1 if calls["n"] == 1 else await real_count(db, stand_id)

        monkeypatch.setattr(queue_repo, "count_active_for_stand", flaky)

    async def test_replace_race_queue_emptied_takes_over_testing_done(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        server = mock_server_service(overrides={
            "batch_status": {
                "busy_state": "testing_done", "busy_actor_type": "service",
                "busy_service_name": "testing_service",
            },
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        self._queue_emptied_before_lock(monkeypatch)

        async with AsyncSessionLocal() as db:
            new = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="replace",
            )

        assert new.state == QueueItemState.PREPARING
        assert [body.get("takeover") for body in server.acquire_bodies] == [True]

    async def test_append_race_queue_emptied_has_no_takeover(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        server = mock_server_service(overrides={
            "batch_status": {"busy_state": "testing_done", "busy_service_name": "testing_service"},
        })
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        self._queue_emptied_before_lock(monkeypatch)

        async with AsyncSessionLocal() as db:
            new = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="append",
            )

        assert server.acquire_bodies and all("takeover" not in body for body in server.acquire_bodies)
        assert new.state == QueueItemState.FAILED

    async def test_replace_race_on_free_stand_has_no_takeover_audit(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        server = mock_server_service()
        events: list[str] = []
        from src.services import audit_service
        monkeypatch.setattr(
            audit_service, "emit", lambda action, *args, **kwargs: events.append(action),
        )
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        self._queue_emptied_before_lock(monkeypatch)

        async with AsyncSessionLocal() as db:
            new = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, on_active_queue="replace",
            )

        assert new.state == QueueItemState.PREPARING
        assert [body.get("takeover") for body in server.acquire_bodies] == [True]
        assert "queue.force_takeover" not in events

    async def test_retry_failed_appends_for_admin_on_active_queue(
        self, client, admin_token, mock_server_service,
    ):
        """Массовый retry у админа не спрашивает про очередь — встаёт в конец."""
        _, stand_id, test_id, head = await self._stand_with_head(client, admin_token, mock_server_service)
        async with AsyncSessionLocal() as db:
            failed = await queue_repo.create(db, {
                "id": queue_item_id(), "stand_id": stand_id, "test_id": test_id,
                "launch_context": LAUNCH_CTX, "state": QueueItemState.FAILED, "position": 5,
                "is_retry": False, "debug_mode": True, "created_by": "usr_queue_test",
            })
            await db.commit()
        async with AsyncSessionLocal() as db:
            stand = await stand_repo.get_by_id(db, stand_id)
            retried, skipped = await queue_svc.retry_failed(db, _identity(), stand)

        assert len(retried) == 1 and skipped == 0
        assert retried[0].state == QueueItemState.QUEUED
        assert retried[0].retry_of_id == failed.id


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
        assert any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)


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
        assert "test_password" not in payload
        git_token_filename = f"git_token_{item.id}.conf"
        # `$4` — имя версии ОС (`RC_NAME`), а не id карточки из `launch_context["RC"]`.
        assert claim_command(payload) == [
            "sudo", "bash", "/home/u/starter.sh", "", git_token_filename,
            f"dates_{item.id}.conf", OS_VERSION_NAME, "",
        ]
        assert OS_VERSION_ID not in payload["launch_command"]
        # Секрета в argv больше нет — маскированная команда с ней совпадает.
        assert payload["launch_command_masked"] == payload["launch_command"]
        token_file = claim_file(payload, "git_token_")
        assert token_file["path"] == f"/home/u/{git_token_filename}"
        assert token_file["content"] == "git-token-value"
        assert token_file["sensitive"] is True and token_file["mode"] == "0600"
        dates_file = claim_file(payload, "dates_")
        assert dates_file["path"] == f"/home/u/dates_{item.id}.conf"
        assert dates_file["content"] == "--run s3cr3t"
        assert dates_file["sensitive"] is True
        # Секреты вырезаются воркером из error и живого лога.
        assert "s3cr3t" in payload["redact_values"]
        assert "git-token-value" in payload["redact_values"]
        # Файлы уходят в порядке: скрипт, токен, dates, testenv-маркер `off`.
        assert [f["path"].rsplit("/", 1)[1] for f in payload["files"]] == [
            "starter.sh", git_token_filename, f"dates_{item.id}.conf", "testenv_marker.conf",
        ]
        assert claim_file(payload, "testenv_")["content"] == "off"
        assert payload["use_pty"] is True
        assert "[/]home/u/starter\\.sh" in payload["stop_command"]
        assert payload["launch_profile_version_id"] == "lpv_default_2"
        assert (await _get_item(item.id)).launch_profile_version_id == "lpv_default_2"
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
        # Значение с пробелом приходит экранированным: `run.py` подставляет
        # dates.conf через shell=True (D4, dates_quoting=shell по умолчанию).
        assert claim_file(payload, "dates_")["content"] == f"--run 'Тест очереди_1.8.5_orel_6.1.0_{stand_id}'"

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
        assert claim_command(payload)[-1] == "kernel"
        # T2: одиночный запуск с testenv — маркер `on` и файл с командой.
        assert claim_file(payload, "testenv_")["content"] == "on"
        assert claim_file(payload, "command.txt")["content"] == payload["launch_command_masked"] + "\n"

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
        assert claim_command(payload)[-1] == ""
        assert claim_file(payload, "testenv_")["content"] == "off"
        assert not any(f["path"].endswith("command.txt") for f in payload["files"])

    async def _enqueue_ready(self, client, admin_token, test_id):
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
        return item

    async def test_claim_carries_department_preflight_and_picks_up_changes(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        """preflight из настроек отдела стенда, правка действует на следующий claim."""
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        await self._enqueue_ready(client, admin_token, test_id)
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        first = resp.json()["item"]["preflight"]
        # Строки настроек нет — легаси-дефолты: строго 200, 180 с, 120 минут.
        assert first["enabled"] is True
        assert [probe["url"] for probe in first["http"]] == [
            "https://jira.astralinux.ru", "https://life.astralinux.ru",
            "https://git.astralinux.ru", "https://releases.devos.astralinux.ru",
        ]
        assert {probe["ok_status"] for probe in first["http"]} == {"200"}
        assert first["poll_interval_seconds"] == 180
        assert first["timeout_seconds"] == 7200

        # Первый item ещё идёт — завершаем его, чтобы стенд выдал следующий.
        done = await client.post(
            f"{QUEUE_BASE}/{resp.json()['item']['queue_item_id']}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert done.status_code == 200, done.text

        changed = {
            "enabled": True,
            "http": [{"url": "https://mirror.example.test", "ok_status": "lt500"}],
            "dns_hosts": [], "dns_port": 53,
            "poll_interval_seconds": 15, "timeout_seconds": 60,
        }
        put = await client.put(
            f"/api/testing/v1/department-test-settings/dep_a", headers=_hdr(admin_token),
            json={"preflight": changed},
        )
        assert put.status_code == 200, put.text

        await self._enqueue_ready(client, admin_token, test_id)
        resp2 = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["item"] is not None
        assert resp2.json()["item"]["preflight"] == {**changed, "probe_timeout_seconds": 15}


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
        # В argv токена больше нет — только имя файла, куда его положат по SFTP.
        token_file = claim_file(payload, "git_token_")
        assert claim_command(payload)[4] == token_file["path"].rsplit("/", 1)[1]
        assert token_file["content"] == "Bearer PAT123"
        assert "Bearer PAT123" not in payload["launch_command_masked"]

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
        token_file = claim_file(payload, "git_token_")
        assert claim_command(payload)[4] == token_file["path"].rsplit("/", 1)[1]
        assert token_file["content"] == "legacy-token"

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
        monkeypatch,
    ):
        from pathlib import Path

        from scripts.import_catalog import run as import_run
        from src.repositories import test_definition as test_definition_repo
        from src.repositories import test_stand as stand_repo

        catalog = Path(__file__).resolve().parents[1] / "scripts" / "import_catalog.allta.yaml"

        mock_server_service(host="10.9.9.9")
        # Учётные данные конечного скрипта — из интеграций отдела стенда (D2).
        await mock_git_token(
            git_credential_id="cred_git_header", credential_id="cred_jira",
            confluence_credential_id="cred_conf", stp_matrix_confluence_space="DEVQA",
        )
        creds = {
            "cred_git_header": ("git-bot", "git-token-value"),
            "cred_jira": ("jira-bot", "jira basic auth"),
            "cred_conf": ("conf-bot", "conf-token"),
        }

        async def fake_reveal(cred_id: str):
            return creds[cred_id]

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        server_id = f"srv_{uuid.uuid4().hex[:10]}"
        exit_code = await import_run(
            catalog, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
            override_stand_server_id=server_id, stand_legacy_token="stand3",
        )
        assert exit_code == 0

        # `-fti` — из папки Zephyr отдела стенда и версии ОС.
        await _seed_zephyr_folder("dep_a", "osv_5e46b2d1", "4242")
        ctx = {"RC": "osv_5e46b2d1", "KERNEL": "6.1.0", "MODE": "orel"}
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
        dates = claim_file(resp.json()["item"], "dates_")["content"]
        assert "-sn 3" in dates
        assert "-tcyc 1.8.5.46_orel_6.1.0_stand3" in dates
        # Значения с пробелами экранированы (D4): run.py читает dates через shell.
        assert "-tcas 'file system benchmark. XFS'" in dates
        assert "--confluence-parent-page 'STRESS_report 1.8.5.46 ⬝ Файловые системы'" in dates
        # Короткое имя из легаси-словаря tests (D6), а не полное.
        assert "--confluence-new-page XFS_1.8.5.46_orel_6.1.0_stand3" in dates
        assert "-fti 4242" in dates
        # Учётные данные отдела вместо плейсхолдеров `none` (D2).
        assert "none" not in dates.split()
        assert "--username conf-bot --token conf-token --confluence-space DEVQA" in dates
        assert "-ba 'jira basic auth'" in dates
        # Секреты dates уходят воркеру в redact_values (живой лог, error).
        redact = resp.json()["item"]["redact_values"]
        assert "conf-token" in redact and "jira basic auth" in redact
        assert claim_file(resp.json()["item"], "dates_")["sensitive"] is True
        assert "-tcv 1.8.5.46" in dates
        assert "osv_5e46b2d1" not in dates

    async def test_rc_name_reaches_tcv_vbox_and_starter_arg(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        monkeypatch,
    ):
        """`-tcv`/`-vbox` и `$4` у `starter.sh` — имя версии ОС, а не её id.

        `launch_context["RC"]` = `osv_<hex>` (так его кладут `test_run.py`/
        `public_queue.py`); `prepare.sh $2` ищет РЦ в `releases.json` по имени
        (`1.8.1.6`), а `kernel/test_run.py` выбирает бокс ВМ по `-vbox`.
        """
        from pathlib import Path

        from scripts.import_catalog import run as import_run
        from src.repositories import test_definition as test_definition_repo
        from src.repositories import test_stand as stand_repo

        catalog = Path(__file__).resolve().parents[1] / "scripts" / "import_catalog.allta.yaml"

        mock_server_service(host="10.9.9.9")
        await mock_git_token(
            git_credential_id="cred_git_header", credential_id="cred_jira",
            confluence_credential_id="cred_conf", stp_matrix_confluence_space="DEVQA",
        )
        creds = {
            "cred_git_header": ("git-bot", "git-token-value"),
            "cred_jira": ("jira-bot", "jira basic auth"),
            "cred_conf": ("conf-bot", "conf-token"),
        }

        async def fake_reveal(cred_id: str):
            return creds[cred_id]

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        server_id = f"srv_{uuid.uuid4().hex[:10]}"
        exit_code = await import_run(
            catalog, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
            override_stand_server_id=server_id, stand_legacy_token="stand3",
        )
        assert exit_code == 0

        await _seed_zephyr_folder("dep_a", "osv_8a16c9e0", "4242")
        ctx = {"RC": "osv_8a16c9e0", "KERNEL": "6.1.0", "MODE": "orel"}
        async with AsyncSessionLocal() as db:
            stand = await stand_repo.get_by_server_id(db, server_id)
            test = await test_definition_repo.get_by_code(db, "kernel.segfault")
            # draft-тест с легаси-привязкой к stand12 — запускаем debug'ом на stand3.
            item = await queue_svc.enqueue(
                db, _identity(), test.id, launch_context=ctx, debug_mode=True, stand_id=stand.id,
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
        assert payload is not None, (await _get_item(item.id)).error
        dates = claim_file(payload, "dates_")["content"]
        assert "-tcv 1.8.1.6 -vbox 1.8.1.6 -testname segfault" in dates
        assert "-tcyc 1.8.1.6_orel_6.1.0_stand3" in dates
        assert "osv_8a16c9e0" not in dates
        assert claim_command(payload) == [
            "sudo", "bash", "/home/u/starter.sh", "kernel", f"git_token_{item.id}.conf",
            f"dates_{item.id}.conf", "1.8.1.6", "",
        ]


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
        # Очередь опустела — стенд паркуется в testing_done, а не сразу в
        # free: голый /release-for-service тут вызывать не должны.
        assert any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)
        assert not any(
            p.endswith("/release-for-service") and not p.endswith("/release-for-service-as-done")
            for _, p in recorded_calls
        )

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
        assert any(p.endswith("/release-for-service-as-done") for _, p in recorded_calls)

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
