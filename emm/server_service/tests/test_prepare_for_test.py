"""Тесты асинхронного контракта `prepare-for-test` (§5.1 плана ALLTA MIGRATION).

Покрывает три HTTP-точки:

* `POST /internal/servers/{id}/prepare-for-test` — 202 + идемпотентность по
  `correlation_id`, 409 занятый/decommissioned сервер, невалидное ядро —
  терминальный `failed` без единого похода к воркеру, identity-гейт
  (401/403/401 на s2s-канале testing_service);
* `GET .../prepare-for-test/{id}` — статус отражает состояние запроса,
  404 на чужой `server_id`;
* `POST .../prepare-for-test-done` (worker callback) — успех отдаёт креды
  исполнения теста и триггерит `testing_client`, провал пишет
  `failed_step`+`error`, non-fatal предупреждение на успехе (расхождение
  режима безопасности перед сменой) уезжает отдельным полем `warning`.

Identity-гейт входного s2s-канала — тот же shared-secret механизм, что у
`/internal/servers/{id}/acquire-for-service` (см.
`test_internal_service_reservation.py`); worker-callback permission-гейт
(`prepare_callback`) уже покрыт для соседних callback'ов в
`test_server_prepare.py::TestPreparedCallback` — здесь не дублируется.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.constants import BusyActorType, BusyState, ServerStatus
from src.models import Server
from src.models.server_prepare_for_test import (
    PREPARE_FOR_TEST_FAILED,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    STEP_USER_PROVISION,
    ServerPrepareForTestRequest,
    ServerTestCredentials,
)
from src.utils.ids import _new_id
from tests._helpers import assert_error, auth_hdr as _user_hdr

BASE = "/api/server/v1/internal/servers"
TESTING_SECRET = "test-testing-service-secret-do-not-use-in-prod"


@pytest.fixture
def configure_service_keys(monkeypatch):
    """Сконфигурировать shared-secret для identity `testing_service`.

    Тот же паттерн, что `test_internal_service_reservation.py` — `get_settings()`
    lru_cached'ится, чистим кэш до/после, чтобы значения не утекали между тестами.
    """
    from src.core.config import get_settings as _get_settings

    monkeypatch.setenv(
        "SERVER_INBOUND_SERVICE_API_KEYS", f"testing_service={TESTING_SECRET}",
    )
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    _get_settings.cache_clear()  # type: ignore[attr-defined]


def _svc_hdr(secret: str = TESTING_SECRET, identity: str = "testing_service") -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "X-Service-Identity": identity}


async def _make_os_version(db, *, name: str, kernels: list[str]):
    from src.models import OsVersion

    osv = OsVersion(id=_new_id("osv_"), name=name, repositories=[], kernels=kernels)
    db.add(osv)
    await db.flush()
    return osv


async def _set_bootstrap_password(db, os_version_id: str) -> None:
    from src.services import os_version_bootstrap_password as bootstrap_svc

    await bootstrap_svc.upsert_bootstrap_password(
        db, os_version_id, "root", "BootstrapPwd!123", None,
    )


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `worker_client.dispatch_task` — им пользуется `pft_svc.start()`
    для ACS-restore. Без перехвата тест реально писал бы Task/outbox row."""
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                             created_by, request_id, target_resource_id=None,
                             idempotency_key=None, priority=0, max_attempts=3):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        return f"tsk_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    return calls


def _body(**overrides) -> dict:
    payload = {
        "os_version_id": "osv_placeholder",
        "kernel": "5.10.0",
        "mode": "orel",
        "test_username": "u",
        "correlation_id": "qi_1",
    }
    payload.update(overrides)
    return payload


class TestStartAuth:
    async def test_missing_identity_401(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(f"{BASE}/{srv.id}/prepare-for-test", json=_body())
        assert_error(resp, 401, "SERVICE_IDENTITY_REQUIRED")

    async def test_unknown_identity_403(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(identity="rogue_service"),
            json=_body(),
        )
        assert_error(resp, 403, "SERVICE_IDENTITY_NOT_ALLOWED")

    async def test_bad_secret_401(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(secret="wrong-secret"),
            json=_body(),
        )
        assert_error(resp, 401, "INVALID_SERVICE_TOKEN")


class TestStartHappyPath:
    async def test_accepted_dispatches_restore_and_stores_mode(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-a", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["prepare_request_id"].startswith("prep_")

        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["task_kind"] == "acs.snapshot_restore"

        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == body["prepare_request_id"],
        ))).scalar_one()
        assert row.status == PREPARE_FOR_TEST_IN_PROGRESS
        assert row.mode == "orel"
        assert row.kernel == "5.10.0"

        srv_row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert srv_row.busy_state == BusyState.ACS
        assert srv_row.busy_actor_type == BusyActorType.SERVICE
        assert srv_row.busy_service_name == "testing_service"

    async def test_smolensk_mode_accepted(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-smol", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, mode="smolensk", correlation_id="qi_smol"),
        )
        assert resp.status_code == 202, resp.text
        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == resp.json()["prepare_request_id"],
        ))).scalar_one()
        assert row.mode == "smolensk"

    async def test_invalid_mode_rejected_by_schema(
        self, client, make_server, configure_service_keys,
    ):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(mode="voronezh"),
        )
        assert resp.status_code == 422, resp.text

    async def test_idempotent_repeat_returns_same_request(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-b", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)
        payload = _body(os_version_id=osv.id, correlation_id="qi_dup")

        resp1 = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(), json=payload,
        )
        resp2 = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(), json=payload,
        )
        assert resp1.status_code == 202
        assert resp2.status_code == 202
        assert resp1.json()["prepare_request_id"] == resp2.json()["prepare_request_id"]
        # второй вызов ничего не диспатчит — идемпотентность по correlation_id
        assert len(captured_dispatch) == 1


class TestStartConflicts:
    async def test_busy_server_409(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_someone"
        srv.busy_actor_type = BusyActorType.USER
        await db.flush()
        osv = await _make_os_version(db, name="1.8-busy", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id),
        )
        assert_error(resp, 409, "SERVER_ALREADY_BUSY")

    async def test_decommissioned_409(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(), json=_body(),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")

    async def test_already_running_pipeline_409(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-c", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        first = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_first"),
        )
        assert first.status_code == 202

        second = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_second"),
        )
        body = assert_error(second, 409, "PREPARE_FOR_TEST_ALREADY_RUNNING")
        assert body["details"]["prepare_request_id"] == first.json()["prepare_request_id"]

    async def test_invalid_kernel_fails_immediately_without_dispatch(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        """Ядра нет в `os_versions.kernels` — терминальный `failed`, ни одного
        похода к воркеру (ни ACS-restore, ни что-либо ещё)."""
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-d", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, kernel="9.9.9-not-listed"),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "failed"
        assert len(captured_dispatch) == 0

        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == resp.json()["prepare_request_id"],
        ))).scalar_one()
        assert row.status == PREPARE_FOR_TEST_FAILED
        assert row.failed_step == "kernel_change"

        srv_row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert srv_row.busy_state == BusyState.FREE  # бронь так и не бралась


class TestStatusEndpoint:
    async def test_status_reflects_in_progress_request(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-e", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        start_resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_status"),
        )
        prepare_request_id = start_resp.json()["prepare_request_id"]

        status_resp = await client.get(
            f"{BASE}/{srv.id}/prepare-for-test/{prepare_request_id}",
            headers=_svc_hdr(),
        )
        assert status_resp.status_code == 200, status_resp.text
        body = status_resp.json()
        assert body["status"] == "in_progress"
        assert body["mode"] == "orel"
        assert body["kernel"] == "5.10.0"
        assert body["server_id"] == srv.id
        assert body["callback_attempts"] >= 0

    async def test_wrong_server_id_404(
        self, client, make_server, db, configure_service_keys, captured_dispatch,
    ):
        srv = await make_server()
        other_srv = await make_server()
        osv = await _make_os_version(db, name="1.8-f", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        start_resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test",
            headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_wrong_srv"),
        )
        prepare_request_id = start_resp.json()["prepare_request_id"]

        resp = await client.get(
            f"{BASE}/{other_srv.id}/prepare-for-test/{prepare_request_id}",
            headers=_svc_hdr(),
        )
        assert_error(resp, 404, "PREPARE_REQUEST_NOT_FOUND")

    async def test_unknown_request_id_404(
        self, client, make_server, configure_service_keys,
    ):
        srv = await make_server()
        resp = await client.get(
            f"{BASE}/{srv.id}/prepare-for-test/prep_does_not_exist",
            headers=_svc_hdr(),
        )
        assert_error(resp, 404, "PREPARE_REQUEST_NOT_FOUND")


@pytest.mark.usefixtures("soft_dept_mode")
class TestWorkerCallbackDone:
    """`POST .../prepare-for-test-done` — worker сообщает исход новых шагов.

    Права воркера (`prepare_callback` грант, worker_bot против reader) уже
    проверены для соседних callback'ов в `test_server_prepare.py`, здесь не
    дублируется — фокус на бизнес-логике `record_prepare_for_test_done`.
    """

    async def _make_request_row(
        self, db, srv, *, correlation_id: str = "qi_cb_1", mode: str = "orel",
    ) -> ServerPrepareForTestRequest:
        from src.utils.ids import prepare_for_test_request_id

        req = ServerPrepareForTestRequest(
            id=prepare_for_test_request_id(),
            server_id=srv.id,
            correlation_id=correlation_id,
            os_version_id="osv_cb",
            kernel="5.10.0",
            mode=mode,
            test_username="u",
            requested_by_service="testing_service",
            status=PREPARE_FOR_TEST_IN_PROGRESS,
            stage=STEP_USER_PROVISION,
        )
        db.add(req)
        await db.flush()
        return req

    async def _make_creds_row(self, db, srv) -> ServerTestCredentials:
        from src.services import secrets_service
        from src.utils.ids import server_test_credentials_id

        creds_id = server_test_credentials_id()
        row = ServerTestCredentials(
            id=creds_id,
            server_id=srv.id,
            username="u",
            password_encrypted=secrets_service.encrypt(
                "plain-test-pass", aad=secrets_service.aad_for_server_test_password(creds_id),
            ),
            ssh_public_key="ssh-ed25519 AAAAtestpubkey",
            ssh_private_key_encrypted=secrets_service.encrypt(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----",
                aad=secrets_service.aad_for_server_test_ssh_key(creds_id),
            ),
        )
        db.add(row)
        await db.flush()
        return row

    def _stub_testing_client(self, monkeypatch, *, delivered=True):
        captured: list[tuple[str, dict]] = []

        async def fake_send(request_id, body):
            captured.append((request_id, body))
            return delivered, 1, (None if delivered else "HTTP 503")

        monkeypatch.setattr(
            "src.services.testing_client.send_prepare_for_test_completed", fake_send,
        )
        return captured

    async def test_success_returns_creds_and_calls_testing_client(
        self, client, make_server, db, dept_a, worker_bot_token_a, monkeypatch,
    ):
        srv = await make_server(department_id=dept_a)
        req = await self._make_request_row(db, srv)
        await self._make_creds_row(db, srv)
        captured = self._stub_testing_client(monkeypatch)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test-done",
            headers=_user_hdr(worker_bot_token_a),
            json={"prepare_request_id": req.id, "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["callback_delivered"] is True

        assert len(captured) == 1
        _, cb_body = captured[0]
        assert cb_body["succeeded"] is True
        assert cb_body["test_username"] == "u"
        assert cb_body["test_password"] == "plain-test-pass"
        assert cb_body["test_ssh_private_key"].startswith("-----BEGIN OPENSSH")
        assert "warning" not in cb_body

        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == req.id,
        ))).scalar_one()
        assert row.status == PREPARE_FOR_TEST_SUCCEEDED
        assert row.callback_delivered_at is not None

    async def test_success_with_mode_warning_forwarded(
        self, client, make_server, db, dept_a, worker_bot_token_a, monkeypatch,
    ):
        """Non-fatal предупреждение (расхождение режима до смены) не проваливает
        пайплайн, но уезжает testing_service отдельным полем `warning`."""
        srv = await make_server(department_id=dept_a)
        req = await self._make_request_row(db, srv, correlation_id="qi_cb_warn")
        await self._make_creds_row(db, srv)
        captured = self._stub_testing_client(monkeypatch)

        warning_text = (
            "astra security mode before prepare-for-test was 'voronezh', "
            "requested mode is 'orel'"
        )
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test-done",
            headers=_user_hdr(worker_bot_token_a),
            json={"prepare_request_id": req.id, "succeeded": True, "error": warning_text},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "succeeded"

        _, cb_body = captured[0]
        assert cb_body["succeeded"] is True
        assert cb_body["warning"] == warning_text

    async def test_failure_writes_failed_step_and_error(
        self, client, make_server, db, dept_a, worker_bot_token_a, monkeypatch,
    ):
        srv = await make_server(department_id=dept_a)
        req = await self._make_request_row(db, srv, correlation_id="qi_cb_fail")
        captured = self._stub_testing_client(monkeypatch, delivered=False)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test-done",
            headers=_user_hdr(worker_bot_token_a),
            json={
                "prepare_request_id": req.id,
                "succeeded": False,
                "failed_step": "kernel_change",
                "error": "no grub menuentry found for kernel 5.10.0",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["callback_delivered"] is False

        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == req.id,
        ))).scalar_one()
        assert row.status == PREPARE_FOR_TEST_FAILED
        assert row.failed_step == "kernel_change"
        assert row.error == "no grub menuentry found for kernel 5.10.0"

        _, cb_body = captured[0]
        assert cb_body["succeeded"] is False
        assert cb_body["failed_step"] == "kernel_change"
        assert cb_body["error"] == "no grub menuentry found for kernel 5.10.0"

    async def test_wrong_server_id_404(
        self, client, make_server, db, dept_a, worker_bot_token_a,
    ):
        srv = await make_server(department_id=dept_a)
        other_srv = await make_server(department_id=dept_a)
        req = await self._make_request_row(db, srv, correlation_id="qi_cb_wrong")

        resp = await client.post(
            f"{BASE}/{other_srv.id}/prepare-for-test-done",
            headers=_user_hdr(worker_bot_token_a),
            json={"prepare_request_id": req.id, "succeeded": True},
        )
        assert_error(resp, 404, "PREPARE_REQUEST_NOT_FOUND")

    async def test_unknown_request_id_404(
        self, client, make_server, dept_a, worker_bot_token_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test-done",
            headers=_user_hdr(worker_bot_token_a),
            json={"prepare_request_id": "prep_does_not_exist", "succeeded": True},
        )
        assert_error(resp, 404, "PREPARE_REQUEST_NOT_FOUND")


PUBLIC_BASE = "/api/server/v1/servers"


class TestTestCredentialsEndpoint:
    """`GET /servers/{id}/test-credentials` — живая отладка (§5.3 плана).

    Гейт — `(server, view_test_credentials)`, засеян только роли `admin`
    (миграция `c4e91a7f3d68`) — `reader`/`operator` не проходят вообще, не
    только на `reveal=true`, в отличие от `server_account.view_password`.
    """

    async def _make_creds_row(self, db, srv):
        from src.models.server_prepare_for_test import ServerTestCredentials
        from src.services import secrets_service
        from src.utils.ids import server_test_credentials_id

        creds_id = server_test_credentials_id()
        row = ServerTestCredentials(
            id=creds_id,
            server_id=srv.id,
            username="u",
            password_encrypted=secrets_service.encrypt(
                "plain-test-pass", aad=secrets_service.aad_for_server_test_password(creds_id),
            ),
            ssh_public_key="ssh-ed25519 AAAAtestpubkey",
            ssh_private_key_encrypted=secrets_service.encrypt(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----",
                aad=secrets_service.aad_for_server_test_ssh_key(creds_id),
            ),
        )
        db.add(row)
        await db.flush()
        return row

    async def test_no_credentials_yet_exists_false(
        self, client, make_server, dept_a, admin_token,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.get(
            f"{PUBLIC_BASE}/{srv.id}/test-credentials", headers=_user_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "exists": False, "username": None, "ssh_public_key": None,
            "rotated_at": None, "password_b64": None, "ssh_private_key_b64": None,
        }

    async def test_metadata_only_without_reveal(
        self, client, make_server, db, dept_a, admin_token,
    ):
        srv = await make_server(department_id=dept_a)
        await self._make_creds_row(db, srv)

        resp = await client.get(
            f"{PUBLIC_BASE}/{srv.id}/test-credentials", headers=_user_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["exists"] is True
        assert body["username"] == "u"
        assert body["ssh_public_key"] == "ssh-ed25519 AAAAtestpubkey"
        assert body["rotated_at"] is not None
        assert body["password_b64"] is None
        assert body["ssh_private_key_b64"] is None

    async def test_reveal_decrypts_secrets(
        self, client, make_server, db, dept_a, admin_token,
    ):
        import base64

        srv = await make_server(department_id=dept_a)
        await self._make_creds_row(db, srv)

        resp = await client.get(
            f"{PUBLIC_BASE}/{srv.id}/test-credentials?reveal=true",
            headers=_user_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert base64.b64decode(body["password_b64"]).decode() == "plain-test-pass"
        assert base64.b64decode(body["ssh_private_key_b64"]).decode().startswith(
            "-----BEGIN OPENSSH"
        )

    async def test_reader_denied_even_without_reveal(
        self, client, make_server, make_token, dept_a,
    ):
        """`view_test_credentials` — отдельный гейт, не подмножество `view`:
        держатель обычного `reader` не должен видеть даже метаданные."""
        srv = await make_server(department_id=dept_a)
        reader_token = make_token(
            platform_role="department_admin", department_id=dept_a,
            service_roles={"server_service": ["reader"]},
        )
        resp = await client.get(
            f"{PUBLIC_BASE}/{srv.id}/test-credentials", headers=_user_hdr(reader_token),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_department_404(
        self, client, make_server, admin_token, dept_b,
    ):
        srv = await make_server(department_id=dept_b)
        resp = await client.get(
            f"{PUBLIC_BASE}/{srv.id}/test-credentials", headers=_user_hdr(admin_token),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
